"""Device persistence helpers."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from .config import ManualDevice
from .logging import get_logger


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _serialize_capabilities(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _deserialize_capabilities(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


@dataclass(frozen=True)
class DiscoveryResult:
    """Parsed discovery response details."""

    id: str
    ip: str
    model: Optional[str] = None
    description: Optional[str] = None
    capabilities: Any = None
    manual: bool = False


@dataclass(frozen=True)
class MappingRecord:
    """Persisted mapping between an ArtNet channel slice and a device."""

    device_id: str
    universe: int
    channel: int
    length: int
    capabilities: Any


@dataclass(frozen=True)
class DeviceStateUpdate:
    """Pending payload to be sent to a device."""

    device_id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PendingState:
    """Queued state row ready for delivery."""

    id: int
    device_id: str
    payload: str
    created_at: str


@dataclass(frozen=True)
class DeviceInfo:
    """Metadata required for transport decisions and monitoring."""

    id: str
    ip: Optional[str]
    capabilities: Any
    offline: bool
    failure_count: int
    last_payload_hash: Optional[str]
    last_payload_at: Optional[str]
    last_failure_at: Optional[str]


class DeviceStore:
    """SQLite-backed persistence for device metadata."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.logger = get_logger("govee.devices")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def sync_manual_devices(self, devices: Sequence[ManualDevice]) -> None:
        await asyncio.to_thread(self._sync_manual_devices, devices)

    def _sync_manual_devices(self, devices: Sequence[ManualDevice]) -> None:
        if not devices:
            return
        conn = self._connect()
        try:
            for device in devices:
                self._upsert_manual(conn, device)
            conn.commit()
            self.logger.info(
                "Synced manual devices", extra={"count": len(devices)}
            )
        finally:
            conn.close()

    def _upsert_manual(self, conn: sqlite3.Connection, device: ManualDevice) -> None:
        now = _now_iso()
        capabilities = _serialize_capabilities(device.capabilities)
        conn.execute(
            """
            INSERT INTO devices (
                id, ip, model, description, capabilities, manual,
                configured, enabled, discovered, first_seen, last_seen,
                stale, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, 1, 1, 0, ?, NULL, 0, datetime('now'), datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                ip=excluded.ip,
                model=COALESCE(excluded.model, devices.model),
                description=COALESCE(excluded.description, devices.description),
                capabilities=COALESCE(excluded.capabilities, devices.capabilities),
                manual=1,
                configured=1,
                enabled=1
            """,
            (
                device.id,
                device.ip,
                device.model,
                device.description,
                capabilities,
                now,
            ),
        )

    async def record_discovery(self, result: DiscoveryResult) -> None:
        await asyncio.to_thread(self._record_discovery, result)

    def _record_discovery(self, result: DiscoveryResult) -> None:
        now = _now_iso()
        capabilities = _serialize_capabilities(result.capabilities)
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO devices (
                    id, ip, model, description, capabilities, manual, discovered,
                    configured, enabled, first_seen, last_seen, stale,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, 1, 1, ?, ?, 0, datetime('now'), datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    ip=excluded.ip,
                    model=COALESCE(excluded.model, devices.model),
                    description=COALESCE(excluded.description, devices.description),
                    capabilities=COALESCE(excluded.capabilities, devices.capabilities),
                    last_seen=excluded.last_seen,
                    first_seen=COALESCE(devices.first_seen, excluded.last_seen),
                    manual=excluded.manual OR devices.manual,
                    discovered=1,
                    configured=1,
                    enabled=1,
                    stale=0
                """,
                (
                    result.id,
                    result.ip,
                    result.model,
                    result.description,
                    capabilities,
                    1 if result.manual else 0,
                    now,
                    now,
                ),
            )
            conn.commit()
            self.logger.debug(
                "Recorded discovery result",
                extra={
                    "id": result.id,
                    "ip": result.ip,
                    "model": result.model,
                    "manual": result.manual,
                },
            )
        finally:
            conn.close()

    async def mark_stale(self, stale_after_seconds: float) -> None:
        await asyncio.to_thread(self._mark_stale, stale_after_seconds)

    def _mark_stale(self, stale_after_seconds: float) -> None:
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                UPDATE devices
                SET stale = 1
                WHERE last_seen IS NOT NULL
                  AND stale = 0
                  AND (julianday('now') - julianday(last_seen)) * 86400 > ?
                """,
                (stale_after_seconds,),
            )
            conn.commit()
            if cursor.rowcount:
                self.logger.info(
                    "Marked devices stale",
                    extra={"count": cursor.rowcount, "threshold": stale_after_seconds},
                )
        finally:
            conn.close()

    async def manual_probe_targets(self) -> List[Tuple[str, str]]:
        return await asyncio.to_thread(self._manual_probe_targets)

    def _manual_probe_targets(self) -> List[Tuple[str, str]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, ip
                FROM devices
                WHERE manual = 1
                  AND enabled = 1
                  AND ip IS NOT NULL
                """
            ).fetchall()
            return [(row["id"], row["ip"]) for row in rows]
        finally:
            conn.close()

    async def mappings(self) -> List[MappingRecord]:
        return await asyncio.to_thread(self._mappings)

    def _mappings(self) -> List[MappingRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT m.device_id, m.universe, m.channel, m.length, d.capabilities
                FROM mappings m
                JOIN devices d ON d.id = m.device_id
                WHERE d.enabled = 1
                  AND (d.stale = 0 OR d.stale IS NULL)
                """
            ).fetchall()
            results: List[MappingRecord] = []
            for row in rows:
                results.append(
                    MappingRecord(
                        device_id=row["device_id"],
                        universe=int(row["universe"]),
                        channel=int(row["channel"]),
                        length=int(row["length"]),
                        capabilities=_deserialize_capabilities(row["capabilities"]),
                    )
                )
            return results
        finally:
            conn.close()

    async def update_capabilities(
        self, device_id: str, capabilities: Mapping[str, Any]
    ) -> None:
        await asyncio.to_thread(self._update_capabilities, device_id, capabilities)

    def _update_capabilities(
        self, device_id: str, capabilities: Mapping[str, Any]
    ) -> None:
        serialized = _serialize_capabilities(capabilities)
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE devices
                SET capabilities = ?
                WHERE id = ?
                """,
                (serialized, device_id),
            )
            conn.commit()
            self.logger.debug(
                "Updated device capabilities",
                extra={"id": device_id},
            )
        finally:
            conn.close()

    async def enqueue_state(self, update: DeviceStateUpdate) -> None:
        await asyncio.to_thread(self._enqueue_state, update)

    def _enqueue_state(self, update: DeviceStateUpdate) -> None:
        serialized = _serialize_capabilities(update.payload) or "null"
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO state (device_id, payload)
                VALUES (?, ?)
                """,
                (update.device_id, serialized),
            )
            conn.commit()
            self.logger.debug(
                "Enqueued device update",
                extra={"device_id": update.device_id},
            )
        finally:
            conn.close()

    async def set_last_seen(
        self, device_ids: Iterable[str], timestamp: Optional[str] = None
    ) -> None:
        await asyncio.to_thread(self._set_last_seen, device_ids, timestamp)

    def _set_last_seen(
        self, device_ids: Iterable[str], timestamp: Optional[str] = None
    ) -> None:
        ts = timestamp or _now_iso()
        conn = self._connect()
        try:
            conn.executemany(
                """
                UPDATE devices
                SET last_seen = ?, stale = 0
                WHERE id = ?
                """,
                [(ts, device_id) for device_id in device_ids],
            )
            conn.commit()
        finally:
            conn.close()

    async def pending_device_ids(self) -> List[str]:
        return await asyncio.to_thread(self._pending_device_ids)

    def _pending_device_ids(self) -> List[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT s.device_id
                FROM state s
                JOIN devices d ON d.id = s.device_id
                WHERE d.enabled = 1
                  AND (d.stale = 0 OR d.stale IS NULL)
                """
            ).fetchall()
            return [row["device_id"] for row in rows]
        finally:
            conn.close()

    async def next_state(self, device_id: str) -> Optional[PendingState]:
        return await asyncio.to_thread(self._next_state, device_id)

    def _next_state(self, device_id: str) -> Optional[PendingState]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT id, device_id, payload, created_at
                FROM state
                WHERE device_id = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (device_id,),
            ).fetchone()
            if not row:
                return None
            return PendingState(
                id=int(row["id"]),
                device_id=row["device_id"],
                payload=row["payload"],
                created_at=row["created_at"],
            )
        finally:
            conn.close()

    async def delete_state(self, state_id: int) -> None:
        await asyncio.to_thread(self._delete_state, state_id)

    def _delete_state(self, state_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                DELETE FROM state
                WHERE id = ?
                """,
                (state_id,),
            )
            conn.commit()
        finally:
            conn.close()

    async def device_info(self, device_id: str) -> Optional[DeviceInfo]:
        return await asyncio.to_thread(self._device_info, device_id)

    def _device_info(self, device_id: str) -> Optional[DeviceInfo]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT
                    id,
                    ip,
                    capabilities,
                    offline,
                    failure_count,
                    last_payload_hash,
                    last_payload_at,
                    last_failure_at,
                    enabled,
                    stale
                FROM devices
                WHERE id = ?
                """,
                (device_id,),
            ).fetchone()
            if not row or not row["enabled"] or row["stale"]:
                return None
            return DeviceInfo(
                id=row["id"],
                ip=row["ip"],
                capabilities=_deserialize_capabilities(row["capabilities"]),
                offline=bool(row["offline"]),
                failure_count=int(row["failure_count"] or 0),
                last_payload_hash=row["last_payload_hash"],
                last_payload_at=row["last_payload_at"],
                last_failure_at=row["last_failure_at"],
            )
        finally:
            conn.close()

    async def record_send_success(self, device_id: str, payload_hash: str) -> None:
        await asyncio.to_thread(self._record_send_success, device_id, payload_hash)

    def _record_send_success(self, device_id: str, payload_hash: str) -> None:
        now = _now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE devices
                SET
                    last_payload_hash = ?,
                    last_payload_at = ?,
                    failure_count = 0,
                    offline = 0,
                    last_failure_at = NULL
                WHERE id = ?
                """,
                (payload_hash, now, device_id),
            )
            conn.commit()
        finally:
            conn.close()

    async def record_send_failure(
        self, device_id: str, payload_hash: str, offline_threshold: int
    ) -> None:
        await asyncio.to_thread(
            self._record_send_failure, device_id, payload_hash, offline_threshold
        )

    def _record_send_failure(
        self, device_id: str, payload_hash: str, offline_threshold: int
    ) -> None:
        now = _now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE devices
                SET
                    last_payload_hash = ?,
                    last_payload_at = ?,
                    failure_count = failure_count + 1,
                    offline = CASE
                        WHEN (failure_count + 1) >= ? THEN 1
                        ELSE offline
                    END,
                    last_failure_at = ?
                WHERE id = ?
                """,
                (payload_hash, now, offline_threshold, now, device_id),
            )
            conn.commit()
        finally:
            conn.close()
