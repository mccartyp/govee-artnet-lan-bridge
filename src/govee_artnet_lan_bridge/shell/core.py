"""Core shell module for govee-artnet CLI.

This is the main shell class that coordinates all command handlers
and manages the interactive terminal interface.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import signal
import sys
import time
from collections import deque
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import websockets
import yaml
from prompt_toolkit import Application
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document, Document as PTDocument
from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.history import FileHistory
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..cli import (
    ClientConfig,
    _api_delete,
    _api_get,
    _api_get_by_id,
    _build_client,
    _device_set_enabled,
    _handle_response,
    _print_output,
)

from .ui_components import (
    ANSILexer,
    ResponseCache,
    TrailingSpaceCompleter,
    TOOLBAR_STYLE,
    DEFAULT_CACHE_TTL,
    FIELD_DESCRIPTIONS,
)

from .controllers import (
    ConnectionState,
    LogTailController,
    WatchController,
)

from .command_handlers.devices import DeviceCommandHandler
from .command_handlers.mappings import MappingCommandHandler
from .command_handlers.monitoring import MonitoringCommandHandler
from .command_handlers.config import ConfigCommandHandler

from .layout_builder import LayoutBuilder
from .keybindings import KeyBindingManager
from .toolbar import ToolbarManager


# Shell version
SHELL_VERSION = "1.0.0"

# Shell configuration constants
DEFAULT_WATCH_INTERVAL = 2.0
DEFAULT_API_TIMEOUT = 10.0
WS_RECV_TIMEOUT = 1.0
DEFAULT_LOG_LINES = 50

class GoveeShell:
    """Interactive shell for the Govee ArtNet bridge using prompt_toolkit."""

    prompt = "govee> "

    def __init__(self, config: ClientConfig):
        """
        Initialize the shell.

        Args:
            config: Client configuration
        """
        self.config = config
        self.client: Optional[httpx.Client] = None
        # Configure console for formatting output to buffers (not terminal)
        self.console = Console(legacy_windows=False, soft_wrap=False, force_terminal=True)

        # Initialize response cache for performance
        cache_ttl = float(os.environ.get("GOVEE_ARTNET_CACHE_TTL", str(DEFAULT_CACHE_TTL)))
        self.cache = ResponseCache(default_ttl=cache_ttl)

        # Track previous data for delta detection in watch mode
        self.previous_data: dict[str, Any] = {}

        # Initialize toolbar manager
        self.toolbar_manager = ToolbarManager(self)

        # Set up command history and data directory
        self.data_dir = Path.home() / ".govee_artnet"
        self.data_dir.mkdir(exist_ok=True)
        history_file = self.data_dir / "shell_history"
        self.bookmarks_file = self.data_dir / "bookmarks.json"
        self.aliases_file = self.data_dir / "aliases.json"
        self.config_file = self.data_dir / "shell_config.toml"

        # Load shell configuration
        self.shell_config = self._load_shell_config()

        # Track if we're using auto pagination (for resize handling)
        # Default to True since initial page_size is auto-detected from terminal
        self.auto_pagination = True

        # Apply default output format and pagination from config if not already set
        default_output = self.shell_config.get("shell", {}).get("default_output", config.output)
        default_page_size = self.shell_config.get("console", {}).get("page_size", config.page_size)

        if config.output == "json" and default_output != "json":
            # Override default output if user hasn't specified one
            self.config = ClientConfig(
                server_url=config.server_url,
                api_key=config.api_key,
                api_bearer_token=config.api_bearer_token,
                output=default_output,
                timeout=config.timeout,
                page_size=default_page_size,
            )
        elif default_page_size != config.page_size:
            # Update page size from shell config
            self.config = ClientConfig(
                server_url=config.server_url,
                api_key=config.api_key,
                api_bearer_token=config.api_bearer_token,
                output=config.output,
                timeout=config.timeout,
                page_size=default_page_size,
            )

        # Load bookmarks and aliases
        self.bookmarks = self._load_json(self.bookmarks_file, {})
        self.aliases = self._load_json(self.aliases_file, {})

        # Command dispatch table
        self.commands: dict[str, Callable[[str], Optional[bool]]] = {
            "connect": self.do_connect,
            "disconnect": self.do_disconnect,
            "status": self.do_status,
            "health": self.do_health,
            "devices": self.do_devices,
            "mappings": self.do_mappings,
            "channels": self.do_channels,
            "logs": self.do_logs,
            "monitor": self.do_monitor,
            "output": self.do_output,
            "bookmark": self.do_bookmark,
            "alias": self.do_alias,
            "watch": self.do_watch,
            "batch": self.do_batch,
            "session": self.do_session,
            "help": self.do_help,
            "?": self.do_help,
            "version": self.do_version,
            "tips": self.do_tips,
            "clear": self.do_clear,
            "exit": self.do_exit,
            "quit": self.do_quit,
            "EOF": self.do_EOF,
        }

        # Create output buffer (read-only buffer for displaying command output with ANSI colors)
        self.output_buffer = Buffer(
            read_only=True,
            multiline=True,
        )

        # Follow-tail mode: auto-scroll to bottom when new output is added
        self.follow_tail = True

        # Create log tail buffer for real-time log streaming
        self.log_tail_buffer = Buffer(
            read_only=True,
            multiline=True,
        )

        # Log tail mode state
        self.in_log_tail_mode = False

        # Log tail controller (will be initialized after app is created)
        self.log_tail_controller: Optional[LogTailController] = None

        # Create watch buffer for periodic watch updates
        self.watch_buffer = Buffer(
            read_only=True,
            multiline=True,
        )

        # Watch mode state
        self.in_watch_mode = False

        # Watch controller (will be initialized after app is created)
        self.watch_controller: Optional[WatchController] = None

        # Set up multi-level autocomplete with command structure
        completer_dict = {
            'connect': None,
            'disconnect': None,
            'status': None,
            'health': {'detailed': None},
            'devices': {
                'list': {
                    'detailed': None,
                    '--id': None,
                    '--ip': None,
                    '--state': {'active': None, 'disabled': None, 'offline': None}
                },
                'enable': None,
                'disable': None,
                'set-name': None
            },
            'mappings': {
                'list': None,
                'get': None,
                'create': {
                    '--device-id': None,
                    '--universe': None,
                    '--template': {'rgb': None, 'rgbw': None, 'brightness_rgb': None, 'rgbwa': None, 'rgbaw': None, 'brgbwct': None},
                    '--start-channel': None,
                    '--channel': None,
                    '--length': None,
                    '--type': {'range': None, 'discrete': None},
                    '--field': {'power': None, 'brightness': None, 'r': None, 'red': None, 'g': None, 'green': None, 'b': None, 'blue': None, 'w': None, 'white': None, 'ct': None, 'color_temp': None},
                    '--allow-overlap': None,
                    '--help': None,
                },
                'delete': None,
                'channel-map': None
            },
            'channels': {'list': None},
            'logs': {
                'stats': None,
                'tail': {
                    '--level': None,
                    '--logger': None,
                },
                'search': {
                    '--regex': None,
                    '--case-sensitive': None,
                    '--lines': None,
                },
            },
            'monitor': {'status': None, 'dashboard': None},
            'output': {'json': None, 'table': None, 'yaml': None},
            'bookmark': {'add': None, 'list': None, 'delete': None, 'use': None},
            'alias': {'add': None, 'list': None, 'delete': None, 'clear': None},
            'watch': {
                'devices': {'--interval': None},
                'mappings': {'--interval': None},
                'logs': {'--interval': None},
                'dashboard': {'--interval': None},
            },
            'batch': {'load': None},
            'session': {'save': None, 'list': None, 'delete': None},
            'help': None,
            '?': None,
            'version': None,
            'tips': None,
            'clear': None,
            'exit': None,
            'quit': None,
        }
        completer = TrailingSpaceCompleter(completer_dict)

        # Create input buffer with history and autocomplete
        self.input_buffer = Buffer(
            completer=completer,
            complete_while_typing=True,
            history=FileHistory(str(history_file)),
            multiline=False,
            accept_handler=self._accept_input,
        )

        # Set up key bindings
        keybinding_manager = KeyBindingManager(self)
        kb = keybinding_manager.create_key_bindings()

        # Create layout with output pane, separator, prompt + input field, and toolbar
        layout_builder = LayoutBuilder(self)
        self.app: Application = layout_builder.build_layout_and_app(kb)

        # Initialize log tail controller now that app is created
        self.log_tail_controller = LogTailController(
            app=self.app,
            log_buffer=self.log_tail_buffer,
            server_url=config.server_url,
        )

        # Initialize watch controller now that app is created
        self.watch_controller = WatchController(
            app=self.app,
            watch_buffer=self.watch_buffer,
            shell=self,
        )

        # Set up terminal resize handler for pagination (prompt_toolkit handles layout resize)
        if hasattr(signal, 'SIGWINCH'):
            signal.signal(signal.SIGWINCH, self._handle_terminal_resize)


        # Initialize command handlers
        self.device_handler = DeviceCommandHandler(self)
        self.mapping_handler = MappingCommandHandler(self)
        self.monitoring_handler = MonitoringCommandHandler(self)
        self.config_handler = ConfigCommandHandler(self)

        self._connect()

    def _load_json(self, file_path: Path, default: Any) -> Any:
        """Load JSON data from file with fallback to default."""
        try:
            if file_path.exists():
                with open(file_path, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _save_json(self, file_path: Path, data: Any) -> None:
        """Save JSON data to file."""
        try:
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            self._append_output(f"[red]Error saving to {file_path}: {exc}[/]" + "\n")

    def _resolve_bookmark(self, value: str) -> str:
        """
        Resolve bookmark reference to actual value.

        Args:
            value: Value that may be a bookmark reference (starts with @) or literal value

        Returns:
            The resolved value from bookmarks, or the original value if not a bookmark
        """
        if value.startswith("@"):
            bookmark_name = value[1:]  # Remove @ prefix
            if bookmark_name in self.bookmarks:
                return self.bookmarks[bookmark_name]
            else:
                self._append_output(f"[yellow]Warning: Bookmark '@{bookmark_name}' not found, using literal value[/]\n")
                return value
        return value

    def _load_shell_config(self) -> dict[str, Any]:
        """
        Load shell configuration from TOML file.

        Returns:
            Configuration dictionary with defaults
        """
        # Auto-detect terminal height for default pagination
        # Reserve space for: toolbar (3 lines) + prompt (1 line) + pagination prompt (1 line)
        import shutil
        terminal_height = shutil.get_terminal_size().lines
        default_page_size = max(10, terminal_height - 5)

        defaults = {
            "shell": {
                "default_output": "table",
                "history_size": 1000,
                "autocomplete": True,
            },
            "connection": {
                "timeout": 10.0,
            },
            "monitoring": {
                "watch_interval": 2.0,
                "log_lines": 50,
            },
            "appearance": {
                "colors": True,
                "timestamps": False,
            },
            "console": {
                "page_size": default_page_size,  # Auto-detected based on terminal height, set to None to disable
            },
        }

        if not self.config_file.exists():
            return defaults

        try:
            import tomllib  # Python 3.11+
        except ImportError:
            try:
                import tomli as tomllib  # Fallback for Python < 3.11
            except ImportError:
                # TOML library not available, use defaults
                return defaults

        try:
            with open(self.config_file, "rb") as f:
                config = tomllib.load(f)
            # Merge with defaults (config file values override defaults)
            for section, values in config.items():
                if section in defaults:
                    defaults[section].update(values)
                else:
                    defaults[section] = values
            return defaults
        except Exception:
            # If config file is invalid, use defaults
            return defaults

    def _append_output(self, text: str) -> None:
        """
        Append text to the output buffer using Rich formatting.

        Args:
            text: Text to append (supports Rich markup and Rich objects like Table)
        """
        # Use Rich console to format the text to ANSI
        buffer = StringIO()
        temp_console = Console(
            file=buffer,
            force_terminal=True,
            width=self.console.width,
            legacy_windows=False,
            soft_wrap=False,
        )
        temp_console.print(text, end="")

        # Get current content and append new text
        current_text = self.output_buffer.text
        formatted_text = buffer.getvalue()
        new_text = current_text + formatted_text

        # Update buffer document
        # If follow-tail is enabled, move cursor to end for auto-scroll
        # Otherwise, keep cursor at current position to allow manual scrolling
        if self.follow_tail:
            cursor_position = len(new_text)
        else:
            # Keep cursor at its current position
            cursor_position = min(self.output_buffer.cursor_position, len(new_text))

        self.output_buffer.set_document(
            Document(text=new_text, cursor_position=cursor_position),
            bypass_readonly=True
        )

        # Trigger redraw
        self.app.invalidate()

    async def _enter_log_tail_mode(self, level: Optional[str] = None, logger: Optional[str] = None) -> None:
        """
        Enter log tail mode and start streaming logs.

        Args:
            level: Optional log level filter
            logger: Optional logger name filter
        """
        if self.in_log_tail_mode:
            return

        # Clear log tail buffer
        self.log_tail_buffer.set_document(Document(""), bypass_readonly=True)

        # Show entering message
        enter_msg = "\033[1;36m╔═══════════════════════════════════════════════════════════╗\033[0m\n"
        enter_msg += "\033[1;36m║           Log Tail Mode - Real-time Log Stream           ║\033[0m\n"
        enter_msg += "\033[1;36m╚═══════════════════════════════════════════════════════════╝\033[0m\n"
        if level:
            enter_msg += f"\033[33mLevel filter: {level}\033[0m\n"
        if logger:
            enter_msg += f"\033[33mLogger filter: {logger}\033[0m\n"
        enter_msg += "\033[2mConnecting to log stream...\033[0m\n\n"

        self.log_tail_buffer.set_document(
            Document(text=enter_msg, cursor_position=len(enter_msg)),
            bypass_readonly=True
        )

        # Switch to log tail mode
        self.in_log_tail_mode = True
        self.app.invalidate()

        # Start log tail controller
        await self.log_tail_controller.start(level=level, logger=logger)

    async def _exit_log_tail_mode(self) -> None:
        """Exit log tail mode and return to normal shell view."""
        if not self.in_log_tail_mode:
            return

        # Stop log tail controller
        await self.log_tail_controller.stop()

        # Switch back to normal mode
        self.in_log_tail_mode = False
        self.app.invalidate()

        # Show exit message in normal output
        self._append_output("\n[dim]Exited log tail mode[/]\n")

    async def _enter_watch_mode(self, target: str, interval: float = 5.0) -> None:
        """
        Enter watch mode and start periodic refreshes.

        Args:
            target: Watch target (devices, mappings, dashboard, logs)
            interval: Refresh interval in seconds
        """
        if self.in_watch_mode:
            return

        # Clear watch buffer
        self.watch_buffer.set_document(Document(""), bypass_readonly=True)

        # Show entering message
        enter_msg = "\033[1;36m╔═══════════════════════════════════════════════════════════╗\033[0m\n"
        enter_msg += f"\033[1;36m║           Watch Mode - {target.upper():<35} ║\033[0m\n"
        enter_msg += "\033[1;36m╚═══════════════════════════════════════════════════════════╝\033[0m\n"
        enter_msg += f"\033[33mRefresh interval: {interval}s\033[0m\n"
        enter_msg += "\033[2mStarting watch...\033[0m\n\n"

        self.watch_buffer.set_document(
            Document(text=enter_msg, cursor_position=len(enter_msg)),
            bypass_readonly=True
        )

        # Switch to watch mode
        self.in_watch_mode = True
        self.app.invalidate()

        # Start watch controller
        await self.watch_controller.start(target=target, interval=interval)

    async def _exit_watch_mode(self) -> None:
        """Exit watch mode and return to normal shell view."""
        if not self.in_watch_mode:
            return

        # Stop watch controller
        await self.watch_controller.stop()

        # Switch back to normal mode
        self.in_watch_mode = False
        self.app.invalidate()

        # Show exit message in normal output
        self._append_output("\n[dim]Exited watch mode[/]\n")

    def _accept_input(self, buffer: Buffer) -> bool:
        """
        Handle command input when user presses Enter.

        Args:
            buffer: Input buffer

        Returns:
            True to clear buffer and save to history, False to keep buffer text
        """
        # Get command text
        line = buffer.text

        # Process command
        if line and not line.isspace():
            # Echo the command with prompt (only for non-empty commands)
            self._append_output(f"{self.prompt}{line}\n")
            # Preprocess (aliases)
            line = self.precmd(line)

            # Execute command
            stop = self.onecmd(line)

            # Handle exit
            if stop:
                self.app.exit(result=True)

        # Manually save to history and clear buffer
        # This ensures both operations happen in the correct order
        buffer.append_to_history()
        buffer.reset()
        return False  # We've already handled clearing the buffer

    def _connect(self) -> None:
        """Establish connection to the bridge server."""
        try:
            self.client = _build_client(self.config)
            # Test connection
            response = self.client.get("/health")
            response.raise_for_status()
            self._append_output(f"[green]Connected to {self.config.server_url}[/]\n")
        except Exception as exc:
            self._append_output(f"[yellow]Warning: Could not connect to {self.config.server_url}: {exc}[/]\n")
            self._append_output("[dim]Some commands may not work. Use 'connect' to retry.[/]\n")
            self.client = None

    def _handle_terminal_resize(self, signum: int, frame: Any) -> None:
        """
        Handle terminal window resize events (SIGWINCH).

        Updates pagination size when auto-pagination is enabled.

        Args:
            signum: Signal number
            frame: Current stack frame
        """
        if self.auto_pagination:
            import shutil
            terminal_height = shutil.get_terminal_size().lines
            # Reserve space for: toolbar (3 lines) + prompt (1 line) + pagination prompt (1 line)
            new_page_size = max(10, terminal_height - 5)

            # Update config with new page size
            self.config = ClientConfig(
                server_url=self.config.server_url,
                api_key=self.config.api_key,
                api_bearer_token=self.config.api_bearer_token,
                output=self.config.output,
                timeout=self.config.timeout,
                page_size=new_page_size,
            )

    def _get_bottom_toolbar(self) -> list[tuple[str, str]]:
        """Get toolbar fragments from toolbar manager."""
        return self.toolbar_manager.get_toolbar_fragments()

    def _handle_error(self, exc: Exception, context: str = "") -> None:
        """
        Centralized error handler with consistent formatting.

        Args:
            exc: The exception that occurred
            context: Optional context string (e.g., "devices list")
        """
        if isinstance(exc, httpx.HTTPStatusError):
            self._append_output(
                f"[bold red]HTTP {exc.response.status_code}:[/] {exc.response.text}\n"
            )
        elif isinstance(exc, httpx.RequestError):
            self._append_output(f"[bold red]Connection Error:[/] {exc}" + "\n")
        else:
            context_str = f" in {context}" if context else ""
            self._append_output(f"[bold red]Error{context_str}:[/] {exc}" + "\n")

    def _capture_api_output(self, api_func: Callable, *args, **kwargs) -> Any:
        """
        Capture output from API functions that print directly to stdout.

        Args:
            api_func: API function to call (e.g., _api_get)
            *args: Positional arguments for the API function
            **kwargs: Keyword arguments for the API function

        Returns:
            Return value from the API function
        """
        import sys
        from io import StringIO

        # Save original stdout
        old_stdout = sys.stdout
        captured_output = StringIO()

        try:
            # Redirect stdout to capture
            sys.stdout = captured_output
            result = api_func(*args, **kwargs)

            # Get captured output and route through _append_output
            output = captured_output.getvalue()
            if output:
                self._append_output(output)

            return result
        finally:
            # Restore stdout
            sys.stdout = old_stdout

    def _cached_get(self, endpoint: str, use_cache: bool = True) -> httpx.Response:
        """
        Perform a GET request with optional caching.

        Args:
            endpoint: API endpoint path
            use_cache: Whether to use cache (default: True)

        Returns:
            httpx.Response object

        Raises:
            Exception: If the request fails
        """
        if not self.client:
            raise Exception("Not connected. Use 'connect' first.")

        # Check cache first
        if use_cache:
            cached = self.cache.get(endpoint)
            if cached is not None:
                # Return cached response-like object
                return cached

        # Make actual API call
        response = self.client.get(endpoint)
        response.raise_for_status()

        # Store in cache
        if use_cache:
            self.cache.set(endpoint, response)

        return response

    def _invalidate_cache(self, pattern: Optional[str] = None) -> None:
        """
        Invalidate cache entries.

        Args:
            pattern: Optional pattern to match keys (e.g., "/devices"). If None, clears all.
        """
        if pattern is None:
            self.cache.clear()
        else:
            # Remove keys matching pattern
            keys_to_remove = [k for k in self.cache.cache.keys() if pattern in k]
            for key in keys_to_remove:
                del self.cache.cache[key]
            self.cache.stats["size"] = len(self.cache.cache)

    def _format_command_help(self, command: str, docstring: str) -> str:
        """
        Format command help with colors and styling using rich.

        Args:
            command: The command name
            docstring: The command's docstring

        Returns:
            Formatted help text with ANSI color codes
        """
        from io import StringIO
        buffer = StringIO()
        temp_console = Console(file=buffer, force_terminal=True, width=self.console.width)

        # Print header
        temp_console.print()
        temp_console.print("─" * 80, style="dim")
        temp_console.print(f"Help for command: {command}", style="bold cyan")
        temp_console.print("─" * 80, style="dim")
        temp_console.print()

        # Parse and format the docstring
        lines = docstring.strip().split("\n")
        in_usage = False
        in_examples = False

        for line in lines:
            stripped = line.strip()

            # Check for section headers
            if stripped.startswith("Usage:"):
                in_usage = True
                in_examples = False
                temp_console.print(stripped, style="bold green")
            elif stripped.startswith("Examples:"):
                in_usage = False
                in_examples = True
                temp_console.print()
                temp_console.print(stripped, style="bold green")
            elif not stripped:
                # Blank line
                temp_console.print()
                in_usage = False
                in_examples = False
            elif in_usage:
                # Usage lines - highlight command syntax
                temp_console.print(f"  {stripped}", style="yellow")
            elif in_examples:
                # Example lines - highlight examples
                temp_console.print(f"  {stripped}", style="cyan")
            else:
                # Description text
                temp_console.print(stripped, style="white")

        temp_console.print()
        return buffer.getvalue()

    def precmd(self, line: str) -> str:
        """Preprocess commands to expand aliases."""
        if not line:
            return line

        # Check if line starts with an alias
        parts = shlex.split(line) if line else []
        if parts and parts[0] in self.aliases:
            # Expand alias
            alias_value = self.aliases[parts[0]]
            expanded = alias_value + " " + " ".join(parts[1:]) if len(parts) > 1 else alias_value
            self._append_output(f"[dim](expanding: {expanded})[/]" + "\n")
            return expanded

        return line

    def do_connect(self, arg: str) -> None:
        """
        Connect or reconnect to the bridge server.
        Usage: connect [SERVER_URL]
        Examples:
            connect                           # Reconnect to current server
            connect http://localhost:8000     # Connect to specific server
            connect http://192.168.1.100:8000 # Connect to remote server
        """
        if arg:
            # Update server URL if provided
            self.config = ClientConfig(
                server_url=arg,
                api_key=self.config.api_key,
                api_bearer_token=self.config.api_bearer_token,
                output=self.config.output,
                timeout=self.config.timeout,
                page_size=self.config.page_size,
            )
        self._connect()

    def do_disconnect(self, arg: str) -> None:
        """Disconnect from the bridge server."""
        if self.client:
            self.client.close()
            self.client = None
            self._append_output("[yellow]Disconnected[/]" + "\n")

    def do_status(self, arg: str) -> None:
        """
        Show connection status and bridge status.
        Usage: status
        Displays bridge operational statistics, device counts, and queue depth.
        """
        if not self.client:
            self._append_output(f"[red]Not connected.[/] [dim]Server URL: {self.config.server_url}[/]" + "\n")
            return

        # Handle help aliases: status --help, status ?
        if arg.strip() in ("--help", "?"):
            self.do_help("status")
            return

        try:
            self._capture_api_output(_api_get, self.client, "/status", self.config)
        except Exception as exc:
            self._handle_error(exc, "status")

    def do_health(self, arg: str) -> None:
        """
        Check bridge health.
        Usage: health                # Show simplified health summary
               health detailed       # Show detailed health information
        """
        if not self.client:
            self._append_output("Not connected. Use 'connect' first.\n")
            return

        # Handle help aliases: health --help, health ?
        if arg.strip() in ("--help", "?"):
            self.do_help("health")
            return

        args = shlex.split(arg) if arg else []

        try:
            if args and args[0] == "detailed":
                # Show detailed health info (original behavior)
                self._capture_api_output(_api_get, self.client, "/health", self.config)
            else:
                # Show simplified health status
                self._show_health_summary()
        except Exception as exc:
            self._handle_error(exc, "health")

    def _show_health_summary(self) -> None:
        """Show simplified health status with component statuses."""
        try:
            response = self.client.get("/health")
            health_data = _handle_response(response)

            # Overall status
            overall_status = health_data.get("status", "unknown")
            status_style = "bold green" if overall_status == "healthy" else "bold yellow" if overall_status == "degraded" else "bold red"
            status_indicator = "✓" if overall_status == "healthy" else "⚠" if overall_status == "degraded" else "✗"

            self._append_output(f"\n[{status_style}]{status_indicator} Bridge Health: {overall_status.upper()}[/]\n\n")

            # Component status table
            subsystems = health_data.get("subsystems", {})
            if subsystems:
                table = Table(title=Text("Component Status", justify="center"), show_header=True, header_style="bold cyan", box=box.ROUNDED)
                table.add_column("Component", style="cyan", width=20)
                table.add_column("Status", style="white", width=15, justify="center")
                table.add_column("Details", style="dim", width=40)

                for name, data in subsystems.items():
                    sub_status = data.get("status", "unknown")

                    # Determine status indicator and style
                    if sub_status == "ok" or sub_status == "healthy":
                        indicator = "✓"
                        status_style = "green"
                        status_text = "Healthy"
                    elif sub_status == "degraded":
                        indicator = "⚠"
                        status_style = "yellow"
                        status_text = "Degraded"
                    else:
                        indicator = "✗"
                        status_style = "red"
                        status_text = "Error"

                    # Extract details if available
                    details = data.get("message", "") or data.get("error", "") or ""
                    if not details and isinstance(data, dict):
                        # Try to construct details from other fields
                        detail_parts = []
                        for key, value in data.items():
                            if key not in ("status", "message", "error") and value:
                                detail_parts.append(f"{key}: {value}")
                        details = ", ".join(detail_parts) if detail_parts else ""

                    table.add_row(
                        name.replace("_", " ").title(),
                        f"[{status_style}]{indicator} {status_text}[/]",
                        details[:40] if details else ""
                    )

                self._append_output(table)
            else:
                self._append_output("[dim]No component details available[/]\n")

            self._append_output(f"\n[dim]Use 'health detailed' for full health information[/]\n")

        except Exception as exc:
            self._append_output(f"[red]Error fetching health: {exc}[/]\n")

    def do_devices(self, arg: str) -> None:
        """
        Device commands: list, list detailed, enable, disable, set-name, set-capabilities.
        Usage: devices list [--id ID] [--ip IP] [--state STATE]              # Show simplified 2-line view
               devices list detailed [--id ID] [--ip IP] [--state STATE]     # Show full device details
               devices enable <device_id>
               devices disable <device_id>
               devices set-name <device_id> <name>                          # Set device name (use "" to clear)
               devices set-capabilities <device_id> --brightness <bool> --color <bool> --white <bool> --color-temp <bool>
        Examples:
            devices list
            devices list --id AA:BB:CC:DD:EE:FFC
            devices list --ip 192.168.1.100
            devices list --state active
            devices list detailed --state offline
            devices set-name AA:BB:CC:DD:EE:FF "Kitchen Light"
            devices set-name AA:BB:CC:DD:EE:FF ""                            # Clear name
            devices set-capabilities AA:BB:CC:DD:EE:FF --brightness true --color true --white false
        """
        return self.device_handler.do_devices(arg)

    def do_mappings(self, arg: str) -> None:
        """
        Mapping commands: list, get, create, delete, channel-map.
        Usage: mappings list
               mappings get <id>
               mappings create --device-id <id> [--universe <num>] --template <name> --start-channel <num>
               mappings create --device-id <id> [--universe <num>] --channel <num> --field <field>
               mappings create --device-id <id> [--universe <num>] --channel <num> --length <num>
               mappings delete <id>
               mappings channel-map
        Templates (multi-channel): rgb, rgbw, brightness_rgb, rgbwa, rgbaw, brgbwct
        Fields (single-channel): power [all], brightness [caps], r/red [caps], g/green [caps], b/blue [caps], w/white [caps], ct/color_temp [caps]
        Note: --universe defaults to 0; [caps] = requires device capability check
        Use 'help mappings create', 'mappings create --help', or 'mappings create ?' for detailed creation help
        """
        return self.mapping_handler.do_mappings(arg)

    def do_channels(self, arg: str) -> None:
        """
        Channel commands: list channels for one or more universes.
        Usage: channels list [universe...]    # Default universe is 0
        Examples:
            channels list              # Show channels for universe 0
            channels list 1            # Show channels for universe 1
            channels list 0 1 2        # Show channels for universes 0, 1, and 2
        """
        return self.monitoring_handler.do_channels(arg)

    def do_logs(self, arg: str) -> None:
        """
        View logs from the bridge.
        Usage: logs [--lines N] [--level LEVEL] [--logger NAME]
               logs tail [--level LEVEL] [--logger NAME]
               logs search PATTERN [--regex]
        Examples:
            logs
            logs --lines 50
            logs --level ERROR
            logs --logger govee.discovery
            logs tail
            logs tail --level ERROR
            logs search "device discovered"
            logs search "error.*timeout" --regex
        """
        return self.monitoring_handler.do_logs(arg)

    def do_monitor(self, arg: str) -> None:
        """
        Real-time monitoring commands.
        Usage: monitor dashboard
               monitor stats
        """
        return self.monitoring_handler.do_monitor(arg)

    def do_output(self, arg: str) -> None:
        """
        Set output format: json, yaml, or table.
        Usage: output json|yaml|table
        """
        return self.config_handler.do_output(arg)

    def do_bookmark(self, arg: str) -> None:
        """
        Manage bookmarks for devices and servers.
        Usage: bookmark add <name> <value>
               bookmark list
               bookmark delete <name>
               bookmark use <name>
        Examples:
            bookmark add myserver http://192.168.1.100:8000
            bookmark add light1 ABC123DEF456
            bookmark list
            bookmark use myserver
            bookmark delete light1
        """
        return self.config_handler.do_bookmark(arg)

    def do_alias(self, arg: str) -> None:
        """
        Manage command aliases (shortcuts).
        Usage: alias add <name> <command>
               alias list
               alias delete <name>
        Examples:
            alias add dl "devices list"
            alias add status-check "status"
            alias list
            alias delete dl
        """
        return self.config_handler.do_alias(arg)

    def do_cache(self, arg: str) -> None:
        """
        Manage response cache.
        Usage: cache stats   - Show cache statistics
               cache clear   - Clear all cached responses
        Examples:
            cache stats
            cache clear
        """
        return self.config_handler.do_cache(arg)

    def do_watch(self, arg: str) -> None:
        """
        Watch devices, mappings, logs, or dashboard with continuous updates.
        Usage: watch <target> [--interval SECONDS]

        Targets:
            devices      - Watch device status
            mappings     - Watch channel mappings
            logs         - Watch recent logs
            dashboard    - Watch dashboard summary

        Options:
            --interval SECONDS  Refresh interval (default: 5.0)

        Examples:
            watch devices              # Continuous watch with 5s refresh
            watch mappings             # Continuous watch with 5s refresh
            watch dashboard --interval 3    # Continuous with 3s refresh

        Controls:
            Press Esc or 'q' to exit watch mode
            Press '+' to decrease interval (faster refresh)
            Press '-' to increase interval (slower refresh)
        """
        return self.config_handler.do_watch(arg)

    def do_batch(self, arg: str) -> None:
        """
        Execute commands from a file.
        Usage: batch <filename>
        Examples:
            batch setup.txt
            batch /path/to/commands.txt
        """
        return self.config_handler.do_batch(arg)

    def do_session(self, arg: str) -> None:
        """
        Save or restore shell session (server URL, output format).
        Usage: session save <name>
               session load <name>
               session list
               session delete <name>
        Examples:
            session save prod
            session load prod
            session list
            session delete prod
        """
        return self.config_handler.do_session(arg)

    def do_help(self, arg: str) -> None:
        """Show help for commands with examples."""
        if arg:
            # Handle subcommands like "help mappings create"
            parts = arg.split(maxsplit=1)
            main_command = parts[0]

            # Check for subcommand help (e.g., "help mappings create")
            if len(parts) > 1:
                subcommand = parts[1]
                # Provide detailed help for specific subcommands
                if main_command == "mappings" and subcommand == "create":
                    self._append_output("[cyan]Mappings Create Help[/]\n")
                    self._append_output("\n[bold]Template-based (recommended):[/]\n")
                    self._append_output("  mappings create --device-id <id> [--universe <num>] --template <name> --start-channel <num>\n")
                    self._append_output("\n[bold]Available templates:[/]\n")
                    self._append_output("  • rgb             - 3 channels: Red, Green, Blue\n")
                    self._append_output("  • rgbw            - 4 channels: Red, Green, Blue, White\n")
                    self._append_output("  • brightness_rgb  - 4 channels: Brightness, Red, Green, Blue\n")
                    self._append_output("  • master_only     - 1 channel: Brightness\n")
                    self._append_output("  • rgbwa           - 5 channels: Red, Green, Blue, White, Brightness\n")
                    self._append_output("  • rgbaw           - 5 channels: Brightness, Red, Green, Blue, White\n")
                    self._append_output("  • full            - 6 channels: Brightness, Red, Green, Blue, White, Color Temp\n")
                    self._append_output("\n[bold]Manual configuration for single fields:[/]\n")
                    self._append_output("  mappings create --device-id <id> --universe <num> --channel <num> --type discrete --field <field>\n")
                    self._append_output("\n[bold]Options:[/]\n")
                    self._append_output("  --device-id <id>        Device identifier (required)\n")
                    self._append_output("  --universe <num>        ArtNet universe (default: 0)\n")
                    self._append_output("  --template <name>       Use a template for multi-channel mapping\n")
                    self._append_output("  --start-channel <num>   Starting Artnet channel for template\n")
                    self._append_output("  --channel <num>         Artnet channel for manual mapping\n")
                    self._append_output("  --length <num>          Number of channels (for manual range)\n")
                    self._append_output("  --type <type>           Mapping type: range or discrete\n")
                    self._append_output("  --field <field>         Field name (r, g, b, w, brightness, ct)\n")
                    self._append_output("  --allow-overlap         Allow overlapping channel ranges\n")
                    return
                else:
                    # For other subcommands, try to show the main command help
                    self._append_output(f"[yellow]No specific help for '{main_command} {subcommand}'[/]\n")
                    self._append_output(f"[dim]Showing help for '{main_command}' instead...[/]\n\n")
                    # Fall through to show main command help

            # Show help for specific command
            handler = self.commands.get(main_command)
            if handler:
                # Get the docstring from the handler
                docstring = handler.__doc__
                if docstring:
                    # Format with colors and styling (returns ANSI-formatted text)
                    help_text = self._format_command_help(main_command, docstring)
                    # Append directly to buffer (already ANSI-formatted)
                    current_text = self.output_buffer.text
                    new_text = current_text + help_text
                    if not help_text.endswith('\n'):
                        new_text += '\n'
                    # Respect follow-tail mode
                    cursor_pos = len(new_text) if self.follow_tail else min(self.output_buffer.cursor_position, len(new_text))
                    self.output_buffer.set_document(
                        Document(text=new_text, cursor_position=cursor_pos),
                        bypass_readonly=True
                    )
                    self.app.invalidate()
                else:
                    # Rich markup, use _append_output
                    self._append_output(f"\n[yellow]No help available for command '{main_command}'[/]\n")
            else:
                self._append_output(f"[red]Unknown command: {main_command}[/]" + "\n")
                self._append_output("[dim]Type 'help' to see all available commands.[/]" + "\n")
            return

        # Show enhanced help with examples using rich
        # Capture output to a string buffer for pagination
        buffer = StringIO()
        temp_console = Console(file=buffer, force_terminal=True, width=self.console.width)

        temp_console.print("═" * self.console.width)
        temp_console.print("Govee ArtNet Bridge Shell - Command Reference", style="bold cyan", justify="center")
        temp_console.print("═" * self.console.width)

        # Create help table
        help_table = Table(show_header=True, header_style="bold magenta", show_lines=True, box=box.ROUNDED)
        help_table.add_column("Command", style="cyan", width=15)
        help_table.add_column("Description", style="white", width=30)
        help_table.add_column("Example", style="yellow", width=35)

        # Add command rows
        help_table.add_row(
            "connect",
            "Connect to the bridge server",
            "connect http://localhost:8000"
        )
        help_table.add_row(
            "status",
            "Show bridge status",
            "status"
        )
        help_table.add_row(
            "health",
            "Check bridge health",
            "health\nhealth detailed"
        )
        help_table.add_row(
            "devices",
            "Manage devices",
            "devices list\ndevices list --state active\ndevices list detailed --id AA:BB\ndevices enable <id>\ndevices disable <id>\ndevices set-name <id> \"Name\""
        )
        help_table.add_row(
            "mappings",
            "Manage ArtNet mappings",
            "mappings list\nmappings get <id>\nmappings create --help\nmappings delete <id>"
        )
        help_table.add_row(
            "channels",
            "View Artnet channel assignments",
            "channels list\nchannels list 1"
        )
        help_table.add_row(
            "logs",
            "View and tail logs",
            "logs\nlogs --level ERROR\nlogs tail\nlogs search \"error\""
        )
        help_table.add_row(
            "monitor",
            "Real-time monitoring",
            "monitor dashboard\nmonitor stats"
        )
        help_table.add_row(
            "bookmark",
            "Save device IDs and URLs",
            "bookmark add light1 ABC123\nbookmark list\nbookmark use light1"
        )
        help_table.add_row(
            "alias",
            "Create command shortcuts",
            "alias add dl \"devices list\"\nalias list"
        )
        help_table.add_row(
            "watch",
            "Refresh data views",
            "watch devices\nwatch mappings\nwatch dashboard"
        )
        help_table.add_row(
            "batch",
            "Execute commands from file",
            "batch setup.txt"
        )
        help_table.add_row(
            "session",
            "Save/restore shell state",
            "session save prod\nsession load prod"
        )
        help_table.add_row(
            "output",
            "Set output format",
            "output table\noutput json\noutput yaml"
        )
        help_table.add_row(
            "version",
            "Show shell version",
            "version"
        )
        help_table.add_row(
            "tips",
            "Show helpful tips",
            "tips"
        )
        help_table.add_row(
            "clear",
            "Clear the screen",
            "clear"
        )
        help_table.add_row(
            "exit/quit",
            "Exit the shell",
            "exit"
        )

        temp_console.print(help_table)
        temp_console.print("Type 'help <command>' for detailed help on a specific command.", style="dim")
        temp_console.print("═" * self.console.width)

        # Append to output buffer (already ANSI-formatted)
        output = buffer.getvalue()
        current_text = self.output_buffer.text
        new_text = current_text + output
        if not output.endswith('\n'):
            new_text += '\n'
        # Respect follow-tail mode
        cursor_pos = len(new_text) if self.follow_tail else min(self.output_buffer.cursor_position, len(new_text))
        self.output_buffer.set_document(
            Document(text=new_text, cursor_position=cursor_pos),
            bypass_readonly=True
        )
        self.app.invalidate()

    def do_version(self, arg: str) -> None:
        """Show shell version information."""
        # Capture output to buffer to avoid Rich Console's terminal handling
        buffer = StringIO()
        temp_console = Console(file=buffer, force_terminal=True, width=self.console.width, legacy_windows=False)

        temp_console.print()
        temp_console.print(f"[bold cyan]Govee ArtNet Bridge Shell[/]")
        temp_console.print(f"[dim]Version:[/] {SHELL_VERSION}")
        temp_console.print()
        temp_console.print("[dim]Features:[/]")
        temp_console.print("  • Interactive shell with autocomplete and history")
        temp_console.print("  • Real-time WebSocket log streaming")
        temp_console.print("  • Rich formatted tables and dashboards")
        temp_console.print("  • Bookmarks, aliases, and sessions")
        temp_console.print("  • Watch mode for continuous monitoring")
        temp_console.print("  • Batch command execution")

        # Append to output buffer (already ANSI-formatted)
        output = buffer.getvalue()
        current_text = self.output_buffer.text
        new_text = current_text + output
        if not output.endswith('\n'):
            new_text += '\n'
        # Respect follow-tail mode
        cursor_pos = len(new_text) if self.follow_tail else min(self.output_buffer.cursor_position, len(new_text))
        self.output_buffer.set_document(
            Document(text=new_text, cursor_position=cursor_pos),
            bypass_readonly=True
        )
        self.app.invalidate()

    def do_tips(self, arg: str) -> None:
        """Show helpful tips for using the shell."""
        # Capture output to buffer to avoid Rich Console's terminal handling
        buffer = StringIO()
        temp_console = Console(file=buffer, force_terminal=True, width=self.console.width, legacy_windows=False)

        temp_console.print()
        temp_console.rule("[bold cyan]Shell Tips & Tricks")
        temp_console.print()

        tips_table = Table(show_header=False, show_edge=False, pad_edge=False)
        tips_table.add_column("Tip", style="cyan")

        tips_table.add_row("💡 Use [bold]Tab[/] to autocomplete commands")
        tips_table.add_row("💡 Press [bold]↑/↓[/] to navigate command history")
        tips_table.add_row("💡 Press [bold]Ctrl+R[/] to search command history")
        tips_table.add_row("💡 Create aliases: [bold]alias add dl \"devices list\"[/]")
        tips_table.add_row("💡 Save bookmarks: [bold]bookmark add light1 ABC123[/]")
        tips_table.add_row("💡 Watch in real-time: [bold]watch dashboard 3[/]")
        tips_table.add_row("💡 Run batch files: [bold]batch setup.txt[/]")
        tips_table.add_row("💡 Save sessions: [bold]session save prod[/]")
        tips_table.add_row("💡 Use [bold]output table[/] for pretty formatting")
        tips_table.add_row("💡 Scroll output: [bold]PgUp/PgDn[/] to scroll, [bold]Ctrl+T[/] to toggle follow-tail")
        tips_table.add_row("💡 Tail logs live: [bold]logs tail --level ERROR[/]")

        temp_console.print(tips_table)

        # Append to output buffer (already ANSI-formatted)
        output = buffer.getvalue()
        current_text = self.output_buffer.text
        new_text = current_text + output
        if not output.endswith('\n'):
            new_text += '\n'
        # Respect follow-tail mode
        cursor_pos = len(new_text) if self.follow_tail else min(self.output_buffer.cursor_position, len(new_text))
        self.output_buffer.set_document(
            Document(text=new_text, cursor_position=cursor_pos),
            bypass_readonly=True
        )
        self.app.invalidate()

    def do_clear(self, arg: str) -> None:
        """Clear the screen."""
        self.output_buffer.set_document(Document(""), bypass_readonly=True)
        self.app.invalidate()

    def do_exit(self, arg: str) -> bool:
        """Exit the shell."""
        if self.client:
            self.client.close()
        self._append_output("[cyan]Goodbye![/]\n")
        return True

    def do_quit(self, arg: str) -> bool:
        """Exit the shell (alias for exit)."""
        return self.do_exit(arg)

    def do_EOF(self, arg: str) -> bool:
        """Handle Ctrl+D."""
        self._append_output("\n")  # Print newline
        return self.do_exit(arg)

    def onecmd(self, line: str) -> bool:
        """
        Execute a single command.

        Args:
            line: Command line to execute

        Returns:
            True if the shell should exit, False otherwise
        """
        # SIGWINCH fallback: Check terminal size on each command (for systems without SIGWINCH)
        if not hasattr(signal, 'SIGWINCH') and self.auto_pagination:
            import shutil
            terminal_height = shutil.get_terminal_size().lines
            new_page_size = max(10, terminal_height - 5)
            if new_page_size != self.config.page_size:
                # Update pagination without user notification
                self.config = ClientConfig(
                    server_url=self.config.server_url,
                    api_key=self.config.api_key,
                    api_bearer_token=self.config.api_bearer_token,
                    output=self.config.output,
                    timeout=self.config.timeout,
                    page_size=new_page_size,
                )

        # Handle empty line
        if not line or line.isspace():
            return False

        # Parse command and arguments
        parts = line.split(maxsplit=1)
        command = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        # Dispatch to command handler
        handler = self.commands.get(command)
        if handler:
            try:
                result = handler(arg)
                return result if result is not None else False
            except Exception as exc:
                self._append_output(f"[bold red]Error executing command:[/] {exc}\n")
                return False
        else:
            # Unknown command
            self._append_output(f"[red]Unknown command: {command}[/]\n")
            self._append_output("[dim]Type 'help' or '?' for available commands.[/]\n")
            return False

    def postcmd(self, stop: bool, line: str) -> bool:
        """
        Hook method executed after a command dispatch.

        Args:
            stop: Stop flag from command
            line: Command line that was executed

        Returns:
            Updated stop flag
        """
        return stop

    def postloop(self) -> None:
        """Hook method executed once when cmdloop() is about to return."""
        pass

    async def cmdloop(self, intro: Optional[str] = None) -> None:
        """
        Run the Application event loop (async version).

        Args:
            intro: Introduction message (optional)
        """
        # Show intro in output area
        if intro is None:
            self._append_output("[bold cyan]═" * 40 + "[/]\n")
            self._append_output("[bold cyan]Govee ArtNet Bridge - Interactive Shell[/]\n")
            self._append_output("[bold cyan]═" * 40 + "[/]\n")
            self._append_output(f"[dim]Version {SHELL_VERSION}[/]\n\n")
            self._append_output("[cyan]Quick Tips:[/]\n")
            self._append_output("  • Type [bold]help[/] to see all commands\n")
            self._append_output("  • Use [bold]Tab[/] for autocomplete\n")
            self._append_output("  • Press [bold]↑/↓[/] to navigate command history\n")
            self._append_output("  • Press [bold]PgUp/PgDn[/] to scroll output (auto-follow enabled by default)\n")
            self._append_output("  • Press [bold]Ctrl+T[/] to toggle follow-tail mode\n")
            self._append_output("  • Try [bold]alias[/] to create shortcuts\n")
            self._append_output("  • Use [bold]bookmark[/] to save device IDs\n")
            self._append_output("  • Try [bold]logs tail[/] for real-time log streaming\n")
            self._append_output("  • Press [bold]Ctrl+D[/] or type [bold]exit[/] to quit\n")
            self._append_output("  • Press [bold]Ctrl+L[/] to clear the screen\n")
            self._append_output("\n[cyan]Quick Start:[/]\n")
            self._append_output("  1. [bold]devices list[/] - View all discovered Govee devices\n")
            self._append_output("     [dim]Shows device ID, IP, state, and capabilities[/]\n")
            self._append_output("  2. [bold]channels list[/] [universe] - Show ArtNet channel assignments\n")
            self._append_output("     [dim]Default universe is 0. Example: channels list 1[/]\n")
            self._append_output("  3. [bold]mappings list[/] - View current channel-to-device mappings\n")
            self._append_output("     [dim]Shows which channels control which device fields[/]\n")
            self._append_output("  4. [bold]mappings create[/] - Create new channel mappings\n")
            self._append_output("     [dim]Use --template (rgb, rgbw, brightness_rgb, etc.)[/]\n")
            self._append_output("     [dim]Example: mappings create --device-id AA:BB:CC:DD:EE:FF --template rgb --start-channel 1[/]\n")
            self._append_output("     [dim]Type 'mappings create --help' for detailed options[/]\n")
        elif intro:
            self._append_output(f"[bold cyan]{intro}[/]\n\n")

        # Run the application (async version)
        await self.app.run_async()

        # Cleanup
        self.postloop()


def run_shell(config: ClientConfig) -> None:
    """
    Run the interactive shell.

    Args:
        config: Client configuration
    """
    try:
        shell = GoveeShell(config)
        asyncio.run(shell.cmdloop())
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye!", file=sys.stderr)
    except Exception as exc:
        print(f"Shell error: {exc}", file=sys.stderr)
        sys.exit(1)
