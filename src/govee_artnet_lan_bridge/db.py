"""SQLite helpers and migrations for the Govee Artnet LAN bridge."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Optional, Tuple, TypeVar

from .logging import get_logger

Migration = Callable[[sqlite3.Connection], None]

SCHEMA_VERSION_KEY = "schema_version"
DEFAULT_INTEGRITY_CHECK_INTERVAL = 6 * 60 * 60  # seconds
BUSY_TIMEOUT_MS = 5000

T = TypeVar("T")


class DatabaseCorruptionError(RuntimeError):
    """Raised when a fatal SQLite corruption is detected."""


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Apply connection-wide pragmas suitable for concurrent writers."""

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _get_schema_version(conn: sqlite3.Connection) -> int:
    _ensure_meta_table(conn)
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (SCHEMA_VERSION_KEY,)
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        """
        INSERT INTO meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (SCHEMA_VERSION_KEY, str(version)),
    )


def _migration_initial_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            description TEXT,
            discovered INTEGER NOT NULL DEFAULT 0,
            configured INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            capabilities TEXT,
            last_seen TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TRIGGER IF NOT EXISTS trg_devices_updated_at
        AFTER UPDATE ON devices
        WHEN old.updated_at = new.updated_at
        BEGIN
            UPDATE devices SET updated_at = datetime('now') WHERE id = NEW.id;
        END;

        CREATE TABLE IF NOT EXISTS mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            universe INTEGER NOT NULL,
            channel INTEGER NOT NULL,
            length INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
        );

        CREATE TRIGGER IF NOT EXISTS trg_mappings_updated_at
        AFTER UPDATE ON mappings
        WHEN old.updated_at = new.updated_at
        BEGIN
            UPDATE mappings SET updated_at = datetime('now') WHERE id = NEW.id;
        END;

        CREATE TABLE IF NOT EXISTS state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            context_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_mappings_universe_channel
            ON mappings (universe, channel);

        CREATE INDEX IF NOT EXISTS idx_devices_last_seen
            ON devices (last_seen);
        """
    )


def _migration_device_metadata(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        ALTER TABLE devices ADD COLUMN ip TEXT;
        ALTER TABLE devices ADD COLUMN model TEXT;
        ALTER TABLE devices ADD COLUMN first_seen TEXT;
        ALTER TABLE devices ADD COLUMN manual INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE devices ADD COLUMN stale INTEGER NOT NULL DEFAULT 0;

        CREATE INDEX IF NOT EXISTS idx_devices_manual ON devices (manual);
        CREATE INDEX IF NOT EXISTS idx_devices_stale ON devices (stale);
        """
    )
    conn.execute(
        """
        UPDATE devices
        SET first_seen = COALESCE(last_seen, created_at)
        WHERE first_seen IS NULL
        """
    )


def _migration_device_send_tracking(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        ALTER TABLE devices ADD COLUMN last_payload_hash TEXT;
        ALTER TABLE devices ADD COLUMN last_payload_at TEXT;
        ALTER TABLE devices ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE devices ADD COLUMN offline INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE devices ADD COLUMN last_failure_at TEXT;

        CREATE INDEX IF NOT EXISTS idx_devices_offline ON devices (offline);
        """
    )


def _migration_state_context_id(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(state)").fetchall()
        if "name" in row.keys()
    }
    if "context_id" in columns:
        return
    conn.executescript(
        """
        ALTER TABLE state ADD COLUMN context_id TEXT;
        """
    )


def _migration_dead_letter_state(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dead_letters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            state_id INTEGER,
            device_id TEXT,
            payload TEXT NOT NULL,
            payload_hash TEXT,
            context_id TEXT,
            reason TEXT,
            details TEXT,
            state_created_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_dead_letters_device_id
            ON dead_letters (device_id);

        CREATE INDEX IF NOT EXISTS idx_dead_letters_created_at
            ON dead_letters (created_at);
        """
    )


def _migration_discrete_mappings(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        ALTER TABLE mappings ADD COLUMN mapping_type TEXT NOT NULL DEFAULT 'range';
        ALTER TABLE mappings ADD COLUMN field TEXT;
        """
    )


