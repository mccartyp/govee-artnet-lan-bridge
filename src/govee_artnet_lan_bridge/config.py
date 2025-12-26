"""Configuration loading for the Govee Artnet LAN bridge."""

from __future__ import annotations

import argparse
import os
import sys
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - fallback for older interpreters
    import tomli as tomllib  # type: ignore


CONFIG_ENV_PREFIX = "GOVEE_ARTNET_"
CONFIG_VERSION = 1
MIN_SUPPORTED_CONFIG_VERSION = 1


def _default_db_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "govee-artnet-lan-bridge" / "bridge.sqlite3"


@dataclass(frozen=True)
class ManualDevice:
    """User-specified device metadata for discovery and persistence."""

    id: str
    ip: str
    model: Optional[str] = None
    description: Optional[str] = None
    capabilities: Optional[Any] = None


@dataclass(frozen=True)
class Config:
    """Application configuration."""

    artnet_port: int = 6454
    api_port: int = 8000
    api_key: Optional[str] = None
    api_bearer_token: Optional[str] = None
    api_docs: bool = True
    db_path: Path = _default_db_path()
    discovery_interval: float = 30.0
    rate_limit_per_second: float = 10.0
    rate_limit_burst: int = 20
    discovery_multicast_address: str = "239.255.255.250"
    discovery_multicast_port: int = 4003
    discovery_probe_payload: str = '{"cmd":"scan"}'
    discovery_response_timeout: float = 2.0
    discovery_stale_after: float = 300.0
    manual_unicast_probes: bool = True
    manual_devices: Sequence[ManualDevice] = ()
    device_default_transport: str = "udp"
    device_default_port: int = 4003
    device_send_timeout: float = 2.0
    device_send_retries: int = 3
    device_backoff_base: float = 0.5
    device_backoff_factor: float = 2.0
    device_backoff_max: float = 5.0
    device_max_send_rate: float = 10.0
    device_queue_poll_interval: float = 0.5
    device_idle_wait: float = 0.2
    device_offline_threshold: int = 3
    device_poll_enabled: bool = False
    device_poll_interval: float = 60.0
    device_poll_timeout: float = 1.5
    device_poll_rate_per_second: float = 2.0
    device_poll_rate_burst: int = 5
    device_poll_offline_threshold: int = 2
    device_poll_payload: str = '{"cmd":"devStatus"}'
    device_poll_port: Optional[int] = None
    device_poll_backoff_base: float = 1.0
    device_poll_backoff_factor: float = 2.0
    device_poll_backoff_max: float = 30.0
    device_poll_batch_size: int = 50
    device_max_queue_depth: int = 1000
    subsystem_failure_threshold: int = 5
    subsystem_failure_cooldown: float = 15.0
    log_format: str = "plain"
    log_level: str = "INFO"
    discovery_log_level: Optional[str] = None
    artnet_log_level: Optional[str] = None
    sender_log_level: Optional[str] = None
    api_log_level: Optional[str] = None
    noisy_log_sample_rate: float = 1.0
    trace_context_ids: bool = False
    trace_context_sample_rate: float = 1.0
    migrate_only: bool = False
    dry_run: bool = False
    config_version: int = CONFIG_VERSION

    def __post_init__(self) -> None:
        _validate_config(self)

    def logging_dict(self) -> Dict[str, Any]:
        """Return a sanitized mapping suitable for structured logging."""

        masked_keys = {
            "api_key": "***REDACTED***" if self.api_key else None,
            "api_bearer_token": "***REDACTED***" if self.api_bearer_token else None,
        }
        manual_devices = [
            {
                "id": device.id,
                "ip": device.ip,
                "model": device.model,
                "description": device.description,
                "capabilities": device.capabilities,
            }
            for device in self.manual_devices
        ]
        base: Dict[str, Any] = {
            "config_version": self.config_version,
            "artnet_port": self.artnet_port,
            "api_port": self.api_port,
            "api_docs": self.api_docs,
            "db_path": str(self.db_path),
            "discovery_interval": self.discovery_interval,
            "discovery_multicast_address": self.discovery_multicast_address,
            "discovery_multicast_port": self.discovery_multicast_port,
            "discovery_probe_payload": self.discovery_probe_payload,
            "discovery_response_timeout": self.discovery_response_timeout,
            "discovery_stale_after": self.discovery_stale_after,
            "manual_unicast_probes": self.manual_unicast_probes,
            "manual_devices": manual_devices,
            "device_default_transport": self.device_default_transport,
            "device_default_port": self.device_default_port,
            "device_send_timeout": self.device_send_timeout,
            "device_send_retries": self.device_send_retries,
            "device_backoff_base": self.device_backoff_base,
            "device_backoff_factor": self.device_backoff_factor,
            "device_backoff_max": self.device_backoff_max,
            "device_max_send_rate": self.device_max_send_rate,
            "device_queue_poll_interval": self.device_queue_poll_interval,
            "device_idle_wait": self.device_idle_wait,
            "device_offline_threshold": self.device_offline_threshold,
            "device_poll_enabled": self.device_poll_enabled,
            "device_poll_interval": self.device_poll_interval,
            "device_poll_timeout": self.device_poll_timeout,
            "device_poll_rate_per_second": self.device_poll_rate_per_second,
            "device_poll_rate_burst": self.device_poll_rate_burst,
            "device_poll_offline_threshold": self.device_poll_offline_threshold,
            "device_poll_payload": self.device_poll_payload,
            "device_poll_port": self.device_poll_port,
            "device_poll_backoff_base": self.device_poll_backoff_base,
            "device_poll_backoff_factor": self.device_poll_backoff_factor,
            "device_poll_backoff_max": self.device_poll_backoff_max,
            "device_poll_batch_size": self.device_poll_batch_size,
            "device_max_queue_depth": self.device_max_queue_depth,
            "subsystem_failure_threshold": self.subsystem_failure_threshold,
            "subsystem_failure_cooldown": self.subsystem_failure_cooldown,
            "log_format": self.log_format,
            "log_level": self.log_level,
            "discovery_log_level": self.discovery_log_level,
            "artnet_log_level": self.artnet_log_level,
            "sender_log_level": self.sender_log_level,
            "api_log_level": self.api_log_level,
            "noisy_log_sample_rate": self.noisy_log_sample_rate,
            "trace_context_ids": self.trace_context_ids,
            "trace_context_sample_rate": self.trace_context_sample_rate,
            "migrate_only": self.migrate_only,
            "dry_run": self.dry_run,
        }
        base.update(masked_keys)
        return base


    @classmethod
    def from_sources(cls, cli_args: Optional[Iterable[str]] = None) -> "Config":
        """Load configuration from defaults, file, env, and CLI (in that order)."""

        args = _parse_cli(cli_args)
        file_config = _load_file_config(
            args.config
            or _coerce_path(os.environ.get(f"{CONFIG_ENV_PREFIX}CONFIG"))
            or None
        )
        env_config = _load_env_config(CONFIG_ENV_PREFIX)
        cli_config = _cli_overrides(args)

        config = cls()
        config = _apply_mapping(config, file_config)
        config = _apply_mapping(config, env_config)
        config = _apply_mapping(config, cli_config)
        return config


