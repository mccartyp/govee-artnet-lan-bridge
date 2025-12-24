"""Structured logging helpers."""

from __future__ import annotations

import json
import logging
import logging.config
from datetime import datetime, timezone
from typing import Any, Dict

from .config import Config


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter for structured logs."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        base: Dict[str, Any] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            base["stack"] = self.formatStack(record.stack_info)
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in {
                "name",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "msg",
                "args",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
            }:
                continue
            base[key] = value
        return json.dumps(base, ensure_ascii=False)


def configure_logging(config: Config) -> None:
    """Configure global logging based on the provided config."""

    level = config.log_level.upper()
    if config.log_format == "json":
        formatter = {
            "format": "json",
            "()": f"{__name__}.JsonFormatter",
        }
    else:
        formatter = {
            "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S%z",
        }

    logging.config.dictConfig(
        {
            "version": 1,
            "formatters": {
                "default": formatter,
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "level": level,
                }
            },
            "loggers": {
                "govee": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.discovery": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.rate_limit": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.devices": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.discovery.protocol": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.artnet": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
            },
            "root": {"level": level, "handlers": ["console"]},
        }
    )


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger for the requested subsystem."""

    return logging.getLogger(name)