def _migration_mapping_fields(conn: sqlite3.Connection) -> None:
    def _deserialize_capabilities(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    def _coerce_mode(capabilities: Any, length: int) -> str:
        default_mode = "rgbw" if length >= 4 else "rgb" if length >= 3 else "brightness"
        if isinstance(capabilities, Mapping):
            mode = str(capabilities.get("mode", default_mode)).lower()
            if mode in {"rgb", "rgbw", "brightness", "custom"}:
                return mode
        return default_mode

    def _coerce_order(capabilities: Any, mode: str) -> Tuple[str, ...]:
        def _normalize_entry(entry: str) -> Optional[str]:
            value = entry.strip().lower()
            if value in {"r", "g", "b", "w", "brightness"}:
                return value
            return None

        default_orders = {
            "rgb": ("r", "g", "b"),
            "rgbw": ("r", "g", "b", "w"),
            "brightness": ("brightness",),
        }
        if isinstance(capabilities, Mapping):
            order_value = capabilities.get("order") or capabilities.get("channel_order")
            if isinstance(order_value, str):
                parsed = tuple(
                    entry for entry in (_normalize_entry(ch) for ch in order_value) if entry
                )
                if parsed:
                    return parsed
            if isinstance(order_value, Iterable) and not isinstance(order_value, (str, bytes)):
                parsed_list = []
                for item in order_value:
                    if not isinstance(item, str):
                        continue
                    normalized = _normalize_entry(item)
                    if normalized:
                        parsed_list.append(normalized)
                if parsed_list:
                    return tuple(parsed_list)
        return default_orders.get(mode, default_orders["brightness"])

    conn.executescript(
        """
        ALTER TABLE mappings ADD COLUMN fields TEXT;
        """
    )

    rows = conn.execute(
        """
        SELECT
            m.id,
            m.mapping_type,
            m.length,
            m.field,
            d.capabilities
        FROM mappings m
        LEFT JOIN devices d ON d.id = m.device_id
        """
    ).fetchall()

    for row in rows:
        mapping_type = str(row["mapping_type"] or "range").strip().lower()
        length = int(row["length"] or 0)
        fields: Tuple[str, ...] = tuple()
        if mapping_type == "discrete" and row["field"]:
            fields = (str(row["field"]).strip().lower(),)
        else:
            capabilities = _deserialize_capabilities(row["capabilities"])
            mode = _coerce_mode(capabilities, length)
            fields = _coerce_order(capabilities, mode)
        if fields:
            conn.execute(
                "UPDATE mappings SET fields = ? WHERE id = ?",
                (json.dumps(list(fields)), row["id"]),
            )
    conn.commit()


def _migration_device_catalog_metadata(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        ALTER TABLE devices ADD COLUMN model_number TEXT;
        ALTER TABLE devices ADD COLUMN device_type TEXT;
        ALTER TABLE devices ADD COLUMN length_meters REAL;
        ALTER TABLE devices ADD COLUMN led_count INTEGER;
        ALTER TABLE devices ADD COLUMN led_density_per_meter REAL;
        ALTER TABLE devices ADD COLUMN has_segments INTEGER;
        ALTER TABLE devices ADD COLUMN segment_count INTEGER;

        UPDATE devices
        SET model_number = COALESCE(model_number, model);
        """
    )


MIGRATIONS: List[Tuple[int, Migration]] = [
    (1, _migration_initial_schema),
    (2, _migration_device_metadata),
    (3, _migration_device_send_tracking),
    (4, _migration_state_context_id),
    (5, _migration_dead_letter_state),
    (6, _migration_discrete_mappings),
    (7, _migration_mapping_fields),
    (
        8,
        lambda conn: conn.executescript(
            """
            ALTER TABLE devices ADD COLUMN poll_failure_count INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE devices ADD COLUMN poll_last_success_at TEXT;
            ALTER TABLE devices ADD COLUMN poll_last_failure_at TEXT;
            ALTER TABLE devices ADD COLUMN poll_state TEXT;
            ALTER TABLE devices ADD COLUMN poll_state_updated_at TEXT;
            """
        ),
    ),
    (9, _migration_device_catalog_metadata),
    (
        10,
        lambda conn: conn.executescript(
            """
            ALTER TABLE devices ADD COLUMN name TEXT;
            """
        ),
    ),
]


def apply_migrations(db_path: Path) -> None:
    """Apply any pending migrations to the SQLite database."""

    logger = get_logger("govee.migrations")
    _ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    _configure_connection(conn)
    try:
        current = _get_schema_version(conn)
        logger.info("Current schema version", extra={"version": current})

        for version, migration in _pending_migrations(current):
            logger.info("Applying migration", extra={"version": version})
            migration(conn)
            _set_schema_version(conn, version)
            conn.commit()
            logger.info("Migration applied", extra={"version": version})
    finally:
        conn.close()


def _pending_migrations(current_version: int) -> Iterable[Tuple[int, Migration]]:
    for version, migration in MIGRATIONS:
        if version > current_version:
            yield version, migration


class DatabaseManager:
    """Serializes access to a shared SQLite connection with health checks."""

    def __init__(
        self,
        db_path: Path,
        *,
        integrity_check_interval: float = DEFAULT_INTEGRITY_CHECK_INTERVAL,
    ) -> None:
        self.db_path = db_path
        self.logger = get_logger("govee.db")
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()
        self._integrity_task: Optional[asyncio.Task[None]] = None
        self._integrity_interval = integrity_check_interval
        self._closed = False

    async def start_integrity_checks(self) -> None:
        if self._integrity_task or self._integrity_interval <= 0:
            return
        self._integrity_task = asyncio.create_task(self._integrity_loop())
        self.logger.info(
            "Started database integrity checks",
            extra={"interval_seconds": self._integrity_interval},
        )

    async def close(self) -> None:
        self._closed = True
        if self._integrity_task:
            self._integrity_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._integrity_task
            self._integrity_task = None
        async with self._lock:
            conn = self._conn
            self._conn = None
        if conn is not None:
            await asyncio.to_thread(conn.close)

    async def run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Run an operation with a shared connection, serialized by a lock."""

        if self._closed:
            raise RuntimeError("Database manager is closed")
        async with self._lock:
            try:
                return await asyncio.to_thread(self._run_with_connection, operation)
            except sqlite3.DatabaseError as exc:
                raise self._handle_db_error(exc) from exc

    def _run_with_connection(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        if self._conn is None:
            _ensure_parent_dir(self.db_path)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            _configure_connection(self._conn)
        return operation(self._conn)

    async def _integrity_loop(self) -> None:
        try:
            while not self._closed:
                try:
                    await self.run(self._integrity_check)
                except DatabaseCorruptionError:
                    self.logger.exception(
                        "Database corruption detected during integrity check"
                    )
                    raise
                await asyncio.sleep(self._integrity_interval)
        except asyncio.CancelledError:
            self.logger.info("Database integrity checks cancelled")
            raise

    def _integrity_check(self, conn: sqlite3.Connection) -> None:
        results = conn.execute("PRAGMA integrity_check").fetchall()
        if not results:
            raise DatabaseCorruptionError("Integrity check returned no results")
        failures = [row[0] for row in results if str(row[0]).lower() != "ok"]
        if failures:
            backup_path = self._backup_corrupt_db("; ".join(failures))
            raise DatabaseCorruptionError(
                f"Integrity check failed; database copied to {backup_path}. "
                "Restore from a known-good backup or replace the database file."
            )

    def _handle_db_error(self, exc: sqlite3.DatabaseError) -> Exception:
        message = str(exc).lower()
        if any(
            key in message
            for key in ("malformed", "corrupt", "file is encrypted or is not a database")
        ):
            backup_path = self._backup_corrupt_db(message)
            return DatabaseCorruptionError(
                f"Database appears to be corrupted ({exc}); copied to {backup_path}. "
                "Restore the database from a backup or remove the corrupted file."
            )
        return exc

    def _backup_corrupt_db(self, reason: str) -> Path:
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        backup_path = self.db_path.with_suffix(f".corrupt-{timestamp}{self.db_path.suffix}")
        try:
            shutil.copy2(self.db_path, backup_path)
            self.logger.error(
                "Database corruption detected; backup created",
                extra={"reason": reason, "backup_path": str(backup_path)},
            )
        except Exception:
            self.logger.exception(
                "Failed to create corruption backup",
                extra={"reason": reason},
            )
        return backup_path
