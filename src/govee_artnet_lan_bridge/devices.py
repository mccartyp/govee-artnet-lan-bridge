"""Device persistence helpers."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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


def _coerce_mode_for_mapping(capabilities: Any, length: int) -> str:
    default_mode = "rgbw" if length >= 4 else "rgb" if length >= 3 else "brightness"
    if isinstance(capabilities, Mapping):
        mode = str(capabilities.get("mode", default_mode)).lower()
        if mode in {"rgb", "rgbw", "brightness", "custom"}:
            return mode
    return default_mode


def _coerce_order_for_mapping(capabilities: Any, mode: str) -> Tuple[str, ...]:
    def _normalize_entry(entry: str) -> Optional[str]:
        value = entry.strip().lower()
        if value in {"r", "g", "b", "w", "brightness"}:
            return value
        return None

    default_orders: Dict[str, Tuple[str, ...]] = {
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


def _required_channels(capabilities: Any, length: int) -> int:
    mode = _coerce_mode_for_mapping(capabilities, length)
    order = _coerce_order_for_mapping(capabilities, mode)
    return len(order) if mode != "custom" else length


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


@dataclass(frozen=True)
class DeviceRow:
    """Full device row for API exposure."""

    id: str
    ip: Optional[str]
    model: Optional[str]
    description: Optional[str]
    capabilities: Any
    manual: bool
    discovered: bool
    configured: bool
    enabled: bool
    stale: bool
    offline: bool
    last_seen: Optional[str]
    first_seen: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MappingRow:
    """Mapping row with primary key for management APIs."""

    id: int
    device_id: str
    universe: int
    channel: int
    length: int
    created_at: str
    updated_at: str


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

    async def devices(self) -> List[DeviceRow]:
        return await asyncio.to_thread(self._devices)

    def _devices(self) -> List[DeviceRow]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    id,
                    ip,
                    model,
                    description,
                    capabilities,
                    manual,
                    discovered,
                    configured,
                    enabled,
                    stale,
                    offline,
                    last_seen,
                    first_seen,
                    created_at,
                    updated_at
                FROM devices
                ORDER BY created_at ASC
                """
            ).fetchall()
            return [self._row_to_device(row) for row in rows]
        finally:
            conn.close()

    async def device(self, device_id: str) -> Optional[DeviceRow]:
        return await asyncio.to_thread(self._device, device_id)

    def _device(self, device_id: str) -> Optional[DeviceRow]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT
                    id,
                    ip,
                    model,
                    description,
                    capabilities,
                    manual,
                    discovered,
                    configured,
                    enabled,
                    stale,
                    offline,
                    last_seen,
                    first_seen,
                    created_at,
                    updated_at
                FROM devices
                WHERE id = ?
                """,
                (device_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_device(row)
        finally:
            conn.close()

    async def create_manual_device(self, manual: ManualDevice) -> DeviceRow:
        return await asyncio.to_thread(self._create_manual_device, manual)

    def _create_manual_device(self, manual: ManualDevice) -> DeviceRow:
        conn = self._connect()
        try:
            self._upsert_manual(conn, manual)
            conn.commit()
            row = conn.execute(
                """
                SELECT
                    id,
                    ip,
                    model,
                    description,
                    capabilities,
                    manual,
                    discovered,
                    configured,
                    enabled,
                    stale,
                    offline,
                    last_seen,
                    first_seen,
                    created_at,
                    updated_at
                FROM devices
                WHERE id = ?
                """,
                (manual.id,),
            ).fetchone()
            if not row:
                raise ValueError("Failed to create device")
            self.logger.info("Created manual device", extra={"id": manual.id, "ip": manual.ip})
            return self._row_to_device(row)
        finally:
            conn.close()

    async def update_device(
        self,
        device_id: str,
        *,
        ip: Optional[str] = None,
        model: Optional[str] = None,
        description: Optional[str] = None,
        capabilities: Optional[Any] = None,
        enabled: Optional[bool] = None,
    ) -> Optional[DeviceRow]:
        return await asyncio.to_thread(
            self._update_device,
            device_id,
            ip,
            model,
            description,
            capabilities,
            enabled,
        )

    def _update_device(
        self,
        device_id: str,
        ip: Optional[str],
        model: Optional[str],
        description: Optional[str],
        capabilities: Optional[Any],
        enabled: Optional[bool],
    ) -> Optional[DeviceRow]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM devices WHERE id = ?",
                (device_id,),
            ).fetchone()
            if not row:
                return None
            serialized_caps = (
                _serialize_capabilities(capabilities)
                if capabilities is not None
                else row["capabilities"]
            )
            conn.execute(
                """
                UPDATE devices
                SET
                    ip = COALESCE(?, ip),
                    model = COALESCE(?, model),
                    description = COALESCE(?, description),
                    capabilities = ?,
                    enabled = COALESCE(?, enabled),
                    configured = 1
                WHERE id = ?
                """,
                (
                    ip,
                    model,
                    description,
                    serialized_caps,
                    int(enabled) if enabled is not None else None,
                    device_id,
                ),
            )
            conn.commit()
            updated = conn.execute(
                """
                SELECT
                    id,
                    ip,
                    model,
                    description,
                    capabilities,
                    manual,
                    discovered,
                    configured,
                    enabled,
                    stale,
                    offline,
                    last_seen,
                    first_seen,
                    created_at,
                    updated_at
                FROM devices
                WHERE id = ?
                """,
                (device_id,),
            ).fetchone()
            if not updated:
                return None
            self.logger.info(
                "Updated device",
                extra={"id": device_id, "enabled": enabled},
            )
            return self._row_to_device(updated)
        finally:
            conn.close()

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

    async def mapping_rows(self) -> List[MappingRow]:
        return await asyncio.to_thread(self._mapping_rows)

    def _mapping_rows(self) -> List[MappingRow]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, device_id, universe, channel, length, created_at, updated_at
                FROM mappings
                ORDER BY universe, channel
                """
            ).fetchall()
            return [self._row_to_mapping(row) for row in rows]
        finally:
            conn.close()

    async def mapping_by_id(self, mapping_id: int) -> Optional[MappingRow]:
        return await asyncio.to_thread(self._mapping_by_id, mapping_id)

    def _mapping_by_id(self, mapping_id: int) -> Optional[MappingRow]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT id, device_id, universe, channel, length, created_at, updated_at
                FROM mappings
                WHERE id = ?
                """,
                (mapping_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_mapping(row)
        finally:
            conn.close()

    async def create_mapping(
        self,
        *,
        device_id: str,
        universe: int,
        channel: int,
        length: int,
        allow_overlap: bool = False,
    ) -> MappingRow:
        return await asyncio.to_thread(
            self._create_mapping, device_id, universe, channel, length, allow_overlap
        )

    def _create_mapping(
        self, device_id: str, universe: int, channel: int, length: int, allow_overlap: bool
    ) -> MappingRow:
        if channel <= 0 or length <= 0:
            raise ValueError("Channel and length must be positive")
        conn = self._connect()
        try:
            device_row = conn.execute(
                "SELECT capabilities FROM devices WHERE id = ?",
                (device_id,),
            ).fetchone()
            if not device_row:
                raise ValueError("Device not found")
            capabilities = _deserialize_capabilities(device_row["capabilities"])
            required = _required_channels(capabilities, length)
            if required > length:
                raise ValueError("Mapping length is shorter than required channels")
            self._ensure_no_overlap(conn, universe, channel, length, None, allow_overlap)
            cursor = conn.execute(
                """
                INSERT INTO mappings (device_id, universe, channel, length)
                VALUES (?, ?, ?, ?)
                """,
                (device_id, universe, channel, length),
            )
            conn.commit()
            mapping_id = cursor.lastrowid
            created = conn.execute(
                """
                SELECT id, device_id, universe, channel, length, created_at, updated_at
                FROM mappings
                WHERE id = ?
                """,
                (mapping_id,),
            ).fetchone()
            if not created:
                raise ValueError("Failed to create mapping")
            self.logger.info(
                "Created mapping",
                extra={
                    "mapping_id": mapping_id,
                    "device_id": device_id,
                    "universe": universe,
                    "channel": channel,
                    "length": length,
                },
            )
            return self._row_to_mapping(created)
        finally:
            conn.close()

    async def update_mapping(
        self,
        mapping_id: int,
        *,
        device_id: Optional[str] = None,
        universe: Optional[int] = None,
        channel: Optional[int] = None,
        length: Optional[int] = None,
        allow_overlap: bool = False,
    ) -> Optional[MappingRow]:
        return await asyncio.to_thread(
            self._update_mapping,
            mapping_id,
            device_id,
            universe,
            channel,
            length,
            allow_overlap,
        )

    def _update_mapping(
        self,
        mapping_id: int,
        device_id: Optional[str],
        universe: Optional[int],
        channel: Optional[int],
        length: Optional[int],
        allow_overlap: bool,
    ) -> Optional[MappingRow]:
        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT id, device_id, universe, channel, length FROM mappings WHERE id = ?",
                (mapping_id,),
            ).fetchone()
            if not existing:
                return None
            new_device_id = device_id or existing["device_id"]
            new_universe = universe if universe is not None else int(existing["universe"])
            new_channel = channel if channel is not None else int(existing["channel"])
            new_length = length if length is not None else int(existing["length"])
            if new_channel <= 0 or new_length <= 0:
                raise ValueError("Channel and length must be positive")
            device_row = conn.execute(
                "SELECT capabilities FROM devices WHERE id = ?",
                (new_device_id,),
            ).fetchone()
            if not device_row:
                raise ValueError("Device not found")
            capabilities = _deserialize_capabilities(device_row["capabilities"])
            required = _required_channels(capabilities, new_length)
            if required > new_length:
                raise ValueError("Mapping length is shorter than required channels")
            self._ensure_no_overlap(
                conn, new_universe, new_channel, new_length, mapping_id, allow_overlap
            )
            conn.execute(
                """
                UPDATE mappings
                SET device_id = ?, universe = ?, channel = ?, length = ?
                WHERE id = ?
                """,
                (new_device_id, new_universe, new_channel, new_length, mapping_id),
            )
            conn.commit()
            updated = conn.execute(
                """
                SELECT id, device_id, universe, channel, length, created_at, updated_at
                FROM mappings
                WHERE id = ?
                """,
                (mapping_id,),
            ).fetchone()
            if not updated:
                return None
            self.logger.info(
                "Updated mapping",
                extra={
                    "mapping_id": mapping_id,
                    "device_id": new_device_id,
                    "universe": new_universe,
                    "channel": new_channel,
                    "length": new_length,
                },
            )
            return self._row_to_mapping(updated)
        finally:
            conn.close()

    async def delete_mapping(self, mapping_id: int) -> bool:
        return await asyncio.to_thread(self._delete_mapping, mapping_id)

    def _delete_mapping(self, mapping_id: int) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM mappings WHERE id = ?",
                (mapping_id,),
            )
            conn.commit()
            if cursor.rowcount:
                self.logger.info("Deleted mapping", extra={"mapping_id": mapping_id})
            return cursor.rowcount > 0
        finally:
            conn.close()

    def _ensure_no_overlap(
        self,
        conn: sqlite3.Connection,
        universe: int,
        channel: int,
        length: int,
        exclude_id: Optional[int],
        allow_overlap: bool,
    ) -> None:
        if allow_overlap:
            return
        start = channel
        end = channel + length - 1
        query = """
            SELECT id, device_id, channel, length
            FROM mappings
            WHERE universe = ?
              AND (? BETWEEN channel AND (channel + length - 1)
                   OR (channel BETWEEN ? AND ?))
        """
        params: Tuple[Any, ...] = (universe, start, start, end)
        if exclude_id is not None:
            query += " AND id != ?"
            params += (exclude_id,)
        conflict = conn.execute(query, params).fetchone()
        if conflict:
            raise ValueError("Mapping overlaps an existing entry")

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

    async def stats(self) -> Mapping[str, int]:
        return await asyncio.to_thread(self._stats)

    def _stats(self) -> Mapping[str, int]:
        conn = self._connect()
        try:
            device_counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled,
                    SUM(CASE WHEN offline = 1 THEN 1 ELSE 0 END) AS offline
                FROM devices
                """
            ).fetchone()
            mapping_counts = conn.execute(
                "SELECT COUNT(*) AS total FROM mappings"
            ).fetchone()
            return {
                "devices_total": int(device_counts["total"] or 0),
                "devices_enabled": int(device_counts["enabled"] or 0),
                "devices_offline": int(device_counts["offline"] or 0),
                "mappings_total": int(mapping_counts["total"] or 0),
            }
        finally:
            conn.close()

    def _row_to_device(self, row: sqlite3.Row) -> DeviceRow:
        return DeviceRow(
            id=row["id"],
            ip=row["ip"],
            model=row["model"],
            description=row["description"],
            capabilities=_deserialize_capabilities(row["capabilities"]),
            manual=bool(row["manual"]),
            discovered=bool(row["discovered"]),
            configured=bool(row["configured"]),
            enabled=bool(row["enabled"]),
            stale=bool(row["stale"]),
            offline=bool(row["offline"]),
            last_seen=row["last_seen"],
            first_seen=row["first_seen"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_mapping(self, row: sqlite3.Row) -> MappingRow:
        return MappingRow(
            id=int(row["id"]),
            device_id=row["device_id"],
            universe=int(row["universe"]),
            channel=int(row["channel"]),
            length=int(row["length"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
