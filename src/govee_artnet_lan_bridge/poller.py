"""Background device liveness polling."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .config import Config
from .devices import DeviceStore, PollTarget
from .health import BackoffPolicy, HealthMonitor
from .logging import get_logger
from .metrics import (
    observe_device_poll_duration,
    record_device_poll,
    record_device_poll_state_update,
    set_device_polling_enabled,
)


@dataclass(frozen=True)
class PollResult:
    """Result of a poll operation."""

    device_id: str
    state: Optional[Mapping[str, Any]]
    status: str


def _extract_state(payload: Any) -> Optional[Mapping[str, Any]]:
    """Attempt to extract a minimal state snapshot from a poll response."""

    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    state = data.get("state") if isinstance(data.get("state"), Mapping) else data
    if not isinstance(state, Mapping):
        return None
    keys = ("power", "brightness", "color")
    result = {key: value for key, value in state.items() if key in keys}
    return result or None


class DevicePollerService:
    """Periodically poll devices for reachability and lightweight state."""

    def __init__(
        self, config: Config, store: DeviceStore, health: Optional[HealthMonitor] = None
    ) -> None:
        self.config = config
        self.store = store
        self.logger = get_logger("govee.poller")
        self._health = health or HealthMonitor(
            ("poller",),
            failure_threshold=self.config.subsystem_failure_threshold,
            cooldown_seconds=self.config.subsystem_failure_cooldown,
        )
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None
        self._payload = self.config.device_poll_payload.encode("utf-8")
        self._backoff = BackoffPolicy(
            base=self.config.device_poll_backoff_base,
            factor=self.config.device_poll_backoff_factor,
            maximum=self.config.device_poll_backoff_max,
        )
        self._rate_tokens = float(self.config.device_poll_rate_burst)
        self._rate_last_refill = time.perf_counter()
        self._rate_lock = asyncio.Lock()
        self._batch_cursor = 0

    async def start(self) -> None:
        if self._task or not self.config.device_poll_enabled:
            set_device_polling_enabled(self.config.device_poll_enabled)
            if not self.config.device_poll_enabled:
                self.logger.info("Device polling disabled; skipping poller startup.")
            return
        self._stop_event.clear()
        set_device_polling_enabled(True)
        self._task = asyncio.create_task(self._run())
        self.logger.info(
            "Device poller started",
            extra={
                "interval_seconds": self.config.device_poll_interval,
                "timeout_seconds": self.config.device_poll_timeout,
                "rate_per_second": self.config.device_poll_rate_per_second,
                "rate_burst": self.config.device_poll_rate_burst,
                "batch_size": self.config.device_poll_batch_size,
            },
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        set_device_polling_enabled(False)
        self.logger.info("Device poller stopped")

    async def _run(self) -> None:
        failures = 0
        while not self._stop_event.is_set():
            allowed, remaining = await self._health.allow_attempt("poller")
            if not allowed:
                self.logger.warning(
                    "Poller suppressed after failures",
                    extra={"cooldown_seconds": round(remaining, 2)},
                )
                await self._sleep_with_stop(remaining)
                continue
            try:
                await self._run_cycle()
                await self._health.record_success("poller")
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                failures += 1
                self.logger.exception("Poll cycle failed")
                await self._health.record_failure("poller", exc)
                await self._sleep_with_stop(self._backoff.delay(failures))
                continue
            await self._sleep_with_stop(self.config.device_poll_interval)

    async def _run_cycle(self) -> None:
        targets = await self.store.poll_targets()
        if not targets:
            return

        batch = self._select_batch(targets)
        tasks = [asyncio.create_task(self._poll_target(target)) for target in batch]
        if tasks:
            await asyncio.gather(*tasks)

    def _select_batch(self, targets: list[PollTarget]) -> list[PollTarget]:
        batch_size = max(1, min(self.config.device_poll_batch_size, len(targets)))
        start = self._batch_cursor % len(targets)
        end = start + batch_size
        if end <= len(targets):
            batch = targets[start:end]
        else:
            batch = targets[start:] + targets[: end - len(targets)]
        self._batch_cursor = end % len(targets)
        return batch

    async def _poll_target(self, target: PollTarget) -> None:
        started = time.perf_counter()
        status = "failure"
        state: Optional[Mapping[str, Any]] = None
        try:
            await self._acquire_rate_limit()
            response = await self._send_poll(target)
            if response is None:
                await self.store.record_poll_failure(
                    target.id, self.config.device_poll_offline_threshold
                )
                status = "timeout"
                return
            try:
                payload = json.loads(response)
            except json.JSONDecodeError:
                payload = None
            state = _extract_state(payload)
            if state:
                record_device_poll_state_update()
            await self.store.record_poll_success(target.id, state)
            status = "success_state" if state else "success"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning(
                "Poll failed",
                extra={"device_id": target.id, "ip": target.ip},
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            await self.store.record_poll_failure(
                target.id, self.config.device_poll_offline_threshold
            )
            status = "error"
        finally:
            duration = time.perf_counter() - started
            record_device_poll(status)
            observe_device_poll_duration(status, duration)

    async def _acquire_rate_limit(self) -> None:
        if self.config.device_poll_rate_per_second <= 0 or self.config.device_poll_rate_burst <= 0:
            return
        while not self._stop_event.is_set():
            async with self._rate_lock:
                now = time.perf_counter()
                elapsed = max(0.0, now - self._rate_last_refill)
                self._rate_last_refill = now
                self._rate_tokens = min(
                    float(self.config.device_poll_rate_burst),
                    self._rate_tokens + elapsed * self.config.device_poll_rate_per_second,
                )
                if self._rate_tokens >= 1.0:
                    self._rate_tokens -= 1.0
                    return
                wait_seconds = (
                    (1.0 - self._rate_tokens) / max(self.config.device_poll_rate_per_second, 0.001)
                )
            await self._sleep_with_stop(wait_seconds)

    async def _send_poll(self, target: PollTarget) -> Optional[str]:
        timeout = self.config.device_poll_timeout
        port = self.config.device_poll_port or self.config.device_default_port

        def _send() -> Optional[str]:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(timeout)
                    sent = sock.sendto(self._payload, (target.ip, port))
                    if sent != len(self._payload):
                        return None
                    data, _ = sock.recvfrom(2048)
                    return data.decode("utf-8")
            except (OSError, TimeoutError):
                return None

        try:
            return await asyncio.wait_for(asyncio.to_thread(_send), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def _sleep_with_stop(self, delay: float) -> None:
        if delay <= 0:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return
