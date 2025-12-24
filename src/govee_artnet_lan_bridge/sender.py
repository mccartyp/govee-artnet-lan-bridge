"""Per-device send queues and transport handling."""

from __future__ import annotations

import asyncio
import hashlib
import socket
import contextlib
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .config import Config
from .devices import DeviceInfo, DeviceStore, PendingState
from .logging import get_logger


@dataclass(frozen=True)
class DeviceTarget:
    """Resolved transport target for a device."""

    id: str
    ip: str
    port: int
    transport: str
    capabilities: Any


def _coerce_transport(capabilities: Any, default: str) -> str:
    if isinstance(capabilities, Mapping):
        value = capabilities.get("transport") or capabilities.get("protocol")
        if isinstance(value, str):
            lowered = value.lower()
            if lowered in {"tcp", "udp"}:
                return lowered
    return default


def _coerce_port(capabilities: Any, default: int) -> int:
    if isinstance(capabilities, Mapping):
        for key in ("port", "control_port", "device_port"):
            if key in capabilities:
                try:
                    parsed = int(capabilities[key])
                    if parsed > 0:
                        return parsed
                except (TypeError, ValueError):
                    continue
    return default


def _derive_target(config: Config, device: DeviceInfo) -> Optional[DeviceTarget]:
    if not device.ip:
        return None
    transport = _coerce_transport(device.capabilities, config.device_default_transport)
    port = _coerce_port(device.capabilities, config.device_default_port)
    return DeviceTarget(
        id=device.id,
        ip=device.ip,
        port=port,
        transport=transport,
        capabilities=device.capabilities,
    )


class DeviceSenderService:
    """Background service draining device queues and handling retries."""

    def __init__(self, config: Config, store: DeviceStore) -> None:
        self.config = config
        self.store = store
        self.logger = get_logger("govee.sender")
        self._stop_event = asyncio.Event()
        self._poll_task: Optional[asyncio.Task[None]] = None
        self._device_tasks: Dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())
        self.logger.info("Device sender service started")

    async def stop(self) -> None:
        self._stop_event.set()
        tasks = list(self._device_tasks.values())
        if self._poll_task:
            tasks.append(self._poll_task)
        for task in tasks:
            task.cancel()
        if tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks)
        self._poll_task = None
        self._device_tasks.clear()
        self.logger.info("Device sender service stopped")

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            await self._ensure_workers()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.config.device_queue_poll_interval
                )
            except asyncio.TimeoutError:
                continue

    async def _ensure_workers(self) -> None:
        device_ids = await self.store.pending_device_ids()
        for device_id in device_ids:
            if device_id not in self._device_tasks:
                self._device_tasks[device_id] = asyncio.create_task(
                    self._run_device_queue(device_id)
                )
        done_ids = [device_id for device_id, task in self._device_tasks.items() if task.done()]
        for device_id in done_ids:
            task = self._device_tasks.pop(device_id)
            if task.cancelled():
                continue
            if task.exception():
                self.logger.error(
                    "Device send task failed",
                    extra={"device_id": device_id},
                    exc_info=task.exception(),
                )

    async def _run_device_queue(self, device_id: str) -> None:
        rate_delay = 0.0
        if self.config.device_max_send_rate > 0:
            rate_delay = 1.0 / self.config.device_max_send_rate
        backoff_floor = max(0.0, self.config.device_backoff_base)
        while not self._stop_event.is_set():
            state = await self.store.next_state(device_id)
            if state is None:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.config.device_idle_wait
                    )
                except asyncio.TimeoutError:
                    continue
                continue
            await self._process_state(state, backoff_floor)
            if rate_delay:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=rate_delay)
                except asyncio.TimeoutError:
                    continue

    async def _process_state(self, state: PendingState, backoff_floor: float) -> None:
        payload_hash = hashlib.sha256(state.payload.encode("utf-8")).hexdigest()
        device = await self.store.device_info(state.device_id)
        if device is None:
            self.logger.warning(
                "Skipping send for unknown or disabled device",
                extra={"device_id": state.device_id},
            )
            await self.store.record_send_failure(
                state.device_id, payload_hash, self.config.device_offline_threshold
            )
            await asyncio.sleep(backoff_floor)
            return

        target = _derive_target(self.config, device)
        if target is None:
            self.logger.warning(
                "Device missing IP; cannot send",
                extra={"device_id": state.device_id},
            )
            await self.store.record_send_failure(
                state.device_id, payload_hash, self.config.device_offline_threshold
            )
            await asyncio.sleep(backoff_floor)
            return

        if device.failure_count == 0 and device.last_payload_hash == payload_hash:
            self.logger.debug(
                "Dropping duplicate payload",
                extra={"device_id": state.device_id},
            )
            await self.store.delete_state(state.id)
            return

        payload = state.payload.encode("utf-8")
        success = await self._send_with_retries(target, payload, payload_hash)
        if success:
            await self.store.record_send_success(state.device_id, payload_hash)
            await self.store.set_last_seen([state.device_id])
            await self.store.delete_state(state.id)
        else:
            await self.store.record_send_failure(
                state.device_id, payload_hash, self.config.device_offline_threshold
            )
            await asyncio.sleep(backoff_floor)

    async def _send_with_retries(
        self, target: DeviceTarget, payload: bytes, payload_hash: str
    ) -> bool:
        backoff = max(0.0, self.config.device_backoff_base)
        attempts = max(1, self.config.device_send_retries)
        for attempt in range(1, attempts + 1):
            if await self._send_once(target, payload):
                return True
            if attempt == attempts:
                break
            await asyncio.sleep(backoff)
            backoff = min(
                self.config.device_backoff_max,
                max(backoff * self.config.device_backoff_factor, self.config.device_backoff_base),
            )
        self.logger.error(
            "Exhausted retries sending payload",
            extra={
                "device_id": target.id,
                "transport": target.transport,
                "port": target.port,
                "hash": payload_hash,
                "attempts": attempts,
            },
        )
        return False

    async def _send_once(self, target: DeviceTarget, payload: bytes) -> bool:
        if target.transport == "tcp":
            return await self._send_tcp(target, payload)
        return await self._send_udp(target, payload)

    async def _send_udp(self, target: DeviceTarget, payload: bytes) -> bool:
        def _send() -> bool:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(self.config.device_send_timeout)
                    sent = sock.sendto(payload, (target.ip, target.port))
                return sent == len(payload)
            except OSError as exc:
                self.logger.warning(
                    "UDP send failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                    extra={"device_id": target.id},
                )
                return False

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_send), timeout=self.config.device_send_timeout
            )
        except (asyncio.TimeoutError, OSError) as exc:
            self.logger.warning(
                "UDP send timed out",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"device_id": target.id},
            )
            return False

    async def _send_tcp(self, target: DeviceTarget, payload: bytes) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target.ip, target.port),
                timeout=self.config.device_send_timeout,
            )
            writer.write(payload)
            await asyncio.wait_for(writer.drain(), timeout=self.config.device_send_timeout)
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    writer.wait_closed(), timeout=self.config.device_send_timeout
                )
            return True
        except (asyncio.TimeoutError, OSError) as exc:
            self.logger.warning(
                "TCP send failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"device_id": target.id},
            )
            return False
