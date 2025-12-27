"""Interactive shell for govee-artnet CLI.

ARCHITECTURAL NOTE - APPLICATION-BASED RENDERING
================================================

This shell uses prompt_toolkit's Application model for full-screen terminal control.
All output is routed through a TextArea widget to ensure proper integration with
prompt_toolkit's rendering system and prevent toolbar overlap issues.

Key components:
- Application with HSplit layout (output pane + input field + toolbar)
- TextArea for scrollable output (replaces direct console.print() calls)
- Command execution in event loop with proper screen management
- Terminal resize handled automatically by Application
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import sys
import time
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import yaml
from prompt_toolkit import Application
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import WordCompleter, NestedCompleter
from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import FormattedTextControl, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.lexers import Lexer, SimpleLexer
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.document import Document
from rich.console import Console
from rich.table import Table
from rich import box

from .cli import (
    ClientConfig,
    _api_delete,
    _api_get,
    _api_get_by_id,
    _build_client,
    _device_set_enabled,
    _handle_response,
    _print_output,
)


# Shell version
SHELL_VERSION = "1.0.0"

# Shell configuration constants
DEFAULT_WATCH_INTERVAL = 2.0
DEFAULT_API_TIMEOUT = 10.0
WS_RECV_TIMEOUT = 1.0
DEFAULT_LOG_LINES = 50

# Cache configuration
DEFAULT_CACHE_TTL = 5.0  # Default cache TTL in seconds

# Toolbar styling for prompt_toolkit
TOOLBAR_STYLE = Style.from_dict({
    #"toolbar": "bg:#2e3440",  # Dark background for entire toolbar
    #"toolbar": "fg:#1e3440",  # Dark background for entire toolbar
    #"toolbar-border": "#4c566a bg:#2e3440",  # Border line with dark background
    #"toolbar-border": "bg:#2e3440 #4c566a",  # Border line with dark background
    #"toolbar-info": "#d8dee9 bg:#2e3440",  # Light gray text on dark background
    #"toolbar-info": "#d8dee9 #2e3440",  # Light gray text on dark background
    #"status-connected": "#a3be8c bold bg:#2e3440",  # Green (connected) on dark background
    #"status-connected": "bg:#2e3440 #a3be8c bold",  # Green (connected) on dark background
    #"status-disconnected": "#bf616a bold bg:#2e3440",  # Red (disconnected) on dark background
    #"status-healthy": "#a3be8c bg:#2e3440",  # Green (healthy) on dark background
    #"status-degraded": "#ebcb8b bg:#2e3440",  # Yellow/amber (degraded) on dark background
    #"device-active": "#a3be8c bg:#2e3440",  # Green (active devices) on dark background
    #"device-unconfigured": "#ebcb8b bg:#2e3440",  # Yellow/amber (unconfigured) on dark background
    #"device-offline": "#bf616a bg:#2e3440",  # Red (offline devices) on dark background

#    "bottom-toolbar": "fg:#d8dee9 bg:#2e3440 noreverse",

#    "toolbar": "fg:#d8dee9 bg:#2e3440",
#    "toolbar-border": "fg:#4c566a bg:#2e3440",
#    "toolbar-info": "fg:#d8dee9 bg:#2e3440",
#    "status-connected": "fg:#a3be8c bold bg:#2e3440",
#    "status-disconnected": "fg:#bf616a bold bg:#2e3440",
#    "status-healthy": "fg:#a3be8c bg:#2e3440",
#    "status-degraded": "fg:#ebcb8b bg:#2e3440",
#    "device-active": "fg:#a3be8c bg:#2e3440",
#    "device-unconfigured": "fg:#ebcb8b bg:#2e3440",
#    "device-offline": "fg:#bf616a bg:#2e3440",

    "bottom-toolbar": "fg:#d8dee9 bg:ansibrightblack noreverse",

    "toolbar": "fg:#d8dee9 bg:ansibrightblack",
    "toolbar-border": "fg:ansiwhite bg:ansibrightblack",
    "toolbar-info": "fg:#d8dee9 bg:ansibrightblack",

    "status-connected": "fg:ansigreen bold bg:ansibrightblack",
    "status-disconnected": "fg:ansired bold bg:ansibrightblack",

    "status-healthy": "fg:ansigreen bg:ansibrightblack",
    "status-degraded": "fg:ansiyellow bg:ansibrightblack",

    "device-active": "fg:ansigreen bg:ansibrightblack",
    "device-unconfigured": "fg:ansiyellow bg:ansibrightblack",
    "device-offline": "fg:ansired bg:ansibrightblack",

})

class ResponseCache:
    """Simple response cache with TTL support."""

    def __init__(self, default_ttl: float = DEFAULT_CACHE_TTL):
        """
        Initialize the cache.

        Args:
            default_ttl: Default time-to-live for cache entries in seconds
        """
        self.default_ttl = default_ttl
        self.cache: dict[str, tuple[Any, float]] = {}  # key -> (value, expiry_time)
        self.stats = {"hits": 0, "misses": 0, "size": 0}

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value if exists and not expired, None otherwise
        """
        if key not in self.cache:
            self.stats["misses"] += 1
            return None

        value, expiry = self.cache[key]
        if time.time() > expiry:
            # Expired, remove from cache
            del self.cache[key]
            self.stats["size"] = len(self.cache)
            self.stats["misses"] += 1
            return None

        self.stats["hits"] += 1
        return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """
        Set value in cache with TTL.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if None)
        """
        expiry = time.time() + (ttl if ttl is not None else self.default_ttl)
        self.cache[key] = (value, expiry)
        self.stats["size"] = len(self.cache)

    def clear(self) -> None:
        """Clear all cache entries."""
        self.cache.clear()
        self.stats["size"] = 0

    def get_stats(self) -> dict[str, int]:
        """Get cache statistics."""
        return self.stats.copy()


