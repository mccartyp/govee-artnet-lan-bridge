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
    log_format: str = "plain"
    log_level: str = "INFO"
    migrate_only: bool = False
    dry_run: bool = False

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
        "--dry-run",
        action="store_true",
        help="Run without network IO while still emitting logs and queueing updates.",
    )
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help="Run database migrations and exit without starting services.",
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
    mapping = {k: v for k, v in vars(args).items() if k != "config" and v is not None}
    if mapping.get("no_api_docs"):
        mapping["api_docs"] = False
        mapping.pop("no_api_docs", None)
    return mapping


def _apply_mapping(config: Config, overrides: Mapping[str, Any]) -> Config:
    data: MutableMapping[str, Any] = {}
    for key, value in overrides.items():
        if value is None:
            continue
        if key == "db_path":
            data[key] = _coerce_path(value)
        elif key in {"artnet_port", "api_port", "rate_limit_burst"}:
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
        }:
            data[key] = float(value)
        elif key in {"discovery_response_timeout", "discovery_stale_after"}:
            data[key] = float(value)
        elif key in {"discovery_multicast_port", "device_default_port"}:
            data[key] = int(value)
        elif key in {"device_send_retries", "device_offline_threshold"}:
            data[key] = int(value)
        elif key in {"log_format", "log_level"}:
            if key == "log_level":
                data[key] = str(value).upper()
            else:
                data[key] = str(value).lower()
        elif key == "device_default_transport":
            data[key] = str(value).lower()
        elif key in {"migrate_only", "api_docs"}:
            data[key] = _coerce_bool(value)
        elif key == "manual_unicast_probes":
            data[key] = _coerce_bool(value)
        elif key == "dry_run":
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
