"""Controllers for real-time shell features.

This module contains controllers for real-time features:
- ConnectionState: WebSocket connection state enum
- LogTailController: Real-time log streaming via WebSocket
- WatchController: Periodic refresh of watch targets
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

import websockets
from prompt_toolkit.document import Document

if TYPE_CHECKING:
    from prompt_toolkit import Application
    from prompt_toolkit.buffer import Buffer
    from ..shell.core import GoveeShell


class ConnectionState(Enum):
    """WebSocket connection states for log tailing."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class LogTailController:
    """
    Controller for real-time log tailing via WebSocket.

    Features:
    - Async WebSocket connection management
    - Automatic reconnection with exponential backoff
    - Filter management (level, logger)
    - Batched UI updates for performance
    - Memory-limited buffer (last 500k chars)
    - Follow-tail mode with manual scroll detection
    """

    # Performance tuning
    MAX_BUFFER_CHARS = 500_000  # ~500KB of log text
    BATCH_INTERVAL = 0.1  # 100ms batching interval
    MAX_RECONNECT_DELAY = 10.0  # Max backoff delay

    def __init__(self, app: Application, log_buffer: Buffer, server_url: str):
        """
        Initialize the log tail controller.

        Args:
            app: The prompt_toolkit Application instance
            log_buffer: Buffer to append log lines to
            server_url: Base HTTP server URL (will be converted to WebSocket)
        """
        self.app = app
        self.log_buffer = log_buffer
        self.server_url = server_url

        # Connection state
        self.state = ConnectionState.DISCONNECTED
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.ws_task: Optional[asyncio.Task] = None
        self.batch_task: Optional[asyncio.Task] = None

        # Filter state
        self.level_filter: Optional[str] = None
        self.logger_filter: Optional[str] = None

        # Follow-tail mode (auto-scroll to newest)
        self.follow_tail = True

        # Pending log lines for batched updates
        self._pending_lines: deque[str] = deque()
        self._lock = asyncio.Lock()

        # Reconnection state
        self._reconnect_delay = 1.0
        self._should_reconnect = True

    @property
    def is_active(self) -> bool:
        """Check if log tailing is currently active."""
        return self.ws_task is not None and not self.ws_task.done()

    @property
    def ws_url(self) -> str:
        """Get the WebSocket URL for log streaming."""
        url = self.server_url.replace("http://", "ws://").replace("https://", "wss://")
        return f"{url}/logs/stream"

    async def start(self, level: Optional[str] = None, logger: Optional[str] = None) -> None:
        """
        Start log tailing with optional filters.

        Args:
            level: Log level filter (e.g., "INFO", "ERROR")
            logger: Logger name filter (e.g., "govee.discovery")
        """
        if self.is_active:
            return

        self.level_filter = level
        self.logger_filter = logger
        self._should_reconnect = True
        self._reconnect_delay = 1.0

        # Start WebSocket connection task
        self.ws_task = asyncio.create_task(self._ws_loop())

        # Start UI batch update task
        self.batch_task = asyncio.create_task(self._batch_update_loop())

    async def stop(self) -> None:
        """Stop log tailing and close WebSocket connection."""
        self._should_reconnect = False

        # Cancel tasks
        if self.ws_task and not self.ws_task.done():
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass

        if self.batch_task and not self.batch_task.done():
            self.batch_task.cancel()
            try:
                await self.batch_task
            except asyncio.CancelledError:
                pass

        # Close WebSocket
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        self.state = ConnectionState.DISCONNECTED
        self.ws_task = None
        self.batch_task = None

    async def set_filters(self, level: Optional[str] = None, logger: Optional[str] = None) -> None:
        """
        Update filters and send to server.

        Args:
            level: Log level filter (None to clear)
            logger: Logger name filter (None to clear)
        """
        self.level_filter = level
        self.logger_filter = logger

        # Send filter update to WebSocket if connected
        if self.websocket and self.state == ConnectionState.CONNECTED:
            try:
                filters = {}
                if level:
                    filters["level"] = level
                if logger:
                    filters["logger"] = logger

                await self.websocket.send(json.dumps(filters))
            except Exception:
                pass  # Will reconnect if needed

    async def clear_filters(self) -> None:
        """Clear all filters."""
        await self.set_filters(level=None, logger=None)

    def append_log_line(self, line: str) -> None:
        """
        Append a log line to the pending queue for batched UI update.

        Args:
            line: Formatted log line to append
        """
        self._pending_lines.append(line)

    def toggle_follow_tail(self) -> bool:
        """
        Toggle follow-tail mode.

        Returns:
            New follow_tail state
        """
        self.follow_tail = not self.follow_tail
        if self.follow_tail:
            # Jump to bottom
            self.log_buffer.cursor_position = len(self.log_buffer.text)
        return self.follow_tail

    def enable_follow_tail(self) -> None:
        """Enable follow-tail mode and jump to bottom."""
        self.follow_tail = True
        self.log_buffer.cursor_position = len(self.log_buffer.text)

    async def _ws_loop(self) -> None:
        """Main WebSocket connection loop with reconnection."""
        while self._should_reconnect:
            try:
                self.state = ConnectionState.CONNECTING
                self.app.invalidate()

                # Connect to WebSocket
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                ) as websocket:
                    self.websocket = websocket
                    self.state = ConnectionState.CONNECTED
                    self._reconnect_delay = 1.0  # Reset backoff on successful connect
                    self.app.invalidate()

                    # Send initial filters if set
                    if self.level_filter or self.logger_filter:
                        filters = {}
                        if self.level_filter:
                            filters["level"] = self.level_filter
                        if self.logger_filter:
                            filters["logger"] = self.logger_filter
                        await websocket.send(json.dumps(filters))

                    # Receive and process log messages
                    async for message in websocket:
                        try:
                            data = json.loads(message)

                            # Skip ping messages
                            if data.get("type") == "ping":
                                continue

                            # Format log entry
                            timestamp = data.get("timestamp", "")
                            level = data.get("level", "INFO")
                            logger_name = data.get("logger", "")
                            message_text = data.get("message", "")

                            # Format with colors (ANSI codes)
                            # Timestamp: dim white
                            # Level: color-coded
                            # Logger: cyan
                            # Message: default
                            level_colors = {
                                "DEBUG": "\033[36m",    # Cyan
                                "INFO": "\033[32m",     # Green
                                "WARNING": "\033[33m",  # Yellow
                                "ERROR": "\033[31m",    # Red
                                "CRITICAL": "\033[1;31m",  # Bold red
                            }
                            level_color = level_colors.get(level, "\033[37m")
                            reset = "\033[0m"
                            dim = "\033[2m"
                            cyan = "\033[36m"

                            formatted_line = (
                                f"{dim}{timestamp}{reset} "
                                f"{level_color}{level:<8}{reset} "
                                f"{cyan}{logger_name}{reset}: "
                                f"{message_text}\n"
                            )

                            self.append_log_line(formatted_line)

                        except json.JSONDecodeError:
                            continue
                        except Exception as exc:
                            # Log parsing errors shouldn't crash the loop
                            self.append_log_line(f"\033[31mError parsing log: {exc}\033[0m\n")

            except asyncio.CancelledError:
                break
            except Exception as exc:
                # Connection failed, set state and reconnect with backoff
                if self._should_reconnect:
                    self.state = ConnectionState.RECONNECTING
                    self.websocket = None
                    self.app.invalidate()

                    # Exponential backoff: 1s -> 2s -> 4s -> 8s -> 10s (max)
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(self._reconnect_delay * 2, self.MAX_RECONNECT_DELAY)
                else:
                    break

        self.state = ConnectionState.DISCONNECTED
        self.websocket = None
        self.app.invalidate()

    async def _batch_update_loop(self) -> None:
        """Batch UI updates every BATCH_INTERVAL to reduce redraw frequency."""
        try:
            while True:
                await asyncio.sleep(self.BATCH_INTERVAL)

                if self._pending_lines:
                    async with self._lock:
                        # Collect all pending lines
                        lines_to_add = "".join(self._pending_lines)
                        self._pending_lines.clear()

                    # Append to buffer
                    if lines_to_add:
                        # Get current buffer text
                        current_text = self.log_buffer.text
                        new_text = current_text + lines_to_add

                        # Trim buffer if exceeding max size
                        if len(new_text) > self.MAX_BUFFER_CHARS:
                            # Keep only the last MAX_BUFFER_CHARS characters
                            # Try to cut at a newline boundary
                            trim_point = len(new_text) - self.MAX_BUFFER_CHARS
                            newline_pos = new_text.find('\n', trim_point)
                            if newline_pos != -1:
                                new_text = new_text[newline_pos + 1:]
                            else:
                                new_text = new_text[trim_point:]

                        # Update buffer
                        self.log_buffer.set_document(
                            Document(text=new_text, cursor_position=len(new_text) if self.follow_tail else self.log_buffer.cursor_position),
                            bypass_readonly=True
                        )

                        # Invalidate UI
                        self.app.invalidate()

        except asyncio.CancelledError:
            pass


