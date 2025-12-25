"""SQLite helpers and migrations for the Govee Artnet LAN bridge."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple, TypeVar

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


MIGRATIONS: List[Tuple[int, Migration]] = [
    (1, _migration_initial_schema),
    (2, _migration_device_metadata),
    (3, _migration_device_send_tracking),
    (4, _migration_state_context_id),
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
