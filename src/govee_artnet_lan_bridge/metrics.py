"""Prometheus metrics helpers."""

from __future__ import annotations

from typing import Optional

try:
    from prometheus_client import (  # type: ignore
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        CONTENT_TYPE_LATEST,
        generate_latest,
    )
except ModuleNotFoundError:  # pragma: no cover - fallback for constrained environments
    class _NoopMetric:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        def labels(self, **_: str) -> "_NoopMetric":
            return self

        def inc(self, amount: float = 1.0) -> None:
            return None

        def observe(self, _: float) -> None:
            return None

        def set(self, _: float) -> None:
            return None

    class CollectorRegistry:  # type: ignore[empty-body]
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

    def generate_latest(_: CollectorRegistry | None = None) -> bytes:
        return b""

    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"

    Counter = Gauge = Histogram = _NoopMetric  # type: ignore

_REGISTRY = CollectorRegistry()

REQUEST_LATENCY = Histogram(
    "govee_api_request_duration_seconds",
    "Time spent processing API requests",
    ["method", "path", "status"],
    registry=_REGISTRY,
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)
REQUEST_COUNT = Counter(
    "govee_api_requests_total",
    "HTTP requests processed by the API",
    ["method", "path", "status"],
    registry=_REGISTRY,
)
DISCOVERY_RESPONSES = Counter(
    "govee_discovery_responses_total",
    "Discovery responses parsed",
    ["source"],
    registry=_REGISTRY,
)
DISCOVERY_ERRORS = Counter(
    "govee_discovery_errors_total",
    "Discovery responses discarded",
    ["reason"],
    registry=_REGISTRY,
)
ARTNET_PACKETS = Counter(
    "govee_artnet_packets_total",
    "ArtNet packets received",
    ["universe"],
    registry=_REGISTRY,
)
ARTNET_DEVICE_UPDATES = Counter(
    "govee_artnet_device_updates_total",
    "Device updates generated from ArtNet payloads",
    ["device_id"],
    registry=_REGISTRY,
)
ARTNET_INGEST_DURATION = Histogram(
    "govee_artnet_ingest_duration_seconds",
    "Time spent applying ArtNet frames",
    ["universe", "status"],
    registry=_REGISTRY,
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)
DEVICE_SEND_RESULTS = Counter(
    "govee_device_sends_total",
    "Device send outcomes",
    ["result"],
    registry=_REGISTRY,
)
DEVICE_SEND_DURATION = Histogram(
    "govee_device_send_duration_seconds",
    "Time to deliver payloads to devices",
    ["result", "transport"],
    registry=_REGISTRY,
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)
SUBSYSTEM_FAILURES = Counter(
    "govee_subsystem_failures_total",
    "Subsystem failures leading to suppression",
    ["subsystem"],
    registry=_REGISTRY,
)
SUBSYSTEM_STATUS = Gauge(
    "govee_subsystem_status",
    "Subsystem health (0=suppressed,1=degraded/recovering,2=ok)",
    ["subsystem"],
    registry=_REGISTRY,
)
DISCOVERY_CYCLE_DURATION = Histogram(
    "govee_discovery_cycle_duration_seconds",
    "Time spent performing discovery cycles",
    ["result"],
    registry=_REGISTRY,
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10],
)
DEVICE_QUEUE_DEPTH = Gauge(
    "govee_device_queue_depth",
    "Queued state payloads per device",
    ["device_id"],
    registry=_REGISTRY,
)
DEVICE_QUEUE_TOTAL = Gauge(
    "govee_device_queue_depth_total",
    "Total queued state payloads",
    registry=_REGISTRY,
)
OFFLINE_DEVICES = Gauge(
    "govee_offline_devices_total",
    "Number of devices marked offline",
    registry=_REGISTRY,
)
RATE_LIMIT_TOKENS = Gauge(
    "govee_rate_limit_tokens",
    "Available tokens in the device sender rate limiter",
    registry=_REGISTRY,
)
RATE_LIMIT_WAITS = Counter(
    "govee_rate_limit_waits_total",
    "Send attempts delayed by the rate limiter",
    ["scope"],
    registry=_REGISTRY,
)


def get_registry() -> CollectorRegistry:
    """Return the registry holding the bridge metrics."""

    return _REGISTRY


def latest_metrics() -> bytes:
    """Render the latest metrics payload for scraping."""

    return generate_latest(_REGISTRY)


def observe_request(method: str, path: str, status: int, duration_seconds: float) -> None:
    """Record API request metrics."""

    status_str = str(status)
    REQUEST_COUNT.labels(method=method, path=path, status=status_str).inc()
    REQUEST_LATENCY.labels(method=method, path=path, status=status_str).observe(duration_seconds)


def record_discovery_response(source: str) -> None:
    """Record a successful discovery response."""

    DISCOVERY_RESPONSES.labels(source=source).inc()


def record_discovery_error(reason: str) -> None:
    """Record a discarded discovery response."""

    DISCOVERY_ERRORS.labels(reason=reason).inc()


def record_artnet_packet(universe: int) -> None:
    """Record an ArtNet packet received for a universe."""

    ARTNET_PACKETS.labels(universe=str(universe)).inc()


def record_artnet_update(device_id: str) -> None:
    """Record a device update generated from an ArtNet payload."""

    ARTNET_DEVICE_UPDATES.labels(device_id=device_id).inc()


def record_send_result(result: str) -> None:
    """Record the result of a device send attempt."""

    DEVICE_SEND_RESULTS.labels(result=result).inc()


def observe_send_duration(result: str, transport: str, duration_seconds: float) -> None:
    """Record how long a send attempt took."""

    DEVICE_SEND_DURATION.labels(result=result, transport=transport).observe(duration_seconds)


def record_subsystem_failure(subsystem: str) -> None:
    """Record a subsystem failure triggering suppression."""

    SUBSYSTEM_FAILURES.labels(subsystem=subsystem).inc()


def record_subsystem_status(subsystem: str, status: str) -> None:
    """Record the current subsystem status."""

    code = 0
    if status == "ok":
        code = 2
    elif status in {"recovering", "degraded"}:
        code = 1
    SUBSYSTEM_STATUS.labels(subsystem=subsystem).set(code)


def observe_discovery_cycle(result: str, duration_seconds: float) -> None:
    """Record the duration of a discovery cycle."""

    DISCOVERY_CYCLE_DURATION.labels(result=result).observe(duration_seconds)


def observe_artnet_ingest(universe: int, status: str, duration_seconds: float) -> None:
    """Record time spent handling an ArtNet frame."""

    ARTNET_INGEST_DURATION.labels(universe=str(universe), status=status).observe(duration_seconds)


def set_queue_depth(device_id: str, depth: int) -> None:
    """Set the queued payload depth for a device."""

    DEVICE_QUEUE_DEPTH.labels(device_id=device_id).set(depth)


def set_total_queue_depth(total: int) -> None:
    """Set the total queued payload depth across devices."""

    DEVICE_QUEUE_TOTAL.set(total)


def set_offline_devices(count: int) -> None:
    """Set the number of offline devices."""

    OFFLINE_DEVICES.set(count)


def set_rate_limit_tokens(tokens: float) -> None:
    """Expose the current available rate limit tokens."""

    RATE_LIMIT_TOKENS.set(tokens)


def record_rate_limit_wait(scope: str) -> None:
    """Record a send that waited for the rate limiter."""

    RATE_LIMIT_WAITS.labels(scope=scope).inc()


METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST
