"""Structured logging helpers."""

from __future__ import annotations

import json
import logging
import logging.config
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping

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


_REDACT_KEYS = {"authorization", "x-api-key", "cookie"}


class BufferHandler(logging.Handler):
    """Logging handler that feeds log entries to a LogBuffer."""

    def __init__(self, log_buffer: Any) -> None:
        """
        Initialize buffer handler.

        Args:
            log_buffer: LogBuffer instance to feed log entries to
        """
        super().__init__()
        self.log_buffer = log_buffer

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record to the buffer.

        Args:
            record: Log record to emit
        """
        try:
            # Import here to avoid circular dependency
            from .log_buffer import LogEntry
            import asyncio

            # Build log entry data
            entry_data = {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }

            # Add exception info if present
            if record.exc_info:
                entry_data["exc_info"] = self.format(record)

            # Add extra fields
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
                    "levelname",
                }:
                    continue
                entry_data[key] = value

            # Create log entry and append to buffer
            entry = LogEntry.from_record(entry_data)

            # Try to add to buffer asynchronously
            try:
                loop = asyncio.get_running_loop()
                asyncio.create_task(self.log_buffer.append(entry))
            except RuntimeError:
                # No event loop running, skip buffer update
                pass
        except Exception:
            # Silently ignore errors to prevent logging from breaking the app
            pass


def redact_mapping(values: Mapping[str, Any], extra_keys: Iterable[str] = ()) -> Dict[str, Any]:
    """Return a shallow copy of `values` with sensitive keys redacted."""

    redacted: Dict[str, Any] = {}
    redact_keys = {key.lower() for key in _REDACT_KEYS} | {key.lower() for key in extra_keys}
    for key, value in values.items():
        if key.lower() in redact_keys:
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted


def configure_logging(config: Config, log_buffer: Any = None) -> None:
    """
    Configure global logging based on the provided config.

    Args:
        config: Configuration object
        log_buffer: Optional LogBuffer instance to attach as a handler
    """
    level = config.log_level.upper()
    discovery_level = (config.discovery_log_level or config.log_level).upper()
    artnet_level = (config.artnet_log_level or config.log_level).upper()
    sender_level = (config.sender_log_level or config.log_level).upper()
    api_level = (config.api_log_level or config.log_level).upper()
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
                "govee.metrics": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.discovery": {
                    "level": discovery_level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.rate_limit": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.artnet": {
                    "level": artnet_level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.artnet.protocol": {
                    "level": artnet_level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.artnet.mapping": {
                    "level": artnet_level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.devices": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.api": {
                    "level": api_level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.api.middleware": {
                    "level": api_level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.discovery.protocol": {
                    "level": discovery_level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "govee.sender": {
                    "level": sender_level,
                    "handlers": ["console"],
                    "propagate": False,
                },
            },
            "root": {"level": level, "handlers": ["console"]},
        }
    )

    # Add buffer handler to all govee loggers if provided
    if log_buffer is not None and config.log_buffer_enabled:
        buffer_handler = BufferHandler(log_buffer)
        buffer_handler.setLevel(level)

        # Add to all govee loggers
        for logger_name in [
            "govee",
            "govee.metrics",
            "govee.discovery",
            "govee.rate_limit",
            "govee.artnet",
            "govee.artnet.protocol",
            "govee.artnet.mapping",
            "govee.devices",
            "govee.api",
            "govee.api.middleware",
            "govee.discovery.protocol",
            "govee.sender",
        ]:
            logger = logging.getLogger(logger_name)
            logger.addHandler(buffer_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger for the requested subsystem."""

    return logging.getLogger(name)