def _validate_config(config: Config) -> None:
    _validate_version(config.config_version)
    _validate_range("artnet_port", config.artnet_port, 1, 65535)
    _validate_range("api_port", config.api_port, 1, 65535)
    _validate_range("discovery_interval", config.discovery_interval, 1.0, 3600.0)
    _validate_range(
        "discovery_response_timeout", config.discovery_response_timeout, 0.1, 120.0
    )
    _validate_range("discovery_stale_after", config.discovery_stale_after, 1.0, 172800.0)
    _validate_range("rate_limit_per_second", config.rate_limit_per_second, 0.1, 10000.0)
    _validate_range("rate_limit_burst", config.rate_limit_burst, 1, 100000)
    _validate_range("device_send_timeout", config.device_send_timeout, 0.1, 120.0)
    _validate_range("device_send_retries", config.device_send_retries, 1, 20)
    _validate_range("device_backoff_base", config.device_backoff_base, 0.0, 60.0)
    _validate_range("device_backoff_factor", config.device_backoff_factor, 1.0, 10.0)
    _validate_range("device_backoff_max", config.device_backoff_max, 0.1, 300.0)
    _validate_range("device_max_send_rate", config.device_max_send_rate, 0.0, 10000.0)
    _validate_range("device_queue_poll_interval", config.device_queue_poll_interval, 0.01, 60.0)
    _validate_range("device_idle_wait", config.device_idle_wait, 0.0, 10.0)
    _validate_range("device_offline_threshold", config.device_offline_threshold, 1, 1000)
    _validate_range("device_poll_interval", config.device_poll_interval, 0.1, 86400.0)
    _validate_range("device_poll_timeout", config.device_poll_timeout, 0.05, 60.0)
    _validate_range("device_poll_rate_per_second", config.device_poll_rate_per_second, 0.0, 10000.0)
    _validate_range("device_poll_rate_burst", config.device_poll_rate_burst, 0, 100000)
    _validate_range("device_poll_offline_threshold", config.device_poll_offline_threshold, 1, 1000)
    _validate_range("device_poll_backoff_base", config.device_poll_backoff_base, 0.0, 300.0)
    _validate_range("device_poll_backoff_factor", config.device_poll_backoff_factor, 1.0, 10.0)
    _validate_range("device_poll_backoff_max", config.device_poll_backoff_max, 0.1, 3600.0)
    _validate_range("device_poll_batch_size", config.device_poll_batch_size, 1, 100000)
    if config.device_poll_port is not None:
        _validate_range("device_poll_port", config.device_poll_port, 1, 65535)
    _validate_range("device_max_queue_depth", config.device_max_queue_depth, 1, 1000000)
    _validate_range("subsystem_failure_threshold", config.subsystem_failure_threshold, 1, 1000)
    _validate_range("subsystem_failure_cooldown", config.subsystem_failure_cooldown, 0.0, 3600.0)
    _validate_range("noisy_log_sample_rate", config.noisy_log_sample_rate, 0.0, 1.0)
    _validate_range("trace_context_sample_rate", config.trace_context_sample_rate, 0.0, 1.0)
    for field_name, value in (
        ("log_level", config.log_level),
        ("discovery_log_level", config.discovery_log_level),
        ("artnet_log_level", config.artnet_log_level),
        ("sender_log_level", config.sender_log_level),
        ("api_log_level", config.api_log_level),
    ):
        _validate_log_level_value(value, field_name)