class WatchController:
    """
    Controller for periodic watch updates with overlay window.

    Features:
    - Periodic refresh of watch targets (devices, mappings, dashboard, logs)
    - Clear and redraw overlay window at each refresh
    - Configurable refresh interval
    - Support for multiple watch targets
    """

    # Default refresh interval
    DEFAULT_REFRESH_INTERVAL = 5.0  # 5 seconds

    def __init__(self, app: Application, watch_buffer: Buffer, shell: GoveeShell):
        """
        Initialize the watch controller.

        Args:
            app: The prompt_toolkit Application instance
            watch_buffer: Buffer to display watch output
            shell: Reference to GoveeShell instance for executing commands
        """
        self.app = app
        self.watch_buffer = watch_buffer
        self.shell = shell

        # Watch state
        self.watch_target: Optional[str] = None
        self.refresh_interval = self.DEFAULT_REFRESH_INTERVAL
        self.watch_task: Optional[asyncio.Task] = None
        self._should_watch = False

    @property
    def is_active(self) -> bool:
        """Check if watch is currently active."""
        return self.watch_task is not None and not self.watch_task.done()

    async def start(self, target: str, interval: float = 5.0) -> None:
        """
        Start watching a target with periodic refreshes.

        Args:
            target: Watch target (devices, mappings, dashboard, logs)
            interval: Refresh interval in seconds
        """
        if self.is_active:
            return

        self.watch_target = target
        self.refresh_interval = interval
        self._should_watch = True

        # Start watch loop task
        self.watch_task = asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        """Stop watching and cancel the watch loop."""
        self._should_watch = False

        # Cancel task
        if self.watch_task and not self.watch_task.done():
            self.watch_task.cancel()
            try:
                await self.watch_task
            except asyncio.CancelledError:
                pass

        self.watch_task = None
        self.watch_target = None

    def set_interval(self, interval: float) -> None:
        """
        Update the refresh interval.

        Args:
            interval: New refresh interval in seconds
        """
        self.refresh_interval = max(0.5, interval)  # Minimum 0.5s to prevent hammering

    async def _watch_loop(self) -> None:
        """Main watch loop - periodically refresh the watch target."""
        try:
            while self._should_watch:
                # Clear the watch buffer before refresh
                self.watch_buffer.set_document(Document(""), bypass_readonly=True)

                # Capture output for this refresh cycle
                output = ""

                # Add timestamp header
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                output += f"\033[1;36m╔═══════════════════════════════════════════════════════════╗\033[0m\n"
                output += f"\033[1;36m║  Watch Mode - {self.watch_target.upper():<43} ║\033[0m\n"
                output += f"\033[1;36m║  Refreshed at {timestamp:<43} ║\033[0m\n"
                output += f"\033[1;36m╚═══════════════════════════════════════════════════════════╝\033[0m\n\n"

                # Execute the watch command and capture output
                try:
                    # Save current output buffer position
                    old_output = self.shell.output_buffer.text

                    # Execute command based on target
                    if self.watch_target == "devices":
                        self.shell.device_handler._show_devices_simple()
                    elif self.watch_target == "mappings":
                        self.shell.mapping_handler._show_mappings_list()
                    elif self.watch_target == "logs":
                        self.shell.do_logs("")
                    elif self.watch_target == "dashboard":
                        self.shell.monitoring_handler._monitor_dashboard()

                    # Capture new output added to output buffer
                    new_output = self.shell.output_buffer.text
                    if len(new_output) > len(old_output):
                        # Extract only the new content
                        command_output = new_output[len(old_output):]
                        output += command_output

                        # Reset output buffer to old content (since we're showing it in watch window)
                        self.shell.output_buffer.set_document(
                            Document(text=old_output),
                            bypass_readonly=True
                        )

                except Exception as exc:
                    output += f"\033[31mError executing watch command: {exc}\033[0m\n"

                # Update watch buffer with new content
                self.watch_buffer.set_document(
                    Document(text=output, cursor_position=0),
                    bypass_readonly=True
                )

                # Invalidate UI to trigger redraw
                self.app.invalidate()

                # Wait for next refresh
                await asyncio.sleep(self.refresh_interval)

        except asyncio.CancelledError:
            pass
