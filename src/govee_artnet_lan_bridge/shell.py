"""Interactive shell for govee-artnet CLI."""

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
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.table import Table

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
        # Configure console with no legacy windows mode and disable soft wrapping
        self.console = Console(legacy_windows=False, soft_wrap=False)

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
            "logs": self.do_logs,
            "monitor": self.do_monitor,
            "output": self.do_output,
            "console": self.do_console,
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

        # Set up autocomplete with all command names
        completer = WordCompleter(list(self.commands.keys()), ignore_case=True)

        # Create prompt session with history, autocomplete, and status toolbar
        # Reserve 3 lines for toolbar: 1 for border + 2 for status info
        self.session: PromptSession = PromptSession(
            history=FileHistory(str(history_file)),
            completer=completer,
            complete_while_typing=True,
            bottom_toolbar=self._get_bottom_toolbar,
            reserve_space_for_menu=3,
            style=TOOLBAR_STYLE,
        )

        # Set up terminal resize handler (Unix-like systems only)
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
            self.console.print(f"[red]Error saving to {file_path}: {exc}[/]")

    def _load_shell_config(self) -> dict[str, Any]:
        """
        Load shell configuration from TOML file.

        Returns:
            Configuration dictionary with defaults
        """
        # Auto-detect terminal height for default pagination
        # Reserve space for: toolbar (3 lines) + prompt (1 line) + pagination prompt (1 line) + buffer (2 lines)
        import shutil
        terminal_height = shutil.get_terminal_size().lines
        default_page_size = max(10, terminal_height - 7)

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

    def _connect(self) -> None:
        """Establish connection to the bridge server."""
        try:
            self.client = _build_client(self.config)
            # Test connection
            response = self.client.get("/health")
            response.raise_for_status()
            self.console.print(f"[green]Connected to {self.config.server_url}[/]")
        except Exception as exc:
            self.console.print(f"[yellow]Warning: Could not connect to {self.config.server_url}: {exc}[/]")
            self.console.print("[dim]Some commands may not work. Use 'connect' to retry.[/]")
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
            # Reserve space for: toolbar (3 lines) + prompt (1 line) + pagination prompt (1 line) + buffer (2 lines)
            new_page_size = max(10, terminal_height - 7)

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
            self.console.print(
                f"[bold red]HTTP {exc.response.status_code}:[/] {exc.response.text}"
            )
        elif isinstance(exc, httpx.RequestError):
            self.console.print(f"[bold red]Connection Error:[/] {exc}")
        else:
            context_str = f" in {context}" if context else ""
            self.console.print(f"[bold red]Error{context_str}:[/] {exc}")

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

    def _paginate_text(self, text: str) -> None:
        """
        Print text with optional pagination based on config.

        Args:
            text: Text to print
        """
        # Strip trailing newlines to prevent excessive blank space at bottom
        text = text.rstrip('\n')

        if not self.config.page_size:
            # No pagination - write text with single trailing newline
            sys.stdout.write(text + '\n')
            sys.stdout.flush()
            return

        lines = text.split("\n")
        line_count = 0

        for i, line in enumerate(lines):
            sys.stdout.write(line)
            # Only add newline if not the last line
            if i < len(lines) - 1:
                sys.stdout.write("\n")
            line_count += 1

            if line_count >= self.config.page_size and i < len(lines) - 1:
                # Pause for user input
                try:
                    response = input("\n[Press Enter to continue, 'q' to quit] ")
                    if response.lower().startswith('q'):
                        sys.stdout.write("\n[Output truncated]\n")
                        return
                    line_count = 0
                except (KeyboardInterrupt, EOFError):
                    sys.stdout.write("\n[Output interrupted]\n")
                    return

        # Add single trailing newline at the end
        sys.stdout.write('\n')
        sys.stdout.flush()

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
            self.console.print(f"[dim](expanding: {expanded})[/]")
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
            self.console.print("[yellow]Disconnected[/]")

    def do_status(self, arg: str) -> None:
        """Show connection status and bridge status."""
        if not self.client:
            self.console.print(f"[red]Not connected.[/] [dim]Server URL: {self.config.server_url}[/]")
            return

        try:
            _api_get(self.client, "/status", self.config)
        except Exception as exc:
            self._handle_error(exc, "status")

    def do_health(self, arg: str) -> None:
        """Check bridge health."""
        if not self.client:
            print("Not connected. Use 'connect' first.")
            return

        try:
            _api_get(self.client, "/health", self.config)
        except Exception as exc:
            self._handle_error(exc, "health")

    def do_devices(self, arg: str) -> None:
        """
        Device commands: list, add, update, enable, disable, command.
        Usage: devices list
               devices enable <device_id>
               devices disable <device_id>
        """
        if not self.client:
            self.console.print("[red]Not connected. Use 'connect' first.[/]")
            return

        args = shlex.split(arg)
        if not args:
            self.console.print("[yellow]Usage: devices <command> [args...][/]")
            return

        command = args[0]

        try:
            if command == "list":
                _api_get(self.client, "/devices", self.config)
            elif command == "enable" and len(args) >= 2:
                device_id = args[1]
                _device_set_enabled(self.client, device_id, True, self.config)
                # Invalidate devices cache after mutation
                self._invalidate_cache("/devices")
            elif command == "disable" and len(args) >= 2:
                device_id = args[1]
                _device_set_enabled(self.client, device_id, False, self.config)
                # Invalidate devices cache after mutation
                self._invalidate_cache("/devices")
            else:
                self.console.print(f"[red]Unknown or incomplete command: devices {arg}[/]")
                self.console.print("[yellow]Try: devices list, devices enable <id>, devices disable <id>[/]")
        except Exception as exc:
            self._handle_error(exc, "devices")

    def do_mappings(self, arg: str) -> None:
        """
        Mapping commands: list, get, delete, channel-map.
        Usage: mappings list
               mappings get <id>
               mappings delete <id>
               mappings channel-map
        """
        if not self.client:
            self.console.print("[red]Not connected. Use 'connect' first.[/]")
            return

        args = shlex.split(arg)
        if not args:
            self.console.print("[yellow]Usage: mappings <command> [args...][/]")
            return

        command = args[0]

        try:
            if command == "list":
                _api_get(self.client, "/mappings", self.config)
            elif command == "get" and len(args) >= 2:
                mapping_id = args[1]
                _api_get_by_id(self.client, "/mappings", mapping_id, self.config)
            elif command == "delete" and len(args) >= 2:
                mapping_id = args[1]
                _api_delete(self.client, "/mappings", mapping_id, self.config)
                # Invalidate mappings cache after mutation
                self._invalidate_cache("/mappings")
                self._invalidate_cache("/channel-map")
                self.console.print(f"[green]Mapping {mapping_id} deleted[/]")
            elif command == "channel-map":
                _api_get(self.client, "/channel-map", self.config)
            else:
                self.console.print(f"[red]Unknown or incomplete command: mappings {arg}[/]")
                self.console.print("[yellow]Try: mappings list, mappings get <id>, mappings delete <id>, mappings channel-map[/]")
        except Exception as exc:
            self._handle_error(exc, "mappings")

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
            self.console.print("[red]Not connected. Use 'connect' first.[/]")
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
                    self.console.print("[yellow]Usage: logs search PATTERN [--regex] [--case-sensitive] [--lines N][/]")
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
                self.console.print(f"[cyan]Found {data['count']} matching log entries:[/]")
                _print_output(data["logs"], self.config.output)

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
                self.console.print(f"[cyan]Showing {data['lines']} of {data['total']} log entries:[/]")
                _print_output(data["logs"], self.config.output)

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
            self.console.print("[red]Error: websockets library not installed[/]")
            self.console.print("[yellow]Install with: pip install websockets[/]")
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

        self.console.print("[cyan]Streaming logs (Press Ctrl+C to stop)...[/]")
        if level_filter:
            self.console.print(f"[dim]  Level filter: {level_filter}[/]")
        if logger_filter:
            self.console.print(f"[dim]  Logger filter: {logger_filter}[/]")
        self.console.print()

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

                        print(f"[{timestamp}] {level:7} | {logger_name:25} | {message_text}")

                    except TimeoutError:
                        # No message received, continue
                        continue

        except KeyboardInterrupt:
            self.console.print("\n[yellow]Stopped tailing logs[/]")
        except Exception as exc:
            self.console.print(f"[red]Error streaming logs: {exc}[/]")

    def do_monitor(self, arg: str) -> None:
        """
        Real-time monitoring commands.
        Usage: monitor dashboard
               monitor stats
        """
        if not self.client:
            self.console.print("[red]Not connected. Use 'connect' first.[/]")
            return

        args = shlex.split(arg)
        if not args:
            self.console.print("[yellow]Usage: monitor dashboard|stats[/]")
            return

        command = args[0]

        try:
            if command == "dashboard":
                self._monitor_dashboard()
            elif command == "stats":
                self._monitor_stats()
            else:
                self.console.print(f"[red]Unknown monitor command: {command}[/]")
                self.console.print("[yellow]Try: monitor dashboard, monitor stats[/]")
        except Exception as exc:
            self._handle_error(exc, "monitor")

    def _monitor_dashboard(self) -> None:
        """Display live dashboard with system status using rich formatting."""
        try:
            # Get health and status
            with self.console.status("[bold cyan]Fetching dashboard data...", spinner="dots"):
                health_data = _handle_response(self.client.get("/health"))
                status_data = _handle_response(self.client.get("/status"))

            # Overall status
            overall_status = health_data.get("status", "unknown")
            status_style = "bold green" if overall_status == "ok" else "bold red"
            status_indicator = "✓" if overall_status == "ok" else "✗"

            # Create header
            self.console.print()
            self.console.rule("[bold cyan]Govee ArtNet Bridge - Dashboard")
            self.console.print()
            self.console.print(f"Status: [{status_style}]{status_indicator} {overall_status.upper()}[/]")
            self.console.print()

            # Devices table
            devices_table = Table(title="Devices", show_header=True, header_style="bold magenta")
            devices_table.add_column("Type", style="cyan")
            devices_table.add_column("Count", justify="right", style="yellow")

            discovered_count = status_data.get("discovered_count", 0)
            manual_count = status_data.get("manual_count", 0)
            devices_table.add_row("Discovered", str(discovered_count))
            devices_table.add_row("Manual", str(manual_count))
            devices_table.add_row("[bold]Total[/]", f"[bold]{discovered_count + manual_count}[/]")

            self.console.print(devices_table)
            self.console.print()

            # Queue info
            queue_depth = status_data.get("queue_depth", 0)
            queue_style = "green" if queue_depth < 100 else "yellow" if queue_depth < 500 else "red"
            self.console.print(f"Message Queue Depth: [{queue_style}]{queue_depth}[/]")
            self.console.print()

            # Subsystems table
            subsystems = health_data.get("subsystems", {})
            if subsystems:
                subsystems_table = Table(title="Subsystems", show_header=True, header_style="bold magenta")
                subsystems_table.add_column("Name", style="cyan")
                subsystems_table.add_column("Status", style="green")

                for name, data in subsystems.items():
                    sub_status = data.get("status", "unknown")
                    indicator = "✓" if sub_status == "ok" else "✗"
                    status_style = "green" if sub_status == "ok" else "red"
                    subsystems_table.add_row(name, f"[{status_style}]{indicator} {sub_status}[/]")

                self.console.print(subsystems_table)
                self.console.print()

        except Exception as exc:
            self.console.print(f"[bold red]Error fetching dashboard:[/] {exc}")

    def _monitor_stats(self) -> None:
        """Display system statistics."""
        self.console.print("[cyan]Fetching statistics...[/]")
        try:
            status_data = _handle_response(self.client.get("/status"))
            _print_output(status_data, self.config.output)
        except Exception as exc:
            self.console.print(f"[red]Error fetching stats: {exc}[/]")

    def do_output(self, arg: str) -> None:
        """
        Set output format: json, yaml, or table.
        Usage: output json|yaml|table
        """
        args = shlex.split(arg)
        if not args or args[0] not in ("json", "yaml", "table"):
            self.console.print("[yellow]Usage: output json|yaml|table[/]")
            self.console.print(f"[dim]Current format: {self.config.output}[/]")
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
        self.console.print(f"[green]Output format set to: {new_format}[/]")

    def do_console(self, arg: str) -> None:
        """
        Configure console pagination settings.
        Usage: console pagination <lines>
               console pagination off
               console pagination auto
               console pagination
        Examples:
            console pagination 20      # Set page size to 20 lines
            console pagination 50      # Set page size to 50 lines
            console pagination off     # Disable pagination
            console pagination auto    # Auto-detect terminal height
            console pagination         # Show current setting
        """
        args = shlex.split(arg)

        if not args or (len(args) == 1 and args[0] == "pagination"):
            # Show current pagination setting
            if self.config.page_size is None:
                self.console.print("Pagination: [yellow]disabled[/]")
            else:
                self.console.print(f"Pagination: [green]{self.config.page_size} lines[/]")
            self.console.print("[dim]Usage: console pagination <lines|off|auto>[/]")
            return

        if len(args) < 2 or args[0] != "pagination":
            self.console.print("[red]Usage: console pagination <lines|off|auto>[/]")
            self.console.print("Examples:")
            self.console.print("  console pagination 20    # Set to 20 lines")
            self.console.print("  console pagination off   # Disable")
            self.console.print("  console pagination auto  # Auto-detect")
            return

        setting = args[1].lower()

        if setting == "off":
            page_size = None
            self.auto_pagination = False  # Disable auto-resize
        elif setting == "auto":
            import shutil
            terminal_height = shutil.get_terminal_size().lines
            page_size = max(10, terminal_height - 2)
            self.auto_pagination = True  # Enable auto-resize
        else:
            try:
                page_size = int(setting)
                if page_size < 1:
                    self.console.print("[red]Page size must be a positive number[/]")
                    return
                self.auto_pagination = False  # User set explicit size, disable auto-resize
            except ValueError:
                self.console.print(f"[red]Invalid pagination setting: {setting}[/]")
                self.console.print("Use a number, 'off', or 'auto'")
                return

        # Update configuration
        self.config = ClientConfig(
            server_url=self.config.server_url,
            api_key=self.config.api_key,
            api_bearer_token=self.config.api_bearer_token,
            output=self.config.output,
            timeout=self.config.timeout,
            page_size=page_size,
        )

        # Save to shell config for persistence
        self.shell_config["console"] = {"page_size": page_size}
        try:
            import tomli_w
            with open(self.config_file, "wb") as f:
                tomli_w.dump(self.shell_config, f)
        except ImportError:
            # tomli_w not available, just print a warning
            self.console.print("[dim]Note: Install tomli_w to persist this setting[/]")
        except Exception as exc:
            self.console.print(f"[dim]Warning: Could not save config: {exc}[/]")

        # Confirm the change
        if page_size is None:
            self.console.print("[green]Pagination disabled[/]")
        else:
            self.console.print(f"[green]Pagination set to {page_size} lines[/]")

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
            self.console.print("Usage: bookmark add|list|delete|use <name> [value]")
            return

        command = args[0]

        if command == "add" and len(args) >= 3:
            name = args[1]
            value = args[2]
            self.bookmarks[name] = value
            self._save_json(self.bookmarks_file, self.bookmarks)
            self.console.print(f"[green]Bookmark '{name}' added: {value}[/]")

        elif command == "list":
            if not self.bookmarks:
                self.console.print("[dim]No bookmarks saved[/]")
                return

            table = Table(title="Bookmarks", show_header=True, header_style="bold magenta")
            table.add_column("Name", style="cyan")
            table.add_column("Value", style="yellow")

            for name, value in self.bookmarks.items():
                table.add_row(name, value)

            self.console.print(table)

        elif command == "delete" and len(args) >= 2:
            name = args[1]
            if name in self.bookmarks:
                del self.bookmarks[name]
                self._save_json(self.bookmarks_file, self.bookmarks)
                self.console.print(f"[green]Bookmark '{name}' deleted[/]")
            else:
                self.console.print(f"[red]Bookmark '{name}' not found[/]")

        elif command == "use" and len(args) >= 2:
            name = args[1]
            if name in self.bookmarks:
                value = self.bookmarks[name]
                # Detect if it's a URL or device ID
                if value.startswith("http://") or value.startswith("https://"):
                    self.do_connect(value)
                else:
                    self.console.print(f"[cyan]Bookmark value: {value}[/]")
                    self.console.print("[dim]Use this value in your commands[/]")
            else:
                self.console.print(f"[red]Bookmark '{name}' not found[/]")

        else:
            self.console.print("Usage: bookmark add|list|delete|use <name> [value]")

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
            self.console.print("Usage: alias add|list|delete <name> [command]")
            return

        command = args[0]

        if command == "add" and len(args) >= 3:
            name = args[1]
            value = " ".join(args[2:])
            self.aliases[name] = value
            self._save_json(self.aliases_file, self.aliases)
            self.console.print(f"[green]Alias '{name}' -> '{value}' added[/]")

        elif command == "list":
            if not self.aliases:
                self.console.print("[dim]No aliases defined[/]")
                return

            table = Table(title="Aliases", show_header=True, header_style="bold magenta")
            table.add_column("Alias", style="cyan")
            table.add_column("Command", style="yellow")

            for name, value in self.aliases.items():
                table.add_row(name, value)

            self.console.print(table)

        elif command == "delete" and len(args) >= 2:
            name = args[1]
            if name in self.aliases:
                del self.aliases[name]
                self._save_json(self.aliases_file, self.aliases)
                self.console.print(f"[green]Alias '{name}' deleted[/]")
            else:
                self.console.print(f"[red]Alias '{name}' not found[/]")

        else:
            self.console.print("Usage: alias add|list|delete <name> [command]")

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
            self.console.print("Usage: cache stats|clear")
            return

        command = args[0]

        if command == "stats":
            stats = self.cache.get_stats()
            total_requests = stats["hits"] + stats["misses"]
            hit_rate = (stats["hits"] / total_requests * 100) if total_requests > 0 else 0

            table = Table(title="Cache Statistics", show_header=True, header_style="bold magenta")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="yellow")

            table.add_row("Hits", str(stats["hits"]))
            table.add_row("Misses", str(stats["misses"]))
            table.add_row("Hit Rate", f"{hit_rate:.1f}%")
            table.add_row("Cache Size", str(stats["size"]))
            table.add_row("TTL (seconds)", str(self.cache.default_ttl))

            self.console.print(table)

        elif command == "clear":
            self.cache.clear()
            self.console.print("[green]Cache cleared successfully[/]")

        else:
            self.console.print("Usage: cache stats|clear")

    def do_watch(self, arg: str) -> None:
        """
        Watch devices or status with continuous updates.
        Usage: watch devices [interval]
               watch status [interval]
               watch dashboard [interval]
        Examples:
            watch devices        # Update every 2 seconds
            watch status 5       # Update every 5 seconds
            watch dashboard 3    # Update every 3 seconds
        """
        if not self.client:
            self.console.print("[red]Not connected. Use 'connect' first.[/]")
            return

        args = shlex.split(arg)
        if not args:
            self.console.print("Usage: watch devices|status|dashboard [interval]")
            return

        command = args[0]
        interval = float(args[1]) if len(args) > 1 else DEFAULT_WATCH_INTERVAL

        self.console.print(f"[cyan]Watching {command} (Press Ctrl+C to stop, updating every {interval}s)[/]")
        self.console.print()

        try:
            import time

            # Note: Watch mode benefits from response caching when interval < cache TTL.
            # For example, with 2s interval and 5s cache TTL, only 2 out of 5 iterations
            # will make actual API calls, reducing server load by 60%.
            # Use 'cache stats' to monitor cache hit rate.

            while True:
                # Clear screen
                self.console.clear()

                # Execute command
                if command == "devices":
                    self.do_devices("list")
                elif command == "status":
                    self.do_status("")
                elif command == "dashboard":
                    self._monitor_dashboard()
                else:
                    self.console.print(f"[red]Unknown watch target: {command}[/]")
                    break

                # Wait
                time.sleep(interval)

        except KeyboardInterrupt:
            self.console.print("\n[yellow]Watch stopped[/]")

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
            self.console.print("Usage: batch <filename>")
            return

        filename = args[0]
        file_path = Path(filename)

        if not file_path.exists():
            self.console.print(f"[red]File not found: {filename}[/]")
            return

        try:
            with open(file_path, "r") as f:
                lines = f.readlines()

            self.console.print(f"[cyan]Executing {len(lines)} commands from {filename}[/]")
            self.console.print()

            for i, line in enumerate(lines, 1):
                line = line.strip()

                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue

                self.console.print(f"[dim]({i}) {line}[/]")
                self.onecmd(line)
                self.console.print()

            self.console.print("[green]Batch execution complete[/]")

        except Exception as exc:
            self.console.print(f"[red]Error executing batch: {exc}[/]")

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
            self.console.print("Usage: session save|load|list|delete <name>")
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
            self.console.print(f"[green]Session '{name}' saved[/]")

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
                self.console.print(f"[green]Session '{name}' loaded[/]")
                self.console.print(f"  Server: {self.config.server_url}")
                self.console.print(f"  Output: {self.config.output}")
                if self.config.page_size:
                    self.console.print(f"  Pagination: {self.config.page_size} lines")
            else:
                self.console.print(f"[red]Session '{name}' not found[/]")

        elif command == "list":
            if not sessions:
                self.console.print("[dim]No sessions saved[/]")
                return

            table = Table(title="Sessions", show_header=True, header_style="bold magenta")
            table.add_column("Name", style="cyan")
            table.add_column("Server URL", style="yellow")
            table.add_column("Output Format", style="green")

            for name, data in sessions.items():
                table.add_row(name, data["server_url"], data["output"])

            self.console.print(table)

        elif command == "delete" and len(args) >= 2:
            name = args[1]
            if name in sessions:
                del sessions[name]
                self._save_json(sessions_file, sessions)
                self.console.print(f"[green]Session '{name}' deleted[/]")
            else:
                self.console.print(f"[red]Session '{name}' not found[/]")

        else:
            self.console.print("Usage: session save|load|list|delete <name>")

    def do_help(self, arg: str) -> None:
        """Show help for commands with examples."""
        if arg:
            # Show help for specific command
            handler = self.commands.get(arg)
            if handler:
                # Get the docstring from the handler
                docstring = handler.__doc__
                if docstring:
                    # Format with colors and styling
                    help_text = self._format_command_help(arg, docstring)
                else:
                    help_text = f"\n[yellow]No help available for command '{arg}'[/]\n"

                # Apply pagination if configured
                self._paginate_text(help_text)
            else:
                self.console.print(f"[red]Unknown command: {arg}[/]")
                self.console.print("[dim]Type 'help' to see all available commands.[/]")
            return

        # Show enhanced help with examples using rich
        # Capture output to a string buffer for pagination
        buffer = StringIO()
        temp_console = Console(file=buffer, force_terminal=True, width=self.console.width)

        temp_console.print()
        temp_console.print("═" * 80)
        temp_console.print("Govee ArtNet Bridge Shell - Command Reference", style="bold cyan", justify="center")
        temp_console.print("═" * 80)
        temp_console.print()

        # Create help table
        help_table = Table(show_header=True, header_style="bold magenta", show_lines=True)
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
            "health"
        )
        help_table.add_row(
            "devices",
            "Manage devices",
            "devices list\ndevices enable <id>\ndevices disable <id>"
        )
        help_table.add_row(
            "mappings",
            "Manage ArtNet mappings",
            "mappings list\nmappings get <id>\nmappings delete <id>"
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
            "Continuous monitoring",
            "watch devices\nwatch dashboard 5"
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
            "console",
            "Configure console settings",
            "console pagination 20\nconsole pagination off\nconsole pagination auto"
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
        temp_console.print()
        temp_console.print("Type 'help <command>' for detailed help on a specific command.", style="dim")
        temp_console.print()

        # Get the buffered output and paginate it
        help_text = buffer.getvalue()
        self._paginate_text(help_text)

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

        # Write directly to stdout with controlled newlines
        output = buffer.getvalue().rstrip('\n')
        sys.stdout.write(output + '\n')
        sys.stdout.flush()

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
        tips_table.add_row("💡 Control pagination: [bold]console pagination 30[/]")
        tips_table.add_row("💡 Tail logs live: [bold]logs tail --level ERROR[/]")

        temp_console.print(tips_table)

        # Write directly to stdout with controlled newlines
        output = buffer.getvalue().rstrip('\n')
        sys.stdout.write(output + '\n')
        sys.stdout.flush()

    def do_clear(self, arg: str) -> None:
        """Clear the screen."""
        self.console.clear()

    def do_exit(self, arg: str) -> bool:
        """Exit the shell."""
        if self.client:
            self.client.close()
        self.console.print("[cyan]Goodbye![/]")
        return True

    def do_quit(self, arg: str) -> bool:
        """Exit the shell (alias for exit)."""
        return self.do_exit(arg)

    def do_EOF(self, arg: str) -> bool:
        """Handle Ctrl+D."""
        print()  # Print newline
        return self.do_exit(arg)

    def onecmd(self, line: str) -> bool:
        """
        Execute a single command.

        Args:
            line: Command line to execute

        Returns:
            True if the shell should exit, False otherwise
        """
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
                self.console.print(f"[bold red]Error executing command:[/] {exc}")
                return False
        else:
            # Unknown command
            self.console.print(f"[red]Unknown command: {command}[/]")
            self.console.print("[dim]Type 'help' or '?' for available commands.[/]")
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
        Custom command loop using prompt_toolkit for better UX.

        Args:
            intro: Introduction message (optional)
        """
        # Clear the screen on startup for a clean interface
        import os
        os.system('cls' if os.name == 'nt' else 'clear')

        # Print custom intro with tips
        if intro is None:
            self.console.print()
            self.console.rule("[bold cyan]Govee ArtNet Bridge - Interactive Shell")
            self.console.print()
            self.console.print(f"[dim]Version {SHELL_VERSION}[/]")
            self.console.print()
            self.console.print("[cyan]Quick Tips:[/]")
            self.console.print("  • Type [bold]help[/] to see all commands")
            self.console.print("  • Use [bold]Tab[/] for autocomplete")
            self.console.print("  • Press [bold]↑/↓[/] to navigate command history")
            self.console.print("  • Try [bold]alias[/] to create shortcuts")
            self.console.print("  • Use [bold]bookmark[/] to save device IDs")
            self.console.print("  • Press [bold]Ctrl+D[/] or type [bold]exit[/] to quit")
            self.console.print()
        elif intro:
            self.console.print(intro, style="bold cyan")
            self.console.print()

        # Main loop
        stop = False
        while not stop:
            try:
                # Get input with prompt_toolkit (autocomplete + history)
                line = self.session.prompt(self.prompt)

                # Process command
                line = self.precmd(line)
                stop = self.onecmd(line)
                stop = self.postcmd(stop, line)

            except KeyboardInterrupt:
                self.console.print("\nUse 'exit' or Ctrl+D to quit.", style="yellow")
            except EOFError:
                # Handle Ctrl+D
                stop = self.do_EOF("")

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
        print("\nInterrupted. Goodbye!")
    except Exception as exc:
        print(f"Shell error: {exc}", file=sys.stderr)
        sys.exit(1)