class ANSILexer(Lexer):
    """
    Lexer that interprets ANSI escape codes in buffer text.

    This lexer processes the document text and converts ANSI escape
    sequences to prompt_toolkit styled text fragments.
    """

    def lex_document(self, document: Document):
        """
        Lex the document by converting ANSI codes to styled fragments.

        Args:
            document: The document to lex

        Returns:
            A callable that returns styled text tuples for each line
        """
        # Convert ANSI text to formatted text fragments
        # to_formatted_text() converts the ANSI object to a list of (style, text) tuples
        ansi_formatted = to_formatted_text(ANSI(document.text))

        # Split the formatted text by lines
        lines_with_styles = []
        current_line = []

        for style, text in ansi_formatted:
            # Split text by newlines while preserving style
            parts = text.split('\n')
            for i, part in enumerate(parts):
                if i > 0:
                    # New line encountered, save current and start new
                    lines_with_styles.append(current_line)
                    current_line = []
                if part:  # Add non-empty parts
                    current_line.append((style, part))

        # Don't forget the last line
        if current_line:
            lines_with_styles.append(current_line)

        # Return a callable that gives the styled fragments for each line
        def get_line(line_number):
            if line_number < len(lines_with_styles):
                return lines_with_styles[line_number]
            return []

        return get_line


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

        # Toolbar status tracking (updated periodically)
        self.toolbar_status = {
            "active_devices": 0,
            "unconfigured_devices": 0,
            "offline_devices": 0,
            "health_status": "unknown",
            "last_update": None,
        }

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

        # Set up multi-level autocomplete with command structure
        completer = NestedCompleter.from_nested_dict({
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
                'disable': None
            },
            'mappings': {'list': None, 'get': None, 'create': None, 'delete': None, 'channel-map': None},
            'channels': {'list': None},
            'logs': {'stats': None},
            'monitor': {'status': None, 'dashboard': None},
            'output': {'json': None, 'table': None, 'yaml': None},
            'bookmark': {'add': None, 'list': None, 'delete': None, 'use': None},
            'alias': {'add': None, 'list': None, 'delete': None, 'clear': None},
            'watch': {'devices': None, 'mappings': None, 'logs': None, 'dashboard': None},
            'batch': {'load': None},
            'session': {'save': None, 'list': None, 'delete': None},
            'help': None,
            '?': None,
            'version': None,
            'tips': None,
            'clear': None,
            'exit': None,
            'quit': None,
        })

        # Create input buffer with history and autocomplete
        self.input_buffer = Buffer(
            completer=completer,
            complete_while_typing=True,
            history=FileHistory(str(history_file)),
            multiline=False,
            accept_handler=self._accept_input,
        )

        # Set up key bindings
        kb = KeyBindings()

        @kb.add('c-c')
        def _(event):
            """Handle Ctrl+C - clear input or show message."""
            if self.input_buffer.text:
                self.input_buffer.reset()
            else:
                self._append_output("\n[yellow]Use 'exit' or Ctrl+D to quit.[/]\n")

        @kb.add('c-d')
        def _(event):
            """Handle Ctrl+D - exit shell."""
            event.app.exit(result=True)

        @kb.add('c-l')
        def _(event):
            """Handle Ctrl+L - clear screen."""
            self.output_buffer.set_document(Document(""), bypass_readonly=True)
            event.app.invalidate()

        @kb.add('c-t')
        def _(event):
            """Handle Ctrl+T - toggle follow-tail mode."""
            self.follow_tail = not self.follow_tail
            status = "enabled" if self.follow_tail else "disabled"
            self._append_output(f"\n[dim]Follow-tail {status}[/]\n")

        @kb.add('pageup')
        def _(event):
            """Handle Page Up - scroll output and disable follow-tail."""
            # Disable follow-tail when manually scrolling
            self.follow_tail = False
            # Scroll output buffer up by one page
            rows = event.app.output.get_size().rows - 4  # Account for input and toolbar
            new_pos = max(0, self.output_buffer.cursor_position - rows * 80)  # Approximate line length
            self.output_buffer.cursor_position = new_pos
            event.app.invalidate()

        @kb.add('pagedown')
        def _(event):
            """Handle Page Down - scroll output down."""
            # Scroll output buffer down by one page
            rows = event.app.output.get_size().rows - 4  # Account for input and toolbar
            new_pos = min(len(self.output_buffer.text), self.output_buffer.cursor_position + rows * 80)
            self.output_buffer.cursor_position = new_pos
            # If we're at the bottom, re-enable follow-tail
            if self.output_buffer.cursor_position >= len(self.output_buffer.text) - 10:
                self.follow_tail = True
            event.app.invalidate()

        # Create layout with output pane, separator, prompt + input field, and toolbar
        from prompt_toolkit.layout import WindowAlign
        self.root_container = HSplit([
            # Output pane - scrollable window with ANSI-formatted text
            # Uses BufferControl with ANSILexer for cursor-based auto-scroll
            # Not focusable to keep input active; PageUp/PageDown scroll without changing focus
            Window(
                content=BufferControl(
                    buffer=self.output_buffer,
                    lexer=ANSILexer(),
                    focusable=False,  # Keep focus on input for typing
                ),
                wrap_lines=False,
            ),
            Window(height=1, char='─'),
            Window(
                content=BufferControl(
                    buffer=self.input_buffer,
                    input_processors=[],
                ),
                height=1,
                get_line_prefix=lambda line_number, wrap_count: f"{self.prompt}",
            ),
            Window(height=1, char='─'),
            Window(
                content=FormattedTextControl(
                    text=self._get_bottom_toolbar,
                ),
                height=2,
                style="class:bottom-toolbar",
            ),
        ])

        # Create Application
        self.app: Application = Application(
            layout=Layout(self.root_container),
            key_bindings=kb,
            style=TOOLBAR_STYLE,
            full_screen=True,
            mouse_support=True,
        )

        # Set up terminal resize handler for pagination (prompt_toolkit handles layout resize)
        if hasattr(signal, 'SIGWINCH'):
            signal.signal(signal.SIGWINCH, self._handle_terminal_resize)

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

        # Explicitly clear the input buffer to ensure it doesn't persist after execution
        buffer.reset()

        return True  # Let prompt_toolkit clear buffer and save to history

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

    def _update_toolbar_status(self) -> None:
        """Update toolbar status information from bridge API."""
        if not self.client:
            return

        try:
            # Fetch health status
            health_response = self.client.get("/health", timeout=1.0)
            if health_response.status_code == 200:
                health_data = health_response.json()
                self.toolbar_status["health_status"] = health_data.get("status", "unknown")

            # Fetch device counts
            devices_response = self.client.get("/devices", timeout=1.0)
            if devices_response.status_code == 200:
                devices = devices_response.json()
                if isinstance(devices, list):
                    # Active: online (not offline), configured, and enabled
                    active = sum(
                        1 for d in devices
                        if d.get("enabled") and d.get("configured") and not d.get("offline")
                    )
                    # Unconfigured: online (not offline) but not configured (enabled doesn't matter for visibility)
                    unconfigured = sum(
                        1 for d in devices
                        if not d.get("configured") and not d.get("offline")
                    )
                    # Offline: offline and enabled
                    offline = sum(
                        1 for d in devices
                        if d.get("enabled") and d.get("offline")
                    )

                    self.toolbar_status["active_devices"] = active
                    self.toolbar_status["unconfigured_devices"] = unconfigured
                    self.toolbar_status["offline_devices"] = offline

            import time
            self.toolbar_status["last_update"] = time.time()
        except Exception:
            # Silently ignore errors - toolbar is non-critical
            pass

    def _get_bottom_toolbar(self) -> list[tuple[str, str]]:
        """
        Two-line toolbar (Variant 2) with correct background fill.
        Uses prompt_toolkit's `bottom-toolbar` class to ensure the toolbar window
        background is always dark, even across newlines.
        """
        import shutil
        import time

        try:
            from prompt_toolkit.utils import get_cwidth
        except Exception:  # pragma: no cover
            def get_cwidth(s: str) -> int:
                return len(s)

        width = shutil.get_terminal_size(fallback=(80, 24)).columns

        if (
            self.toolbar_status["last_update"] is None
            or time.time() - self.toolbar_status["last_update"] > 5
        ):
            self._update_toolbar_status()

        BASE = "class:bottom-toolbar"

        def S(cls: str) -> str:
            # Always include the base toolbar container class.
            # This ensures the window background stays dark.
            return f"{BASE} class:{cls}"

        def fit_line(fragments: list[tuple[str, str]], target_width: int) -> list[tuple[str, str]]:
            out: list[tuple[str, str]] = []
            used = 0

            def add(style: str, text: str) -> None:
                nonlocal used
                if not text or used >= target_width:
                    return
                remaining = target_width - used
                w = get_cwidth(text)
                if w <= remaining:
                    out.append((style, text))
                    used += w
                    return

                ell = "…"
                ell_w = get_cwidth(ell)
                keep = remaining - ell_w if remaining > ell_w else remaining

                t = text
                while t and get_cwidth(t) > keep:
                    t = t[:-1]

                if keep > 0 and remaining > ell_w:
                    out.append((style, t + ell))
                elif keep > 0:
                    out.append((style, t))
                used = target_width

            for s, t in fragments:
                add(s, t)

            if used < target_width:
                out.append((S("toolbar"), " " * (target_width - used)))

            return out

        parts: list[tuple[str, str]] = []

        # Border line
        parts.append((S("toolbar-border"), "─" * width + "\n"))

        # Line 1: Connection + devices
        line1: list[tuple[str, str]] = []
        if self.client:
            line1.append((S("status-connected"), "● Connected"))
        else:
            line1.append((S("status-disconnected"), "○ Disconnected"))

        line1.extend([
            (S("toolbar-info"), " │ Devices: "),
            (S("toolbar-info"), "Active "),
            (S("device-active"), str(self.toolbar_status["active_devices"])),
            (S("toolbar-info"), " | Unconfigured "),
            (S("device-unconfigured"), str(self.toolbar_status["unconfigured_devices"])),
            (S("toolbar-info"), " | Offline "),
            (S("device-offline"), str(self.toolbar_status["offline_devices"])),
        ])

        parts.extend(fit_line(line1, width))
        parts.append((S("toolbar"), "\n"))  # newline must also be under bottom-toolbar

        # Line 2: Health + server + updated
        health = self.toolbar_status["health_status"]
        if health == "healthy":
            h_style, h_icon = S("status-healthy"), "✓"
        elif health == "degraded":
            h_style, h_icon = S("status-degraded"), "⚠"
        else:
            h_style, h_icon = S("toolbar-info"), "?"

        last_update = self.toolbar_status["last_update"]
        age_txt = f"{int(time.time() - last_update)}s ago" if last_update else "n/a"

        line2: list[tuple[str, str]] = [
            (S("toolbar-info"), "Health: "),
            (h_style, f"{h_icon} {health}"),
            (S("toolbar-info"), " │ Server: "),
            (S("toolbar-info"), self.config.server_url),
            (S("toolbar-info"), " │ Updated: "),
            (S("toolbar-info"), age_txt),
        ]

        parts.extend(fit_line(line2, width))
        return parts

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
        """Connect or reconnect to the bridge server."""
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
        """Show connection status and bridge status."""
        if not self.client:
            self._append_output(f"[red]Not connected.[/] [dim]Server URL: {self.config.server_url}[/]" + "\n")
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
                table = Table(title="Component Status", show_header=True, header_style="bold cyan", box=box.ROUNDED)
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

    def _format_last_seen_age(self, last_seen: Optional[str]) -> str:
        """Format last_seen timestamp as human-readable age.

        Args:
            last_seen: ISO timestamp string or None

        Returns:
            Human-readable age string (e.g., "2d 3h 15m 42s", "5m 12s", "never")
        """
        if not last_seen:
            return "[dim]never[/]"

        try:
            from datetime import datetime
            last_seen_dt = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
            now = datetime.now(last_seen_dt.tzinfo) if last_seen_dt.tzinfo else datetime.now()
            delta = now - last_seen_dt

            # Calculate components
            total_seconds = int(delta.total_seconds())
            if total_seconds < 0:
                return "[dim]just now[/]"

            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60

            # Format based on age
            if days > 0:
                return f"{days}d {hours}h {minutes}m {seconds}s"
            elif hours > 0:
                return f"{hours}h {minutes}m {seconds}s"
            elif minutes > 0:
                return f"{minutes}m {seconds}s"
            else:
                return f"{seconds}s"
        except Exception:
            return "[dim]unknown[/]"

    def _show_devices_simple(self, filter_id: Optional[str] = None, filter_ip: Optional[str] = None, filter_state: Optional[str] = None) -> None:
        """Show devices in simplified 2-line table format.

        Args:
            filter_id: Optional filter by device ID (MAC address)
            filter_ip: Optional filter by IP address
            filter_state: Optional filter by state (active, disabled, offline)
        """
        try:
            # Fetch devices from API
            response = self.client.get("/devices")
            devices = _handle_response(response)

            if not devices:
                self._append_output("[yellow]No devices found[/]\n")
                return

            # Apply filters if provided
            if filter_id:
                devices = [d for d in devices if filter_id.lower() in d.get("id", "").lower()]
            if filter_ip:
                devices = [d for d in devices if filter_ip in d.get("ip", "")]
            if filter_state:
                state_lower = filter_state.lower()
                filtered = []
                for d in devices:
                    is_offline = d.get("offline", False)
                    is_enabled = d.get("enabled", False)
                    is_configured = d.get("configured", False)

                    # Determine device state
                    if state_lower == "active" and not is_offline and is_configured and is_enabled:
                        filtered.append(d)
                    elif state_lower == "disabled" and not is_enabled:
                        filtered.append(d)
                    elif state_lower == "offline" and is_offline:
                        filtered.append(d)
                devices = filtered

            if not devices:
                self._append_output("[yellow]No devices match the filters[/]\n")
                return

            # Create simplified table (reordered columns, removed Enabled/Configured, added Last Seen)
            table = Table(title="Devices", show_header=True, header_style="bold cyan", box=box.ROUNDED)
            table.add_column("ID", style="cyan", width=20, no_wrap=True)
            table.add_column("IP Address", style="green", width=15)
            table.add_column("Model", style="yellow", width=15)
            table.add_column("State", style="white", width=20)
            table.add_column("Last Seen", style="magenta", width=18)

            # Add device rows
            for device in devices:
                device_id = device.get("id", "N/A")[:20]  # Truncate long IDs
                model = device.get("model_number", "Unknown")
                ip_address = device.get("ip", "N/A")

                # Determine state(s)
                states = []
                is_offline = device.get("offline", False)
                is_enabled = device.get("enabled", False)
                is_configured = device.get("configured", False)

                if not is_offline and is_configured and is_enabled:
                    states.append(("[green]", "Active"))
                if not is_enabled:
                    states.append(("[yellow]", "Disabled"))
                if is_offline:
                    states.append(("[red]", "Offline"))

                # Format state column
                if states:
                    state_str = " / ".join([f"{color}{state}[/]" for color, state in states])
                else:
                    state_str = "[dim]Unknown[/]"

                # Format last seen age
                last_seen_age = self._format_last_seen_age(device.get("last_seen"))

                table.add_row(
                    device_id,
                    ip_address,
                    model,
                    state_str,
                    last_seen_age
                )

            self._append_output(table)
            self._append_output(f"\n[dim]Total: {len(devices)} device(s). Use 'devices list detailed' for full info.[/]\n")

        except Exception as exc:
            self._append_output(f"[red]Error fetching devices: {exc}[/]\n")

    def _show_devices_detailed(self, filter_id: Optional[str] = None, filter_ip: Optional[str] = None, filter_state: Optional[str] = None) -> None:
        """Show devices in detailed card format with colors.

        Args:
            filter_id: Optional filter by device ID (MAC address)
            filter_ip: Optional filter by IP address
            filter_state: Optional filter by state (active, disabled, offline)
        """
        try:
            # Fetch devices from API
            response = self.client.get("/devices")
            devices = _handle_response(response)

            if not devices:
                self._append_output("[yellow]No devices found[/]\n")
                return

            # Apply filters if provided
            if filter_id:
                devices = [d for d in devices if filter_id.lower() in d.get("id", "").lower()]
            if filter_ip:
                devices = [d for d in devices if filter_ip in d.get("ip", "")]
            if filter_state:
                state_lower = filter_state.lower()
                filtered = []
                for d in devices:
                    is_offline = d.get("offline", False)
                    is_enabled = d.get("enabled", False)
                    is_configured = d.get("configured", False)

                    # Determine device state
                    if state_lower == "active" and not is_offline and is_configured and is_enabled:
                        filtered.append(d)
                    elif state_lower == "disabled" and not is_enabled:
                        filtered.append(d)
                    elif state_lower == "offline" and is_offline:
                        filtered.append(d)
                devices = filtered

            if not devices:
                self._append_output("[yellow]No devices match the filters[/]\n")
                return

            # Print each device as a card
            for idx, device in enumerate(devices):
                # Create a table for this device
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Field", style="bold cyan", width=20)
                table.add_column("Value", style="yellow")

                # Key fields to display in order with colors
                key_fields = [
                    ("ID", "id"),
                    ("IP", "ip"),
                    ("Model", "model_number"),
                    ("Type", "device_type"),
                    ("Description", "description"),
                    ("Enabled", "enabled"),
                    ("Manual", "manual"),
                    ("Discovered", "discovered"),
                    ("Configured", "configured"),
                    ("Offline", "offline"),
                    ("Stale", "stale"),
                ]

                # Add key fields with appropriate colors
                for label, key in key_fields:
                    if key in device and device[key] is not None:
                        value = device[key]
                        if isinstance(value, bool):
                            value_str = "✓" if value else "✗"
                            # Color coding for boolean values
                            if key in ("enabled", "configured", "discovered", "manual"):
                                style = "green" if value else "red"
                            elif key in ("offline", "stale"):
                                style = "red" if value else "green"
                            else:
                                style = "green" if value else "red"
                            table.add_row(f"[bold cyan]{label}[/]", f"[{style}]{value_str}[/]")
                        else:
                            table.add_row(f"[bold cyan]{label}[/]", str(value))

                # Add capabilities as JSON if present
                if "capabilities" in device and device["capabilities"]:
                    import json
                    caps_str = json.dumps(device["capabilities"], indent=2) if isinstance(device["capabilities"], dict) else str(device["capabilities"])
                    table.add_row("[bold cyan]Capabilities[/]", caps_str)

                # Add metadata fields if present
                metadata_fields = [
                    ("LED Count", "led_count"),
                    ("Length (m)", "length_meters"),
                    ("Segments", "segment_count"),
                    ("Last Seen", "last_seen"),
                    ("First Seen", "first_seen"),
                ]

                for label, key in metadata_fields:
                    if key in device and device[key] is not None:
                        table.add_row(f"[bold cyan]{label}[/]", str(device[key]))

                # Print device header and table
                header_text = f"[bold magenta]Device {idx + 1} of {len(devices)}[/]"
                self._append_output(header_text + "\n")
                self._append_output(table)

                # Add separator between devices
                if idx < len(devices) - 1:
                    self._append_output("\n[dim]" + "─" * 80 + "[/]\n")

            self._append_output(f"\n[dim]Total: {len(devices)} device(s).[/]\n")

        except Exception as exc:
            self._append_output(f"[red]Error fetching devices: {exc}[/]\n")

    def do_devices(self, arg: str) -> None:
        """
        Device commands: list, list detailed, enable, disable.
        Usage: devices list [--id ID] [--ip IP] [--state STATE]              # Show simplified 2-line view
               devices list detailed [--id ID] [--ip IP] [--state STATE]     # Show full device details
               devices enable <device_id>
               devices disable <device_id>
        Examples:
            devices list
            devices list --id AA:BB:CC
            devices list --ip 192.168.1.100
            devices list --state active
            devices list detailed --state offline
        """
        if not self.client:
            self._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        args = shlex.split(arg)
        if not args:
            self._append_output("[yellow]Usage: devices <command> [args...][/]" + "\n")
            return

        command = args[0]

        try:
            if command == "list":
                # Parse optional filter parameters
                filter_id = None
                filter_ip = None
                filter_state = None
                is_detailed = False

                i = 1
                while i < len(args):
                    if args[i] == "detailed":
                        is_detailed = True
                    elif args[i] == "--id" and i + 1 < len(args):
                        filter_id = args[i + 1]
                        i += 1
                    elif args[i] == "--ip" and i + 1 < len(args):
                        filter_ip = args[i + 1]
                        i += 1
                    elif args[i] == "--state" and i + 1 < len(args):
                        filter_state = args[i + 1]
                        i += 1
                    i += 1

                # Check if "detailed" subcommand was provided
                if is_detailed:
                    # Show full detailed view with filters
                    self._show_devices_detailed(filter_id, filter_ip, filter_state)
                else:
                    # Show simplified 2-line view with filters
                    self._show_devices_simple(filter_id, filter_ip, filter_state)
            elif command == "enable" and len(args) >= 2:
                device_id = args[1]
                self._capture_api_output(_device_set_enabled, self.client, device_id, True, self.config)
                # Invalidate devices cache after mutation
                self._invalidate_cache("/devices")
            elif command == "disable" and len(args) >= 2:
                device_id = args[1]
                self._capture_api_output(_device_set_enabled, self.client, device_id, False, self.config)
                # Invalidate devices cache after mutation
                self._invalidate_cache("/devices")
            else:
                self._append_output(f"[red]Unknown or incomplete command: devices {arg}[/]" + "\n")
                self._append_output("[yellow]Try: devices list, devices enable <id>, devices disable <id>[/]" + "\n")
        except Exception as exc:
            self._handle_error(exc, "devices")

    def _show_mappings_list(self) -> None:
        """Show mappings list with unicode table borders."""
        try:
            response = self.client.get("/mappings")
            mappings = _handle_response(response)

            if not mappings:
                self._append_output("[yellow]No mappings found[/]\n")
                return

            # Create table with unicode borders
            table = Table(title="ArtNet Mappings", show_header=True, header_style="bold cyan", box=box.ROUNDED)
            table.add_column("ID", style="cyan", width=8)
            table.add_column("Device ID", style="yellow", width=20)
            table.add_column("Universe", style="green", width=8, justify="right")
            table.add_column("Channel", style="magenta", width=8, justify="right")
            table.add_column("Length", style="blue", width=6, justify="right")
            table.add_column("Field", style="white", width=12)

            # Add mapping rows
            for mapping in mappings:
                table.add_row(
                    str(mapping.get("id", "N/A")),
                    str(mapping.get("device_id", "N/A"))[:20],
                    str(mapping.get("universe", "N/A")),
                    str(mapping.get("channel", "N/A")),
                    str(mapping.get("length", "N/A")),
                    str(mapping.get("field", "N/A"))
                )

            self._append_output(table)
            self._append_output(f"\n[dim]Total: {len(mappings)} mapping(s)[/]\n")

        except Exception as exc:
            self._append_output(f"[red]Error fetching mappings: {exc}[/]\n")

    def _create_mapping(self, args: list[str]) -> None:
        """
        Create a new mapping with template or manual configuration.

        Args:
            args: Command line arguments for mapping creation
        """
        # Parse arguments
        device_id = None
        universe = 0  # Default to universe 0
        start_channel = None
        channel = None
        length = None
        mapping_type = None
        field = None
        template = None
        allow_overlap = False

        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--help":
                self._append_output("[cyan]Mappings Create Help[/]\n")
                self._append_output("\n[bold]Template-based (recommended):[/]\n")
                self._append_output("  mappings create --device-id <id> [--universe <num>] --template <name> --start-channel <num>\n")
                self._append_output("\n[bold]Available templates:[/]\n")
                self._append_output("  • rgb        - 3 channels (red, green, blue)\n")
                self._append_output("  • rgbw       - 4 channels (red, green, blue, white)\n")
                self._append_output("  • brightness - 1 channel (brightness)\n")
                self._append_output("  • temperature - 1 channel (color temperature)\n")
                self._append_output("\n[bold]Manual configuration for single fields:[/]\n")
                self._append_output("  mappings create --device-id <id> [--universe <num>] --channel <num> --type <type> --field <field>\n")
                self._append_output("\n[bold]Manual configuration for multi-channel fields:[/]\n")
                self._append_output("  mappings create --device-id <id> [--universe <num>] --channel <num> --length <num> --type <type> --field <field>\n")
                self._append_output("\n[bold]Field types (for manual configuration):[/]\n")
                self._append_output("  Single field mappings (length=1):\n")
                self._append_output("    --type single --field red          - Map to red channel only\n")
                self._append_output("    --type single --field green        - Map to green channel only\n")
                self._append_output("    --type single --field blue         - Map to blue channel only\n")
                self._append_output("    --type single --field white        - Map to white channel only\n")
                self._append_output("    --type single --field brightness   - Map to brightness control\n")
                self._append_output("  Multi-channel mappings:\n")
                self._append_output("    --type color --field rgb           - Map to RGB (length=3)\n")
                self._append_output("    --type color --field rgbw          - Map to RGBW (length=4)\n")
                self._append_output("\n[bold]Examples:[/]\n")
                self._append_output("  # Template-based (universe defaults to 0 if omitted)\n")
                self._append_output("  mappings create --device-id AA:BB:CC:DD:EE:FF --template rgb --start-channel 1\n")
                self._append_output("  mappings create --device-id @kitchen --universe 1 --template rgbw --start-channel 10\n")
                self._append_output("\n  # Manual single field mapping\n")
                self._append_output("  mappings create --device-id AA:BB:CC:DD:EE:FF --channel 5 --type single --field brightness\n")
                self._append_output("  mappings create --device-id @kitchen --channel 20 --type single --field white\n")
                self._append_output("\n  # Manual multi-channel mapping\n")
                self._append_output("  mappings create --device-id AA:BB:CC:DD:EE:FF --channel 1 --length 3 --type color --field rgb\n")
                return
            elif arg == "--device-id" and i + 1 < len(args):
                device_id = self._resolve_bookmark(args[i + 1])
                i += 2
            elif arg == "--universe" and i + 1 < len(args):
                try:
                    universe = int(args[i + 1])
                except ValueError:
                    self._append_output(f"[red]Invalid universe number: {args[i + 1]}[/]\n")
                    return
                i += 2
            elif arg == "--start-channel" and i + 1 < len(args):
                try:
                    start_channel = int(args[i + 1])
                except ValueError:
                    self._append_output(f"[red]Invalid start channel: {args[i + 1]}[/]\n")
                    return
                i += 2
            elif arg == "--channel" and i + 1 < len(args):
                try:
                    channel = int(args[i + 1])
                except ValueError:
                    self._append_output(f"[red]Invalid channel: {args[i + 1]}[/]\n")
                    return
                i += 2
            elif arg == "--length" and i + 1 < len(args):
                try:
                    length = int(args[i + 1])
                except ValueError:
                    self._append_output(f"[red]Invalid length: {args[i + 1]}[/]\n")
                    return
                i += 2
            elif arg == "--type" and i + 1 < len(args):
                mapping_type = args[i + 1]
                i += 2
            elif arg == "--field" and i + 1 < len(args):
                field = args[i + 1]
                i += 2
            elif arg == "--template" and i + 1 < len(args):
                template = args[i + 1]
                i += 2
            elif arg == "--allow-overlap":
                allow_overlap = True
                i += 1
            else:
                self._append_output(f"[red]Unknown argument: {arg}[/]\n")
                self._append_output("[yellow]Use 'mappings create --help' for usage information[/]\n")
                return

        # Validate required fields
        if not device_id:
            self._append_output("[red]Error: --device-id is required[/]\n")
            return

        # Build payload
        payload: dict[str, Any] = {
            "device_id": device_id,
            "universe": universe,
            "allow_overlap": allow_overlap,
        }

        if template:
            # Template-based mapping
            if template not in {"rgb", "rgbw", "brightness", "temperature"}:
                self._append_output(f"[red]Invalid template: {template}[/]\n")
                self._append_output("[yellow]Valid templates: rgb, rgbw, brightness, temperature[/]\n")
                return

            if start_channel is None and channel is None:
                self._append_output("[red]Error: --start-channel (or --channel) is required when using a template[/]\n")
                return

            payload["template"] = template
            payload["start_channel"] = start_channel if start_channel is not None else channel
            if channel is not None:
                payload["channel"] = channel
        else:
            # Manual mapping
            if channel is None:
                if start_channel is not None:
                    channel = start_channel
                else:
                    self._append_output("[red]Error: --channel is required when not using a template[/]\n")
                    return

            payload["channel"] = channel
            payload["length"] = length if length is not None else 1

            if mapping_type:
                payload["mapping_type"] = mapping_type
            if field:
                payload["field"] = field

        # Create the mapping
        try:
            response = self.client.post("/mappings", json=payload)
            data = _handle_response(response)

            # Invalidate caches
            self._invalidate_cache("/mappings")
            self._invalidate_cache("/channel-map")

            # Show success message with details
            if template:
                self._append_output(f"[green]✓ Created {template} mapping for device {device_id}[/]\n")
            else:
                self._append_output(f"[green]✓ Created mapping for device {device_id}[/]\n")

            # Show the created mapping details
            if isinstance(data, list):
                # Template-based mapping returns a list of channel mappings
                mapping_ids = [str(m.get('id', 'N/A')) for m in data]
                self._append_output(f"[dim]Created {len(data)} channel mappings (IDs: {', '.join(mapping_ids)})[/]\n")
            else:
                # Manual mapping returns a single mapping object
                self._append_output(f"[dim]Mapping ID: {data.get('id', 'N/A')}[/]\n")

        except Exception as exc:
            self._append_output(f"[red]Error creating mapping: {exc}[/]\n")

    def do_mappings(self, arg: str) -> None:
        """
        Mapping commands: list, get, create, delete, channel-map.
        Usage: mappings list
               mappings get <id>
               mappings create --device-id <id> [--universe <num>] --template <name> --start-channel <num>
               mappings create --device-id <id> [--universe <num>] --channel <num> [--length <num>] --type <type> --field <field>
               mappings delete <id>
               mappings channel-map
        Templates: rgb, rgbw, brightness, temperature
        Note: --universe defaults to 0 if omitted
        """
        if not self.client:
            self._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        args = shlex.split(arg)
        if not args:
            self._append_output("[yellow]Usage: mappings <command> [args...][/]" + "\n")
            return

        command = args[0]

        try:
            if command == "list":
                self._show_mappings_list()
            elif command == "get" and len(args) >= 2:
                mapping_id = args[1]
                self._capture_api_output(_api_get_by_id, self.client, "/mappings", mapping_id, self.config)
            elif command == "create":
                self._create_mapping(args[1:])
            elif command == "delete" and len(args) >= 2:
                mapping_id = args[1]
                self._capture_api_output(_api_delete, self.client, "/mappings", mapping_id, self.config)
                # Invalidate mappings cache after mutation
                self._invalidate_cache("/mappings")
                self._invalidate_cache("/channel-map")
                self._append_output(f"[green]Mapping {mapping_id} deleted[/]" + "\n")
            elif command == "channel-map":
                self._capture_api_output(_api_get, self.client, "/channel-map", self.config)
            else:
                self._append_output(f"[red]Unknown or incomplete command: mappings {arg}[/]" + "\n")
                self._append_output("[yellow]Try: mappings list, mappings get <id>, mappings create --help, mappings delete <id>, mappings channel-map[/]" + "\n")
        except Exception as exc:
            self._handle_error(exc, "mappings")

    def do_channels(self, arg: str) -> None:
        """
        Channel commands: list channels for a universe.
        Usage: channels list [universe]    # Default universe is 0
        Examples:
            channels list              # Show channels for universe 0
            channels list 1            # Show channels for universe 1
        """
        if not self.client:
            self._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        args = shlex.split(arg)
        if not args:
            self._append_output("[yellow]Usage: channels list [universe][/]" + "\n")
            return

        command = args[0]

        try:
            if command == "list":
                # Parse universe argument (default to 0)
                universe = 0
                if len(args) > 1:
                    try:
                        universe = int(args[1])
                    except ValueError:
                        self._append_output(f"[red]Invalid universe number: {args[1]}[/]\n")
                        return

                self._show_channels_list(universe)
            else:
                self._append_output(f"[red]Unknown command: channels {arg}[/]" + "\n")
                self._append_output("[yellow]Try: channels list [universe][/]" + "\n")
        except Exception as exc:
            self._handle_error(exc, "channels")

    def _show_channels_list(self, universe: int = 0) -> None:
        """Show DMX channels for the specified universe.

        Args:
            universe: ArtNet universe number (default 0)
        """
        try:
            # Fetch mappings and devices
            mappings_response = self.client.get("/mappings")
            mappings = _handle_response(mappings_response)

            devices_response = self.client.get("/devices")
            devices = _handle_response(devices_response)

            # Create device lookup by ID
            device_lookup = {d["id"]: d for d in devices} if devices else {}

            # Filter mappings for this universe
            universe_mappings = [m for m in mappings if m.get("universe") == universe]

            if not universe_mappings:
                self._append_output(f"[yellow]No mappings found for universe {universe}[/]\n")
                return

            # Build channel map (1-512)
            channel_map = {}  # channel_num -> (device_id, ip, function, mapping_info)

            # Channel function names for common templates
            TEMPLATE_FUNCTIONS = {
                "rgb": ["Red", "Green", "Blue"],
                "rgbw": ["Red", "Green", "Blue", "White"],
                "rgbww": ["Red", "Green", "Blue", "Warm White", "Cool White"],
                "brightness": ["Brightness"],
                "dimmer": ["Dimmer"],
                "cct": ["Color Temp", "Brightness"],
                "rgbcct": ["Red", "Green", "Blue", "Color Temp", "Brightness"],
            }

            for mapping in universe_mappings:
                device_id = mapping.get("device_id", "N/A")
                start_channel = mapping.get("channel", 1)
                channel_length = mapping.get("length", 1)
                field = mapping.get("field", "").lower()

                # Get device IP
                device = device_lookup.get(device_id, {})
                device_ip = device.get("ip", "N/A")

                # Determine channel functions - use field to determine if it matches a known template
                functions = TEMPLATE_FUNCTIONS.get(field, [f"Ch{i+1}" for i in range(channel_length)])

                # Populate channel map
                for i in range(channel_length):
                    channel_num = start_channel + i
                    if 1 <= channel_num <= 512:
                        function = functions[i] if i < len(functions) else f"Ch{i+1}"
                        channel_map[channel_num] = (device_id, device_ip, function, field)

            if not channel_map:
                self._append_output(f"[yellow]No channels populated for universe {universe}[/]\n")
                return

            # Create table with unicode borders
            table = Table(
                title=f"DMX Channels - Universe {universe}",
                show_header=True,
                header_style="bold cyan",
                box=box.ROUNDED
            )
            table.add_column("Channel", style="cyan", width=8, justify="right")
            table.add_column("Device ID", style="yellow", width=20)
            table.add_column("IP Address", style="green", width=15)
            table.add_column("Function", style="magenta", width=15)
            table.add_column("Field", style="dim", width=10)

            # Add rows for populated channels (sorted by channel number)
            for channel_num in sorted(channel_map.keys()):
                device_id, device_ip, function, field = channel_map[channel_num]

                # Apply color coding to functions
                if "Red" in function:
                    function_style = "[red]" + function + "[/]"
                elif "Green" in function:
                    function_style = "[green]" + function + "[/]"
                elif "Blue" in function:
                    function_style = "[blue]" + function + "[/]"
                elif "White" in function or "Brightness" in function or "Dimmer" in function:
                    function_style = "[white]" + function + "[/]"
                elif "Temp" in function or "CCT" in function:
                    function_style = "[yellow]" + function + "[/]"
                else:
                    function_style = function

                table.add_row(
                    str(channel_num),
                    device_id[:20],
                    device_ip,
                    function_style,
                    field
                )

            self._append_output(table)
            self._append_output(f"\n[dim]Total: {len(channel_map)} populated channel(s) in universe {universe}[/]\n")
            self._append_output(f"[dim]Range: {min(channel_map.keys())} - {max(channel_map.keys())}[/]\n")

        except Exception as exc:
            self._append_output(f"[red]Error fetching channels: {exc}[/]\n")

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
        if not self.client:
            self._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        args = shlex.split(arg)

        try:
            # Check if this is a tail command
            if args and args[0] == "tail":
                self._logs_tail(args[1:])
                return

            # Check if this is a search command
            if args and args[0] == "search":
                if len(args) < 2:
                    self._append_output("[yellow]Usage: logs search PATTERN [--regex] [--case-sensitive] [--lines N][/]" + "\n")
                    return

                pattern = args[1]
                params: dict[str, Any] = {"pattern": pattern}

                # Parse optional flags
                i = 2
                while i < len(args):
                    if args[i] == "--regex":
                        params["regex"] = True
                    elif args[i] == "--case-sensitive":
                        params["case_sensitive"] = True
                    elif args[i] == "--lines" and i + 1 < len(args):
                        params["lines"] = int(args[i + 1])
                        i += 1
                    i += 1

                data = _handle_response(self.client.get("/logs/search", params=params))
                self._append_output(f"[cyan]Found {data['count']} matching log entries:[/]" + "\n")
                self._capture_api_output(_print_output, data["logs"], self.config.output)

            else:
                # Regular log view
                params: dict[str, Any] = {}

                # Parse flags
                i = 0
                while i < len(args):
                    if args[i] == "--lines" and i + 1 < len(args):
                        params["lines"] = int(args[i + 1])
                        i += 1
                    elif args[i] == "--level" and i + 1 < len(args):
                        params["level"] = args[i + 1]
                        i += 1
                    elif args[i] == "--logger" and i + 1 < len(args):
                        params["logger"] = args[i + 1]
                        i += 1
                    elif args[i] == "--offset" and i + 1 < len(args):
                        params["offset"] = int(args[i + 1])
                        i += 1
                    i += 1

                data = _handle_response(self.client.get("/logs", params=params))
                self._append_output(f"[cyan]Showing {data['lines']} of {data['total']} log entries:[/]" + "\n")
                self._capture_api_output(_print_output, data["logs"], self.config.output)

        except Exception as exc:
            self._handle_error(exc, "logs")

    def _logs_tail(self, args: list[str]) -> None:
        """
        Tail logs in real-time using WebSocket.

        Args:
            args: Command arguments (filters)
        """
        # Check for websockets library early
        try:
            import websockets.sync.client as ws_client
        except ImportError:
            self._append_output("[red]Error: websockets library not installed[/]" + "\n")
            self._append_output("[yellow]Install with: pip install websockets[/]" + "\n")
            return

        # Parse filters
        level_filter = None
        logger_filter = None

        i = 0
        while i < len(args):
            if args[i] == "--level" and i + 1 < len(args):
                level_filter = args[i + 1]
                i += 1
            elif args[i] == "--logger" and i + 1 < len(args):
                logger_filter = args[i + 1]
                i += 1
            i += 1

        # Build WebSocket URL
        ws_url = self.config.server_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url += "/logs/stream"

        self._append_output("[cyan]Streaming logs (Press Ctrl+C to stop)...[/]" + "\n")
        if level_filter:
            self._append_output(f"[dim]  Level filter: {level_filter}[/]" + "\n")
        if logger_filter:
            self._append_output(f"[dim]  Logger filter: {logger_filter}[/]" + "\n")
        self._append_output("\n")

        try:
            with ws_client.connect(ws_url) as websocket:
                # Send filters if set
                if level_filter or logger_filter:
                    filters = {}
                    if level_filter:
                        filters["level"] = level_filter
                    if logger_filter:
                        filters["logger"] = logger_filter
                    websocket.send(json.dumps(filters))

                # Stream logs
                while True:
                    try:
                        message = websocket.recv(timeout=WS_RECV_TIMEOUT)
                        data = json.loads(message)

                        # Skip ping messages
                        if data.get("type") == "ping":
                            continue

                        # Format and print log entry
                        timestamp = data.get("timestamp", "")
                        level = data.get("level", "INFO")
                        logger_name = data.get("logger", "")
                        message_text = data.get("message", "")

                        self._append_output(f"[{timestamp}] {level:7} | {logger_name:25} | {message_text}\n")

                    except TimeoutError:
                        # No message received, continue
                        continue

        except KeyboardInterrupt:
            self._append_output("\n[yellow]Stopped tailing logs[/]\n")
        except Exception as exc:
            self._append_output(f"[red]Error streaming logs: {exc}[/]\n")

    def do_monitor(self, arg: str) -> None:
        """
        Real-time monitoring commands.
        Usage: monitor dashboard
               monitor stats
        """
        if not self.client:
            self._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        args = shlex.split(arg)
        if not args:
            self._append_output("[yellow]Usage: monitor dashboard|stats[/]" + "\n")
            return

        command = args[0]

        try:
            if command == "dashboard":
                self._monitor_dashboard()
            elif command == "stats":
                self._monitor_stats()
            else:
                self._append_output(f"[red]Unknown monitor command: {command}[/]" + "\n")
                self._append_output("[yellow]Try: monitor dashboard, monitor stats[/]" + "\n")
        except Exception as exc:
            self._handle_error(exc, "monitor")

    def _monitor_dashboard(self) -> None:
        """Display live dashboard with system status using rich formatting."""
        try:
            # Get health and status
            self._append_output("[bold cyan]Fetching dashboard data...[/]\n")
            health_data = _handle_response(self.client.get("/health"))
            status_data = _handle_response(self.client.get("/status"))

            # Overall status
            overall_status = health_data.get("status", "unknown")
            status_style = "bold green" if overall_status == "ok" else "bold red"
            status_indicator = "✓" if overall_status == "ok" else "✗"

            # Create header
            self._append_output("\n")
            self._append_output("[bold cyan]" + "═" * 60 + "[/]\n")
            self._append_output("[bold cyan]Govee ArtNet Bridge - Dashboard[/]\n")
            self._append_output("[bold cyan]" + "═" * 60 + "[/]\n")
            self._append_output(f"Status: [{status_style}]{status_indicator} {overall_status.upper()}[/]" + "\n")
            self._append_output("\n")

            # Devices table
            devices_table = Table(title="Devices", show_header=True, header_style="bold magenta", box=box.ROUNDED)
            devices_table.add_column("Type", style="cyan")
            devices_table.add_column("Count", justify="right", style="yellow")

            discovered_count = status_data.get("discovered_count", 0)
            manual_count = status_data.get("manual_count", 0)
            devices_table.add_row("Discovered", str(discovered_count))
            devices_table.add_row("Manual", str(manual_count))
            devices_table.add_row("[bold]Total[/]", f"[bold]{discovered_count + manual_count}[/]")

            self._append_output(devices_table)
            self._append_output("\n\n")

            # Queue info
            queue_depth = status_data.get("queue_depth", 0)
            queue_style = "green" if queue_depth < 100 else "yellow" if queue_depth < 500 else "red"
            self._append_output(f"Message Queue Depth: [{queue_style}]{queue_depth}[/]" + "\n")
            self._append_output("\n")

            # Subsystems table
            subsystems = health_data.get("subsystems", {})
            if subsystems:
                subsystems_table = Table(title="Subsystems", show_header=True, header_style="bold magenta", box=box.ROUNDED)
                subsystems_table.add_column("Name", style="cyan")
                subsystems_table.add_column("Status", style="green")

                for name, data in subsystems.items():
                    sub_status = data.get("status", "unknown")
                    indicator = "✓" if sub_status == "ok" else "✗"
                    status_style = "green" if sub_status == "ok" else "red"
                    subsystems_table.add_row(name, f"[{status_style}]{indicator} {sub_status}[/]")

                self._append_output(subsystems_table)
                self._append_output("\n\n")

        except Exception as exc:
            self._append_output(f"[bold red]Error fetching dashboard:[/] {exc}" + "\n")

    def _monitor_stats(self) -> None:
        """Display system statistics."""
        self._append_output("[cyan]Fetching statistics...[/]" + "\n")
        try:
            status_data = _handle_response(self.client.get("/status"))
            self._capture_api_output(_print_output, status_data, self.config.output)
        except Exception as exc:
            self._append_output(f"[red]Error fetching stats: {exc}[/]" + "\n")

    def do_output(self, arg: str) -> None:
        """
        Set output format: json, yaml, or table.
        Usage: output json|yaml|table
        """
        args = shlex.split(arg)
        if not args or args[0] not in ("json", "yaml", "table"):
            self._append_output("[yellow]Usage: output json|yaml|table[/]" + "\n")
            self._append_output(f"[dim]Current format: {self.config.output}[/]" + "\n")
            return

        new_format = args[0]
        self.config = ClientConfig(
            server_url=self.config.server_url,
            api_key=self.config.api_key,
            api_bearer_token=self.config.api_bearer_token,
            output=new_format,
            timeout=self.config.timeout,
            page_size=self.config.page_size,
        )
        self._append_output(f"[green]Output format set to: {new_format}[/]" + "\n")

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
        args = shlex.split(arg)
        if not args:
            self._append_output("Usage: bookmark add|list|delete|use <name> [value]" + "\n")
            return

        command = args[0]

        if command == "add" and len(args) >= 3:
            name = args[1]
            value = args[2]
            self.bookmarks[name] = value
            self._save_json(self.bookmarks_file, self.bookmarks)
            self._append_output(f"[green]Bookmark '{name}' added: {value}[/]" + "\n")

        elif command == "list":
            if not self.bookmarks:
                self._append_output("[dim]No bookmarks saved[/]" + "\n")
                return

            table = Table(title="Bookmarks", show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("Name", style="cyan")
            table.add_column("Value", style="yellow")

            for name, value in self.bookmarks.items():
                table.add_row(name, value)

            self._append_output(table + "\n")

        elif command == "delete" and len(args) >= 2:
            name = args[1]
            if name in self.bookmarks:
                del self.bookmarks[name]
                self._save_json(self.bookmarks_file, self.bookmarks)
                self._append_output(f"[green]Bookmark '{name}' deleted[/]" + "\n")
            else:
                self._append_output(f"[red]Bookmark '{name}' not found[/]" + "\n")

        elif command == "use" and len(args) >= 2:
            name = args[1]
            if name in self.bookmarks:
                value = self.bookmarks[name]
                # Detect if it's a URL or device ID
                if value.startswith("http://") or value.startswith("https://"):
                    self.do_connect(value)
                else:
                    self._append_output(f"[cyan]Bookmark value: {value}[/]" + "\n")
                    self._append_output("[dim]Use this value in your commands[/]" + "\n")
            else:
                self._append_output(f"[red]Bookmark '{name}' not found[/]" + "\n")

        else:
            self._append_output("Usage: bookmark add|list|delete|use <name> [value]" + "\n")

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
        args = shlex.split(arg)
        if not args:
            self._append_output("Usage: alias add|list|delete <name> [command]" + "\n")
            return

        command = args[0]

        if command == "add" and len(args) >= 3:
            name = args[1]
            value = " ".join(args[2:])
            self.aliases[name] = value
            self._save_json(self.aliases_file, self.aliases)
            self._append_output(f"[green]Alias '{name}' -> '{value}' added[/]" + "\n")

        elif command == "list":
            if not self.aliases:
                self._append_output("[dim]No aliases defined[/]" + "\n")
                return

            table = Table(title="Aliases", show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("Alias", style="cyan")
            table.add_column("Command", style="yellow")

            for name, value in self.aliases.items():
                table.add_row(name, value)

            self._append_output(table + "\n")

        elif command == "delete" and len(args) >= 2:
            name = args[1]
            if name in self.aliases:
                del self.aliases[name]
                self._save_json(self.aliases_file, self.aliases)
                self._append_output(f"[green]Alias '{name}' deleted[/]" + "\n")
            else:
                self._append_output(f"[red]Alias '{name}' not found[/]" + "\n")

        else:
            self._append_output("Usage: alias add|list|delete <name> [command]" + "\n")

    def do_cache(self, arg: str) -> None:
        """
        Manage response cache.
        Usage: cache stats   - Show cache statistics
               cache clear   - Clear all cached responses
        Examples:
            cache stats
            cache clear
        """
        args = shlex.split(arg) if arg else []
        if not args:
            self._append_output("Usage: cache stats|clear" + "\n")
            return

        command = args[0]

        if command == "stats":
            stats = self.cache.get_stats()
            total_requests = stats["hits"] + stats["misses"]
            hit_rate = (stats["hits"] / total_requests * 100) if total_requests > 0 else 0

            table = Table(title="Cache Statistics", show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="yellow")

            table.add_row("Hits", str(stats["hits"]))
            table.add_row("Misses", str(stats["misses"]))
            table.add_row("Hit Rate", f"{hit_rate:.1f}%")
            table.add_row("Cache Size", str(stats["size"]))
            table.add_row("TTL (seconds)", str(self.cache.default_ttl))

            self._append_output(table + "\n")

        elif command == "clear":
            self.cache.clear()
            self._append_output("[green]Cache cleared successfully[/]" + "\n")

        else:
            self._append_output("Usage: cache stats|clear" + "\n")

    def do_watch(self, arg: str) -> None:
        """
        Watch devices, mappings, logs, or dashboard (single refresh).
        Usage: watch devices
               watch mappings
               watch logs
               watch dashboard
        Examples:
            watch devices        # Refresh devices once
            watch mappings       # Refresh mappings once
            watch dashboard      # Show dashboard

        Note: In interactive shell mode, continuous watch is not supported to prevent UI blocking.
              Use 'monitor dashboard' for real-time updates, or run the command manually as needed.
              For continuous monitoring, use the CLI in non-interactive mode.
        """
        if not self.client:
            self._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        args = shlex.split(arg)
        if not args:
            self._append_output("[yellow]Usage: watch devices|mappings|logs|dashboard[/]" + "\n")
            return

        command = args[0]

        try:
            # Add timestamp header
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._append_output(f"\n[bold cyan]─── Refreshed at {timestamp} ───[/]\n")

            # Execute command based on target
            if command == "devices":
                self._show_devices_simple()
            elif command == "mappings":
                self._show_mappings_list()
            elif command == "logs":
                self.do_logs("")
            elif command == "dashboard":
                self._monitor_dashboard()
            else:
                self._append_output(f"[red]Unknown watch target: {command}[/]\n")
                self._append_output("[yellow]Valid targets: devices, mappings, logs, dashboard[/]\n")

            self._append_output(f"\n[dim]Tip: Run the command again to refresh, or use 'monitor dashboard' for live updates[/]\n")

        except Exception as exc:
            self._handle_error(exc, f"watch {command}")

    def do_batch(self, arg: str) -> None:
        """
        Execute commands from a file.
        Usage: batch <filename>
        Examples:
            batch setup.txt
            batch /path/to/commands.txt
        """
        args = shlex.split(arg)
        if not args:
            self._append_output("Usage: batch <filename>" + "\n")
            return

        filename = args[0]
        file_path = Path(filename)

        if not file_path.exists():
            self._append_output(f"[red]File not found: {filename}[/]" + "\n")
            return

        try:
            with open(file_path, "r") as f:
                lines = f.readlines()

            self._append_output(f"[cyan]Executing {len(lines)} commands from {filename}[/]" + "\n")
            self._append_output("\n")

            for i, line in enumerate(lines, 1):
                line = line.strip()

                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue

                self._append_output(f"[dim]({i}) {line}[/]" + "\n")
                self.onecmd(line)
                self._append_output("\n")

            self._append_output("[green]Batch execution complete[/]" + "\n")

        except Exception as exc:
            self._append_output(f"[red]Error executing batch: {exc}[/]" + "\n")

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
        args = shlex.split(arg)
        if not args:
            self._append_output("Usage: session save|load|list|delete <name>" + "\n")
            return

        command = args[0]
        sessions_file = self.data_dir / "sessions.json"
        sessions = self._load_json(sessions_file, {})

        if command == "save" and len(args) >= 2:
            name = args[1]
            session_data = {
                "server_url": self.config.server_url,
                "output": self.config.output,
                "page_size": self.config.page_size,
            }
            sessions[name] = session_data
            self._save_json(sessions_file, sessions)
            self._append_output(f"[green]Session '{name}' saved[/]" + "\n")

        elif command == "load" and len(args) >= 2:
            name = args[1]
            if name in sessions:
                session_data = sessions[name]
                self.config = ClientConfig(
                    server_url=session_data["server_url"],
                    api_key=self.config.api_key,
                    api_bearer_token=self.config.api_bearer_token,
                    output=session_data["output"],
                    timeout=self.config.timeout,
                    page_size=session_data.get("page_size", self.config.page_size),
                )
                self._connect()
                self._append_output(f"[green]Session '{name}' loaded[/]" + "\n")
                self._append_output(f"  Server: {self.config.server_url}" + "\n")
                self._append_output(f"  Output: {self.config.output}" + "\n")
                if self.config.page_size:
                    self._append_output(f"  Pagination: {self.config.page_size} lines" + "\n")
            else:
                self._append_output(f"[red]Session '{name}' not found[/]" + "\n")

        elif command == "list":
            if not sessions:
                self._append_output("[dim]No sessions saved[/]" + "\n")
                return

            table = Table(title="Sessions", show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("Name", style="cyan")
            table.add_column("Server URL", style="yellow")
            table.add_column("Output Format", style="green")

            for name, data in sessions.items():
                table.add_row(name, data["server_url"], data["output"])

            self._append_output(table + "\n")

        elif command == "delete" and len(args) >= 2:
            name = args[1]
            if name in sessions:
                del sessions[name]
                self._save_json(sessions_file, sessions)
                self._append_output(f"[green]Session '{name}' deleted[/]" + "\n")
            else:
                self._append_output(f"[red]Session '{name}' not found[/]" + "\n")

        else:
            self._append_output("Usage: session save|load|list|delete <name>" + "\n")

    def do_help(self, arg: str) -> None:
        """Show help for commands with examples."""
        if arg:
            # Show help for specific command
            handler = self.commands.get(arg)
            if handler:
                # Get the docstring from the handler
                docstring = handler.__doc__
                if docstring:
                    # Format with colors and styling (returns ANSI-formatted text)
                    help_text = self._format_command_help(arg, docstring)
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
                    self._append_output(f"\n[yellow]No help available for command '{arg}'[/]\n")
            else:
                self._append_output(f"[red]Unknown command: {arg}[/]" + "\n")
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
            "devices list\ndevices list --state active\ndevices list detailed --id AA:BB\ndevices enable <id>\ndevices disable <id>"
        )
        help_table.add_row(
            "mappings",
            "Manage ArtNet mappings",
            "mappings list\nmappings get <id>\nmappings create --help\nmappings delete <id>"
        )
        help_table.add_row(
            "channels",
            "View DMX channel assignments",
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

    def cmdloop(self, intro: Optional[str] = None) -> None:
        """
        Run the Application event loop.

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
            self._append_output("  • Press [bold]Ctrl+D[/] or type [bold]exit[/] to quit\n")
            self._append_output("  • Press [bold]Ctrl+L[/] to clear the screen\n")
        elif intro:
            self._append_output(f"[bold cyan]{intro}[/]\n\n")

        # Run the application
        self.app.run()

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
        shell.cmdloop()
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye!", file=sys.stderr)
    except Exception as exc:
        print(f"Shell error: {exc}", file=sys.stderr)
        sys.exit(1)