def _validate_version(version: int) -> None:
    if version < MIN_SUPPORTED_CONFIG_VERSION:
        raise ValueError(
            f"Config version {version} is too old; minimum supported is {MIN_SUPPORTED_CONFIG_VERSION}."
        )
    if version > CONFIG_VERSION:
        raise ValueError(
            f"Config version {version} is newer than supported ({CONFIG_VERSION}); please upgrade the bridge."
        )


def _validate_range(name: str, value: float, minimum: float, maximum: float) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}; got {value}.")


def _validate_log_level_value(value: Optional[str], name: str) -> None:
    if value is None:
        return
    allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if value.upper() not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}; got {value}.")


def _parse_cli(cli_args: Optional[Iterable[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="govee-artnet-bridge",
        description="Run the Govee Artnet LAN bridge.",
    )
    parser.add_argument("--config", type=Path, help="Path to TOML config file.")
    parser.add_argument("--artnet-port", type=int, help="UDP port for Artnet traffic.")
    parser.add_argument(
        "--api-port",
        type=int,
        help="TCP port for the HTTP/API server.",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        help="API key required via X-API-Key or Authorization: ApiKey <key>.",
    )
    parser.add_argument(
        "--api-bearer-token",
        type=str,
        help="Bearer token required via Authorization: Bearer <token>.",
    )
    parser.add_argument(
        "--api-docs",
        action="store_true",
        help="Enable interactive API docs (enabled by default).",
    )
    parser.add_argument(
        "--no-api-docs",
        action="store_true",
        help="Disable interactive API docs.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        help="Path to the SQLite database file.",
    )
    parser.add_argument(
        "--discovery-interval",
        type=float,
        help="Seconds between device discovery scans.",
    )
    parser.add_argument(
        "--discovery-multicast-address",
        type=str,
        help="Multicast address used for discovery probes.",
    )
    parser.add_argument(
        "--discovery-multicast-port",
        type=int,
        help="UDP port used for discovery probes and responses.",
    )
    parser.add_argument(
        "--discovery-probe-payload",
        type=str,
        help="Raw payload sent in discovery probes.",
    )
    parser.add_argument(
        "--discovery-response-timeout",
        type=float,
        help="Seconds to wait for discovery responses after sending probes.",
    )
    parser.add_argument(
        "--discovery-stale-after",
        type=float,
        help="Seconds after last_seen before a device is marked stale.",
    )
    parser.add_argument(
        "--rate-limit-per-second",
        type=float,
        help="Allowed outgoing events per second.",
    )
    parser.add_argument(
        "--rate-limit-burst",
        type=int,
        help="Allowed burst size for the rate limiter.",
    )
    parser.add_argument(
        "--log-format",
        choices=["plain", "json"],
        help="Structured logging format.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log verbosity level.",
    )
    parser.add_argument(
        "--discovery-log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log verbosity for discovery.",
    )
    parser.add_argument(
        "--artnet-log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log verbosity for ArtNet handling.",
    )
    parser.add_argument(
        "--sender-log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log verbosity for sender pipeline.",
    )
    parser.add_argument(
        "--api-log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log verbosity for API server.",
    )
    parser.add_argument(
        "--noisy-log-sample-rate",
        type=float,
        help="Sampling rate for noisy debug logs (0.0-1.0).",
    )
    parser.add_argument(
        "--trace-context-ids",
        action="store_true",
        help="Enable context IDs tying ArtNet frames to send attempts.",
    )
    parser.add_argument(
        "--trace-context-sample-rate",
        type=float,
        help="Sampling rate for emitting context IDs (0.0-1.0).",
    )
    parser.add_argument(
        "--manual-device",
        action="append",
        dest="manual_devices",
        help=(
            "Manually provision a device as id=<id>,ip=<ip>,model=<model>,"
            "description=<description>,capabilities=<json>"
        ),
    )
    parser.add_argument(
        "--manual-unicast-probes",
        action="store_true",
        help="Send unicast discovery probes to manually configured devices.",
    )
    parser.add_argument(
        "--device-default-transport",
        choices=["tcp", "udp"],
        help="Transport used when capabilities do not specify one.",
    )
    parser.add_argument(
        "--device-default-port",
        type=int,
        help="Port used when capabilities do not specify one.",
    )
    parser.add_argument(
        "--device-send-timeout",
        type=float,
        help="Seconds to wait for device send operations.",
    )
    parser.add_argument(
        "--device-send-retries",
        type=int,
        help="Number of retries before marking a send as failed.",
    )
    parser.add_argument(
        "--device-backoff-base",
        type=float,
        help="Initial backoff delay between retries.",
    )
    parser.add_argument(
        "--device-backoff-factor",
        type=float,
        help="Multiplier applied to backoff between attempts.",
    )
    parser.add_argument(
        "--device-backoff-max",
        type=float,
        help="Maximum backoff delay between retries.",
    )
    parser.add_argument(
        "--device-max-send-rate",
        type=float,
        help="Maximum sends per second per device.",
    )
    parser.add_argument(
        "--device-queue-poll-interval",
        type=float,
        help="Seconds between scans for devices with queued payloads.",
    )
    parser.add_argument(
        "--device-idle-wait",
        type=float,
        help="Delay when a device queue is empty before polling again.",
    )
    parser.add_argument(
        "--device-offline-threshold",
        type=int,
        help="Consecutive failures before marking a device offline.",
    )
    parser.add_argument(
        "--device-poll-enabled",
        action="store_true",
        help="Enable background polling for device reachability.",
    )
    parser.add_argument(
        "--device-poll-interval",
        type=float,
        help="Seconds between poll cycles.",
    )
    parser.add_argument(
        "--device-poll-timeout",
        type=float,
        help="Seconds to wait for poll responses.",
    )
    parser.add_argument(
        "--device-poll-rate-per-second",
        type=float,
        help="Maximum poll requests per second.",
    )
    parser.add_argument(
        "--device-poll-rate-burst",
        type=int,
        help="Maximum burst of poll requests.",
    )
    parser.add_argument(
        "--device-poll-offline-threshold",
        type=int,
        help="Consecutive poll failures before marking a device offline.",
    )
    parser.add_argument(
        "--device-poll-payload",
        type=str,
        help="Raw payload sent in poll requests.",
    )
    parser.add_argument(
        "--device-poll-port",
        type=int,
        help="Port used for polling (defaults to device_default_port when omitted).",
    )
    parser.add_argument(
        "--device-poll-backoff-base",
        type=float,
        help="Initial backoff delay between poll retries or cycles.",
    )
    parser.add_argument(
        "--device-poll-backoff-factor",
        type=float,
        help="Multiplier applied to poll backoff between attempts.",
    )
    parser.add_argument(
        "--device-poll-backoff-max",
        type=float,
        help="Maximum backoff delay between poll retries or cycles.",
    )
    parser.add_argument(
        "--device-poll-batch-size",
        type=int,
        help="Maximum number of devices to poll per cycle.",
    )
    parser.add_argument(
        "--device-max-queue-depth",
        type=int,
        help="Maximum queued payloads per device before refusing new entries.",
    )
    parser.add_argument(
        "--subsystem-failure-threshold",
        type=int,
        help="Consecutive failures before subsystem attempts are temporarily suppressed.",
    )
    parser.add_argument(
        "--subsystem-failure-cooldown",
        type=float,
        help="Seconds to pause a subsystem after repeated failures.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without network IO while still emitting logs and queueing updates.",
    )
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help="Run database migrations and exit without starting services.",
    )
    parser.add_argument(
        "--config-version",
        type=int,
        help="Version of the configuration schema being supplied.",
    )
    return parser.parse_args(args=cli_args)


def _load_file_config(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as f:
        parsed = tomllib.load(f)
    if not isinstance(parsed, Mapping):
        raise ValueError("Configuration file must contain a TOML table.")
    return {k.replace("-", "_"): v for k, v in parsed.items()}


def _load_env_config(prefix: str) -> Dict[str, Any]:
    mapping: Dict[str, Any] = {}
    for field in Config.__dataclass_fields__:
        env_key = f"{prefix}{field}".upper()
        if env_key in os.environ:
            mapping[field] = os.environ[env_key]
    return mapping


def _cli_overrides(args: argparse.Namespace) -> Dict[str, Any]:
    mapping = {k: v for k, v in vars(args).items() if k not in ("config", "no_api_docs") and v is not None}
    if args.no_api_docs:
        mapping["api_docs"] = False
    return mapping


def _apply_mapping(config: Config, overrides: Mapping[str, Any]) -> Config:
    data: MutableMapping[str, Any] = {}
    for key, value in overrides.items():
        if value is None:
            continue
        if key == "db_path":
            data[key] = _coerce_path(value)
        elif key in {
            "artnet_port",
            "api_port",
            "rate_limit_burst",
            "device_max_queue_depth",
            "subsystem_failure_threshold",
            "config_version",
        }:
            data[key] = int(value)
        elif key in {
            "discovery_interval",
            "rate_limit_per_second",
            "device_send_timeout",
            "device_backoff_base",
            "device_backoff_factor",
            "device_backoff_max",
            "device_max_send_rate",
            "device_queue_poll_interval",
            "device_idle_wait",
            "device_poll_interval",
            "device_poll_timeout",
            "device_poll_rate_per_second",
            "device_poll_backoff_base",
            "device_poll_backoff_factor",
            "device_poll_backoff_max",
            "subsystem_failure_cooldown",
            "noisy_log_sample_rate",
            "trace_context_sample_rate",
        }:
            data[key] = float(value)
        elif key in {"discovery_response_timeout", "discovery_stale_after"}:
            data[key] = float(value)
        elif key in {"discovery_multicast_port", "device_default_port", "device_poll_port"}:
            data[key] = int(value)
        elif key in {"device_send_retries", "device_offline_threshold", "device_poll_offline_threshold", "device_poll_rate_burst", "device_poll_batch_size"}:
            data[key] = int(value)
        elif key in {"log_format", "log_level"}:
            if key == "log_level":
                data[key] = str(value).upper()
            else:
                data[key] = str(value).lower()
        elif key in {"discovery_log_level", "artnet_log_level", "sender_log_level", "api_log_level"}:
            data[key] = str(value).upper()
        elif key == "device_default_transport":
            data[key] = str(value).lower()
        elif key in {"migrate_only", "api_docs", "device_poll_enabled"}:
            data[key] = _coerce_bool(value)
        elif key == "manual_unicast_probes":
            data[key] = _coerce_bool(value)
        elif key == "dry_run":
            data[key] = _coerce_bool(value)
        elif key == "trace_context_ids":
            data[key] = _coerce_bool(value)
        elif key == "manual_devices":
            data[key] = _coerce_manual_devices(value)
        else:
            data[key] = value
    return replace(config, **data)


def _coerce_path(value: Any) -> Path:
    return value if isinstance(value, Path) else Path(str(value)).expanduser()


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _coerce_manual_devices(value: Any) -> Sequence[ManualDevice]:
    if value is None:
        return ()
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            return (_manual_from_str(value),)
        return _coerce_manual_devices(parsed)

    if isinstance(value, ManualDevice):
        return (value,)
    if isinstance(value, Mapping):
        return (_manual_from_mapping(value),)

    if isinstance(value, Iterable):
        devices: List[ManualDevice] = []
        for item in value:
            if isinstance(item, ManualDevice):
                devices.append(item)
            elif isinstance(item, Mapping):
                devices.append(_manual_from_mapping(item))
            elif isinstance(item, str):
                devices.extend(_coerce_manual_devices(item))
            else:
                raise ValueError("Unsupported manual device entry")
        return tuple(devices)

    raise ValueError("Unsupported manual_devices configuration")


def _manual_from_mapping(value: Mapping[str, Any]) -> ManualDevice:
    if "id" not in value or "ip" not in value:
        raise ValueError("Manual devices require 'id' and 'ip' fields")
    return ManualDevice(
        id=str(value["id"]),
        ip=str(value["ip"]),
        model=str(value.get("model")) if value.get("model") is not None else None,
        description=str(value.get("description")) if value.get("description") is not None else None,
        capabilities=value.get("capabilities"),
    )


_PAIR = re.compile(r"(?P<key>[^=]+)=(?P<value>.+)")


def _manual_from_str(value: str) -> ManualDevice:
    cap_value: Optional[str] = None
    if "capabilities=" in value:
        prefix, cap_raw = value.split("capabilities=", 1)
        value = prefix.rstrip(",")
        cap_value = cap_raw.strip()

    parts = [part.strip() for part in value.split(",") if part.strip()]
    mapping: Dict[str, Any] = {}
    for part in parts:
        match = _PAIR.match(part)
        if not match:
            raise ValueError(
                "Manual device arguments must be key=value pairs separated by commas"
            )
        key = match.group("key").strip()
        val = match.group("value").strip()
        mapping[key] = val
    if cap_value is not None:
        try:
            mapping["capabilities"] = json.loads(cap_value)
        except json.JSONDecodeError:
            mapping["capabilities"] = cap_value
    return _manual_from_mapping(mapping)


def load_config(cli_args: Optional[Iterable[str]] = None) -> Config:
    """Public helper used by the entrypoint."""

    try:
        return Config.from_sources(cli_args)
    except Exception as exc:  # pragma: no cover - defensive logging path
        print(f"Failed to load configuration: {exc}", file=sys.stderr)
        raise
