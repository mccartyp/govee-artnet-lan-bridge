"""Shared retry, backoff, and health tracking utilities."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from .metrics import record_subsystem_failure, record_subsystem_status


@dataclass
class BackoffPolicy:
    """Exponential backoff parameters."""

    base: float
    factor: float
    maximum: float

    def delay(self, failures: int) -> float:
        """Calculate the delay for the given failure count."""

        if failures <= 0:
            return 0.0
        backoff = max(0.0, self.base)
        for _ in range(failures - 1):
            backoff = min(
                self.maximum,
                max(backoff * self.factor, self.base),
            )
        return backoff

    def iter_delays(self, attempts: int) -> Tuple[float, ...]:
        """Yield backoff delays for the configured number of attempts."""

        if attempts <= 1:
            return tuple()
        delays = []
        backoff = max(0.0, self.base)
        for _ in range(attempts - 1):
            delays.append(backoff)
            backoff = min(
                self.maximum,
                max(backoff * self.factor, self.base),
            )
        return tuple(delays)


@dataclass
class SubsystemState:
    """Mutable health status for a subsystem."""

    name: str
    status: str = "ok"
    failures: int = 0
    suppressions: int = 0
    suppressed_until: Optional[float] = None
    last_error: Optional[str] = None
    last_success: Optional[float] = None
    last_failure: Optional[float] = None

    def as_dict(self, now: Optional[float] = None) -> Dict[str, Any]:
        remaining = None
        if self.suppressed_until is not None:
            remaining = max(0.0, self.suppressed_until - (now or time.monotonic()))
        return {
            "status": self.status,
            "failures": self.failures,
            "suppressions": self.suppressions,
            "suppressed_for": remaining,
            "last_error": self.last_error,
            "last_success": self.last_success,
            "last_failure": self.last_failure,
        }


class HealthMonitor:
    """Track subsystem health with a simple circuit breaker."""

    def __init__(
        self,
        subsystem_names: Tuple[str, ...],
        failure_threshold: int,
        cooldown_seconds: float,
    ) -> None:
        self._states: Dict[str, SubsystemState] = {
            name: SubsystemState(name=name) for name in subsystem_names
        }
        self._failure_threshold = max(1, failure_threshold)
        self._cooldown = max(0.0, cooldown_seconds)
        self._lock = asyncio.Lock()
        for name in subsystem_names:
            record_subsystem_status(name, "ok")

    async def record_success(self, subsystem: str) -> None:
        """Mark a successful attempt for a subsystem."""

        async with self._lock:
            state = self._states[subsystem]
            state.status = "ok"
            state.failures = 0
            state.last_error = None
            state.suppressed_until = None
            state.last_success = time.monotonic()
            record_subsystem_status(subsystem, "ok")

    async def record_failure(self, subsystem: str, error: Optional[BaseException] = None) -> None:
        """Record a failure and potentially open the circuit."""

        async with self._lock:
            state = self._states[subsystem]
            state.failures += 1
            state.last_failure = time.monotonic()
            state.last_error = str(error) if error else state.last_error
            should_suppress = state.failures >= self._failure_threshold
            if should_suppress:
                state.status = "suppressed"
                state.suppressions += 1
                state.suppressed_until = state.last_failure + self._cooldown
                record_subsystem_failure(subsystem)
            else:
                state.status = "degraded"
            record_subsystem_status(subsystem, state.status)

    async def allow_attempt(self, subsystem: str) -> Tuple[bool, float]:
        """Return whether an attempt is allowed and remaining suppression time."""

        async with self._lock:
            state = self._states[subsystem]
            now = time.monotonic()
            if state.suppressed_until and state.suppressed_until > now:
                remaining = state.suppressed_until - now
                record_subsystem_status(subsystem, state.status)
                return False, remaining
            if state.status == "suppressed":
                state.status = "recovering"
            record_subsystem_status(subsystem, state.status)
            return True, 0.0

    async def snapshot(self) -> Mapping[str, Dict[str, Any]]:
        """Return a copy of the subsystem health state."""

        async with self._lock:
            now = time.monotonic()
            return {name: state.as_dict(now) for name, state in self._states.items()}
