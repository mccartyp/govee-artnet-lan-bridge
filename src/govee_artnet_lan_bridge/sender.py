"""Per-device send queues and transport handling."""

from __future__ import annotations

import asyncio
import hashlib
import socket
import contextlib
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .config import Config
from .devices import DeviceInfo, DeviceStore, PendingState
from .health import BackoffPolicy, HealthMonitor
from .logging import get_logger
from .metrics import (
    observe_send_duration,
    record_rate_limit_wait,
    record_send_result,
    set_rate_limit_tokens,
)


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

    def __init__(
        self, config: Config, store: DeviceStore, health: Optional[HealthMonitor] = None
    ) -> None:
        self.config = config
        self.store = store
        self.logger = get_logger("govee.sender")
        self._stop_event = asyncio.Event()
        self._poll_task: Optional[asyncio.Task[None]] = None
        self._device_tasks: Dict[str, asyncio.Task[None]] = {}
        self._dry_run = config.dry_run
        self._health = health or HealthMonitor(
            ("sender",),
            failure_threshold=config.subsystem_failure_threshold,
            cooldown_seconds=config.subsystem_failure_cooldown,
        )
        self._backoff = BackoffPolicy(
            base=config.device_backoff_base,
            factor=config.device_backoff_factor,
            maximum=config.device_backoff_max,
        )
        self._rate_tokens = float(config.rate_limit_burst)
        self._rate_last_refill = time.perf_counter()
        self._rate_lock = asyncio.Lock()
        set_rate_limit_tokens(self._rate_tokens)

    async def start(self) -> None:
        self._stop_event.clear()
        await self.store.refresh_metrics()
        self._rate_tokens = float(self.config.rate_limit_burst)
        self._rate_last_refill = time.perf_counter()
        set_rate_limit_tokens(self._rate_tokens)
        self._poll_task = asyncio.create_task(self._poll_loop())
        if self._dry_run:
            self.logger.info("Device sender service started in dry-run mode; payloads will not be sent.")
        else:
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
            await self._process_state(state)
            if rate_delay:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=rate_delay)
                except asyncio.TimeoutError:
                    continue

    async def _process_state(self, state: PendingState) -> None:
        started = time.perf_counter()
        transport_label = "none"
        context_extra = {"device_id": state.device_id, "context_id": state.context_id}

        def _finalize(result: str) -> None:
            duration = time.perf_counter() - started
            record_send_result(result)
            observe_send_duration(result, transport_label, duration)

        allowed, remaining = await self._health.allow_attempt("sender")
        if not allowed:
            self.logger.warning(
                "Send pipeline suppressed after repeated failures",
                extra={**context_extra, "cooldown_seconds": round(remaining, 2)},
            )
            await self._sleep_with_stop(remaining)
            _finalize("suppressed")
            return

        payload_hash = hashlib.sha256(state.payload.encode("utf-8")).hexdigest()
        device = await self.store.device_info(state.device_id)
        if device is None:
            self.logger.warning(
                "Skipping send for unknown or disabled device",
                extra=context_extra,
            )
            await self.store.record_send_failure(
                state.device_id, payload_hash, self.config.device_offline_threshold
            )
            await self._health.record_failure("sender", RuntimeError("unknown or disabled device"))
            await self.store.quarantine_state(
                state, payload_hash, reason="device_unavailable", details="missing, disabled, or stale"
            )
            _finalize("dead_letter")
            return

        target = _derive_target(self.config, device)
        if target is None:
            self.logger.warning(
                "Device missing IP; cannot send",
                extra=context_extra,
            )
            await self.store.record_send_failure(
                state.device_id, payload_hash, self.config.device_offline_threshold
            )
            await self._health.record_failure("sender", RuntimeError("device missing IP"))
            await self.store.quarantine_state(
                state, payload_hash, reason="missing_ip", details="device has no IP address"
            )
            _finalize("dead_letter")
            return

        if device.failure_count == 0 and device.last_payload_hash == payload_hash:
            self.logger.debug(
                "Dropping duplicate payload",
                extra=context_extra,
            )
            await self.store.delete_state(state.id)
            return

        await self._acquire_rate_limit(state.device_id, state.context_id)
        payload = state.payload.encode("utf-8")
        transport_label = target.transport
        try:
            success = await self._send_with_retries(target, payload, payload_hash, state.context_id)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error(
                "Unhandled send error",
                extra=context_extra,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            await self.store.record_send_failure(
                state.device_id, payload_hash, self.config.device_offline_threshold
            )
            await self._health.record_failure("sender", exc)
            await self._sleep_with_stop(self._backoff.delay(1))
            _finalize("error")
            return
        if success:
            await self._health.record_success("sender")
            await self.store.record_send_success(state.device_id, payload_hash)
            await self.store.set_last_seen([state.device_id])
            await self.store.delete_state(state.id)
            _finalize("success" if not self._dry_run else "dry_run")
        else:
            await self.store.record_send_failure(
                state.device_id, payload_hash, self.config.device_offline_threshold
            )
            await self._health.record_failure("sender", RuntimeError("send failed"))
            await self._sleep_with_stop(self._backoff.delay(1))
            _finalize("failure")

    async def _send_with_retries(
        self, target: DeviceTarget, payload: bytes, payload_hash: str, context_id: Optional[str]
    ) -> bool:
        if self._dry_run:
            self.logger.info(
                "Dry-run: would send payload",
                extra={
                    "device_id": target.id,
                    "transport": target.transport,
                    "port": target.port,
                    "context_id": context_id,
                },
            )
            return True
        attempts = max(1, self.config.device_send_retries)
        delays = self._backoff.iter_delays(attempts)
        for attempt in range(1, attempts + 1):
            if await self._send_once(target, payload, context_id):
                return True
            if attempt == attempts:
                break
            await self._sleep_with_stop(delays[attempt - 1])
        self.logger.error(
            "Exhausted retries sending payload",
            extra={
                "device_id": target.id,
                "transport": target.transport,
                "port": target.port,
                "hash": payload_hash,
                "attempts": attempts,
                "context_id": context_id,
            },
        )
        return False

    async def _send_once(
        self, target: DeviceTarget, payload: bytes, context_id: Optional[str]
    ) -> bool:
        if target.transport == "tcp":
            return await self._send_tcp(target, payload, context_id)
        return await self._send_udp(target, payload, context_id)

    async def _send_udp(
        self, target: DeviceTarget, payload: bytes, context_id: Optional[str]
    ) -> bool:
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
                    extra={"device_id": target.id, "context_id": context_id},
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
                extra={"device_id": target.id, "context_id": context_id},
            )
            return False

    async def _send_tcp(
        self, target: DeviceTarget, payload: bytes, context_id: Optional[str]
    ) -> bool:
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
                extra={"device_id": target.id, "context_id": context_id},
            )
            return False

    async def _sleep_with_stop(self, delay: float) -> None:
        if delay <= 0:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return

    async def _acquire_rate_limit(self, device_id: str, context_id: Optional[str]) -> None:
        if self.config.rate_limit_per_second <= 0 or self.config.rate_limit_burst <= 0:
            return
        while not self._stop_event.is_set():
            async with self._rate_lock:
                now = time.perf_counter()
                elapsed = max(0.0, now - self._rate_last_refill)
                self._rate_last_refill = now
                self._rate_tokens = min(
                    float(self.config.rate_limit_burst),
                    self._rate_tokens + elapsed * self.config.rate_limit_per_second,
                )
                if self._rate_tokens >= 1.0:
                    self._rate_tokens -= 1.0
                    set_rate_limit_tokens(self._rate_tokens)
                    return
                wait_seconds = (1.0 - self._rate_tokens) / self.config.rate_limit_per_second
                set_rate_limit_tokens(self._rate_tokens)
            self.logger.debug(
                "Rate limit exceeded; delaying send",
                extra={
                    "device_id": device_id,
                    "context_id": context_id,
                    "wait_seconds": round(wait_seconds, 3),
                    "tokens": round(self._rate_tokens, 3),
                },
            )
            record_rate_limit_wait("global")
            await self._sleep_with_stop(wait_seconds)
