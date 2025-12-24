"""SQLite helpers and migrations for the Govee Artnet LAN bridge."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Iterable, List, Tuple

from .logging import get_logger

Migration = Callable[[sqlite3.Connection], None]

SCHEMA_VERSION_KEY = "schema_version"


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


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
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_mappings_universe_channel
            ON mappings (universe, channel);

        CREATE INDEX IF NOT EXISTS idx_devices_last_seen
            ON devices (last_seen);
        """
    )


MIGRATIONS: List[Tuple[int, Migration]] = [
    (1, _migration_initial_schema),
]


def apply_migrations(db_path: Path) -> None:
    """Apply any pending migrations to the SQLite database."""

    logger = get_logger("govee.migrations")
    _ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
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
