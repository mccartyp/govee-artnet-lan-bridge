"""Configuration loading for the Govee Artnet LAN bridge."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - fallback for older interpreters
    import tomli as tomllib  # type: ignore


CONFIG_ENV_PREFIX = "GOVEE_ARTNET_"


def _default_db_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "govee-artnet-lan-bridge" / "bridge.sqlite3"


@dataclass(frozen=True)
class Config:
    """Application configuration."""

    artnet_port: int = 6454
    api_port: int = 8000
    db_path: Path = _default_db_path()
    discovery_interval: float = 30.0
    rate_limit_per_second: float = 10.0
    rate_limit_burst: int = 20
    log_format: str = "plain"
    log_level: str = "INFO"
    migrate_only: bool = False

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
    return {k: v for k, v in vars(args).items() if k != "config" and v is not None}


def _apply_mapping(config: Config, overrides: Mapping[str, Any]) -> Config:
    data: MutableMapping[str, Any] = {}
    for key, value in overrides.items():
        if value is None:
            continue
        if key == "db_path":
            data[key] = _coerce_path(value)
        elif key in {"artnet_port", "api_port", "rate_limit_burst"}:
            data[key] = int(value)
        elif key in {"discovery_interval", "rate_limit_per_second"}:
            data[key] = float(value)
        elif key in {"log_format", "log_level"}:
            if key == "log_level":
                data[key] = str(value).upper()
            else:
                data[key] = str(value).lower()
        elif key == "migrate_only":
            data[key] = _coerce_bool(value)
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


def load_config(cli_args: Optional[Iterable[str]] = None) -> Config:
    """Public helper used by the entrypoint."""

    try:
        return Config.from_sources(cli_args)
    except Exception as exc:  # pragma: no cover - defensive logging path
        print(f"Failed to load configuration: {exc}", file=sys.stderr)
        raise
