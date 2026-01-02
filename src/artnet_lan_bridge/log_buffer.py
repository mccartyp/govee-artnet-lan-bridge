"""In-memory circular buffer for log entries with search and filtering."""

from __future__ import annotations

import asyncio
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Deque, List, Optional, Set


@dataclass
class LogEntry:
    """Structured log entry."""

    timestamp: str
    level: str
    logger: str
    message: str
    extra: dict[str, Any]

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> LogEntry:
        """Create LogEntry from log record dict."""
        return cls(
            timestamp=record.get("ts", datetime.now(tz=timezone.utc).isoformat()),
            level=record.get("level", "INFO"),
            logger=record.get("logger", ""),
            message=record.get("message", ""),
            extra={k: v for k, v in record.items() if k not in ("ts", "level", "logger", "message")},
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "timestamp": self.timestamp,
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
        }
        if self.extra:
            result["extra"] = self.extra
        return result

    def matches_filter(
        self,
        level: Optional[str] = None,
        logger: Optional[str] = None,
    ) -> bool:
        """Check if entry matches filter criteria."""
        if level and self.level != level:
            return False
        if logger and not self.logger.startswith(logger):
            return False
        return True

    def matches_search(
        self,
        pattern: str,
        regex: bool = False,
        case_sensitive: bool = False,
    ) -> bool:
        """Check if entry matches search pattern."""
        search_text = f"{self.logger} {self.message}"
        if self.extra:
            search_text += " " + str(self.extra)

        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                return bool(re.search(pattern, search_text, flags))
            except re.error:
                return False
        else:
            if not case_sensitive:
                search_text = search_text.lower()
                pattern = pattern.lower()
            return pattern in search_text


class LogBuffer:
    """Thread-safe circular buffer for recent log entries."""

    def __init__(self, max_size: int = 10000):
        """
        Initialize log buffer.

        Args:
            max_size: Maximum number of log entries to retain
        """
        self.max_size = max_size
        self._buffer: Deque[LogEntry] = deque(maxlen=max_size)
        self._lock = asyncio.Lock()
        self._subscribers: Set[Callable[[LogEntry], None]] = set()

    async def append(self, entry: LogEntry) -> None:
        """
        Add log entry to buffer and notify subscribers.

        Args:
            entry: Log entry to add
        """
        async with self._lock:
            self._buffer.append(entry)

        # Notify subscribers (don't hold lock during notifications)
        for callback in list(self._subscribers):
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback(entry))
                else:
                    callback(entry)
            except Exception:
                # Ignore subscriber errors to prevent one bad subscriber from breaking others
                pass

    async def query(
        self,
        lines: int = 100,
        level: Optional[str] = None,
        logger: Optional[str] = None,
        offset: int = 0,
    ) -> tuple[List[LogEntry], int]:
        """
        Query log entries with optional filters.

        Args:
            lines: Maximum number of entries to return
            level: Filter by log level (exact match)
            logger: Filter by logger name (prefix match)
            offset: Skip first N entries

        Returns:
            Tuple of (filtered entries, total count before pagination)
        """
        async with self._lock:
            # Convert to list for slicing
            all_entries = list(self._buffer)

        # Apply filters
        if level or logger:
            filtered = [e for e in all_entries if e.matches_filter(level, logger)]
        else:
            filtered = all_entries

        total = len(filtered)

        # Apply pagination
        start = min(offset, total)
        end = min(start + lines, total)
        result = filtered[start:end]

        return result, total

    async def search(
        self,
        pattern: str,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> List[LogEntry]:
        """
        Search log entries by pattern.

        Args:
            pattern: Search pattern (string or regex)
            regex: Use regex matching
            case_sensitive: Case-sensitive search
            max_results: Maximum results to return

        Returns:
            List of matching log entries
        """
        async with self._lock:
            all_entries = list(self._buffer)

        results = []
        for entry in all_entries:
            if entry.matches_search(pattern, regex, case_sensitive):
                results.append(entry)
                if len(results) >= max_results:
                    break

        return results

    async def subscribe(self, callback: Callable[[LogEntry], None]) -> Callable[[], None]:
        """
        Subscribe to new log entries.

        Args:
            callback: Function to call for each new log entry

        Returns:
            Unsubscribe function
        """
        self._subscribers.add(callback)

        def unsubscribe() -> None:
            self._subscribers.discard(callback)

        return unsubscribe

    async def clear(self) -> None:
        """Clear all log entries from buffer."""
        async with self._lock:
            self._buffer.clear()

    async def size(self) -> int:
        """Get current number of entries in buffer."""
        async with self._lock:
            return len(self._buffer)

    async def get_all(self) -> List[LogEntry]:
        """Get all log entries (useful for export)."""
        async with self._lock:
            return list(self._buffer)
