"""Interactive shell for govee-artnet CLI."""

from __future__ import annotations

import cmd
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.table import Table

from .cli import ClientConfig, _build_client, _handle_response, _print_output


class GoveeShell(cmd.Cmd):
    """Interactive shell for the Govee ArtNet bridge."""

    intro = "Govee ArtNet Bridge Shell. Type 'help' or '?' for commands, 'exit' to quit."
    prompt = "govee> "

    def __init__(self, config: ClientConfig):
        """
        Initialize the shell.

        Args:
            config: Client configuration
        """
        super().__init__()
        self.config = config
        self.client: Optional[httpx.Client] = None
        self.console = Console()

        # Set up command history
        history_dir = Path.home() / ".govee_artnet"
        history_dir.mkdir(exist_ok=True)
        history_file = history_dir / "shell_history"

        # Set up autocomplete with all command names
        commands = [
            "connect", "disconnect", "status", "health",
            "devices", "mappings", "logs", "monitor",
            "output", "clear", "exit", "quit", "help"
        ]
        completer = WordCompleter(commands, ignore_case=True)

        # Create prompt session with history and autocomplete
        self.session: PromptSession = PromptSession(
            history=FileHistory(str(history_file)),
            completer=completer,
            complete_while_typing=True,
        )

        self._connect()

    def _connect(self) -> None:
        """Establish connection to the bridge server."""
        try:
            self.client = _build_client(self.config)
            # Test connection
            response = self.client.get("/health")
            response.raise_for_status()
            print(f"Connected to {self.config.server_url}")
        except Exception as exc:
            print(f"Warning: Could not connect to {self.config.server_url}: {exc}")
            print("Some commands may not work. Use 'connect' to retry.")
            self.client = None

    def do_connect(self, arg: str) -> None:
        """Connect or reconnect to the bridge server."""
        if arg:
            # Update server URL if provided
            self.config = ClientConfig(
                server_url=arg,
                api_key=self.config.api_key,
                api_bearer_token=self.config.api_bearer_token,
                output=self.config.output,
            )
        self._connect()

    def do_disconnect(self, arg: str) -> None:
        """Disconnect from the bridge server."""
        if self.client:
            self.client.close()
            self.client = None
            print("Disconnected")

    def do_status(self, arg: str) -> None:
        """Show connection status and bridge status."""
        if not self.client:
            print(f"Not connected. Server URL: {self.config.server_url}")
            return

        try:
            data = _handle_response(self.client.get("/status"))
            _print_output(data, self.config.output)
        except Exception as exc:
            print(f"Error: {exc}")

    def do_health(self, arg: str) -> None:
        """Check bridge health."""
        if not self.client:
            print("Not connected. Use 'connect' first.")
            return

        try:
            data = _handle_response(self.client.get("/health"))
            _print_output(data, self.config.output)
        except Exception as exc:
            print(f"Error: {exc}")

    def do_devices(self, arg: str) -> None:
        """
        Device commands: list, add, update, enable, disable, command.
        Usage: devices list
               devices enable <device_id>
               devices disable <device_id>
        """
        if not self.client:
            print("Not connected. Use 'connect' first.")
            return

        args = shlex.split(arg)
        if not args:
            print("Usage: devices <command> [args...]")
            return

        command = args[0]

        try:
            if command == "list":
                data = _handle_response(self.client.get("/devices"))
                _print_output(data, self.config.output)
            elif command == "enable" and len(args) >= 2:
                device_id = args[1]
                data = _handle_response(
                    self.client.patch(f"/devices/{device_id}", json={"enabled": True})
                )
                _print_output(data, self.config.output)
            elif command == "disable" and len(args) >= 2:
                device_id = args[1]
                data = _handle_response(
                    self.client.patch(f"/devices/{device_id}", json={"enabled": False})
                )
                _print_output(data, self.config.output)
            else:
                print(f"Unknown or incomplete command: devices {arg}")
                print("Try: devices list, devices enable <id>, devices disable <id>")
        except Exception as exc:
            print(f"Error: {exc}")

    def do_mappings(self, arg: str) -> None:
        """
        Mapping commands: list, get, delete, channel-map.
        Usage: mappings list
               mappings get <id>
               mappings delete <id>
               mappings channel-map
        """
        if not self.client:
            print("Not connected. Use 'connect' first.")
            return

        args = shlex.split(arg)
        if not args:
            print("Usage: mappings <command> [args...]")
            return

        command = args[0]

        try:
            if command == "list":
                data = _handle_response(self.client.get("/mappings"))
                _print_output(data, self.config.output)
            elif command == "get" and len(args) >= 2:
                mapping_id = args[1]
                data = _handle_response(self.client.get(f"/mappings/{mapping_id}"))
                _print_output(data, self.config.output)
            elif command == "delete" and len(args) >= 2:
                mapping_id = args[1]
                _handle_response(self.client.delete(f"/mappings/{mapping_id}"))
                print(f"Mapping {mapping_id} deleted")
            elif command == "channel-map":
                data = _handle_response(self.client.get("/channel-map"))
                _print_output(data, self.config.output)
            else:
                print(f"Unknown or incomplete command: mappings {arg}")
                print("Try: mappings list, mappings get <id>, mappings delete <id>, mappings channel-map")
        except Exception as exc:
            print(f"Error: {exc}")

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
            print("Not connected. Use 'connect' first.")
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
                    print("Usage: logs search PATTERN [--regex] [--case-sensitive] [--lines N]")
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
                print(f"Found {data['count']} matching log entries:")
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
                print(f"Showing {data['lines']} of {data['total']} log entries:")
                _print_output(data["logs"], self.config.output)

        except Exception as exc:
            print(f"Error: {exc}")

    def _logs_tail(self, args: list[str]) -> None:
        """
        Tail logs in real-time using WebSocket.

        Args:
            args: Command arguments (filters)
        """
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

        print("Streaming logs (Press Ctrl+C to stop)...")
        if level_filter:
            print(f"  Level filter: {level_filter}")
        if logger_filter:
            print(f"  Logger filter: {logger_filter}")
        print()

        try:
            import websockets.sync.client as ws_client

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
                        message = websocket.recv(timeout=1.0)
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
            print("\nStopped tailing logs")
        except ImportError:
            print("Error: websockets library not installed")
            print("Install with: pip install websockets")
        except Exception as exc:
            print(f"Error streaming logs: {exc}")

    def do_monitor(self, arg: str) -> None:
        """
        Real-time monitoring commands.
        Usage: monitor dashboard
               monitor stats
        """
        if not self.client:
            print("Not connected. Use 'connect' first.")
            return

        args = shlex.split(arg)
        if not args:
            print("Usage: monitor dashboard|stats")
            return

        command = args[0]

        try:
            if command == "dashboard":
                self._monitor_dashboard()
            elif command == "stats":
                self._monitor_stats()
            else:
                print(f"Unknown monitor command: {command}")
                print("Try: monitor dashboard, monitor stats")
        except Exception as exc:
            print(f"Error: {exc}")

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
        print("Fetching statistics...")
        try:
            status_data = _handle_response(self.client.get("/status"))
            _print_output(status_data, self.config.output)
        except Exception as exc:
            print(f"Error fetching stats: {exc}")

    def do_output(self, arg: str) -> None:
        """
        Set output format: json, yaml, or table.
        Usage: output json|yaml|table
        """
        args = shlex.split(arg)
        if not args or args[0] not in ("json", "yaml", "table"):
            print("Usage: output json|yaml|table")
            print(f"Current format: {self.config.output}")
            return

        new_format = args[0]
        self.config = ClientConfig(
            server_url=self.config.server_url,
            api_key=self.config.api_key,
            api_bearer_token=self.config.api_bearer_token,
            output=new_format,
        )
        print(f"Output format set to: {new_format}")

    def do_help(self, arg: str) -> None:
        """Show help for commands with examples."""
        if arg:
            # Show help for specific command
            super().do_help(arg)
            return

        # Show enhanced help with examples using rich
        self.console.print()
        self.console.rule("[bold cyan]Govee ArtNet Bridge Shell - Command Reference")
        self.console.print()

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
            "output",
            "Set output format",
            "output table\noutput json\noutput yaml"
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

        self.console.print(help_table)
        self.console.print()
        self.console.print("[dim]Type 'help <command>' for detailed help on a specific command.[/]")
        self.console.print()

    def do_clear(self, arg: str) -> None:
        """Clear the screen."""
        os.system("cls" if os.name == "nt" else "clear")

    def do_exit(self, arg: str) -> bool:
        """Exit the shell."""
        if self.client:
            self.client.close()
        print("Goodbye!")
        return True

    def do_quit(self, arg: str) -> bool:
        """Exit the shell (alias for exit)."""
        return self.do_exit(arg)

    def do_EOF(self, arg: str) -> bool:
        """Handle Ctrl+D."""
        print()  # Print newline
        return self.do_exit(arg)

    def cmdloop(self, intro: Optional[str] = None) -> None:
        """
        Override cmdloop to use prompt_toolkit for better UX.

        Args:
            intro: Introduction message (optional)
        """
        # Print intro
        if intro is None:
            intro = self.intro
        if intro:
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

    def emptyline(self) -> None:
        """Do nothing on empty line."""
        pass

    def default(self, line: str) -> None:
        """Handle unknown commands."""
        print(f"Unknown command: {line}")
        print("Type 'help' or '?' for available commands.")


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
