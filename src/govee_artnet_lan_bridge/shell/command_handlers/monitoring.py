"""Monitoring and logging command handlers."""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from rich import box
from rich.table import Table
from rich.text import Text

from ...cli import _handle_response, _print_output
from ..ui_components import FIELD_DESCRIPTIONS
from . import CommandHandler


class MonitoringCommandHandler(CommandHandler):
    """Handler for monitoring, logging, and channel commands."""

    def do_channels(self, arg: str) -> None:
        """
        Channel commands: list channels for one or more universes.
        Usage: channels list [universe...]    # Default universe is 0
        Examples:
            channels list              # Show channels for universe 0
            channels list 1            # Show channels for universe 1
            channels list 0 1 2        # Show channels for universes 0, 1, and 2
        """
        if not self.client:
            self.shell._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        # Handle help aliases: channels --help, channels ?
        if arg.strip() in ("--help", "?"):
            self.shell.do_help("channels")
            return

        args = shlex.split(arg)
        if not args:
            self.shell._append_output("[yellow]Usage: channels list [universe...][/]" + "\n")
            return

        command = args[0]

        try:
            if command == "list":
                # Parse universe arguments (default to [0])
                universes = [0]
                if len(args) > 1:
                    # Parse one or more universe numbers
                    try:
                        universes = [int(u) for u in args[1:]]
                    except ValueError as e:
                        self.shell._append_output(f"[red]Invalid universe number: {e}[/]\n")
                        return

                self._show_channels_list(universes)
            else:
                self.shell._append_output(f"[red]Unknown command: channels {arg}[/]" + "\n")
                self.shell._append_output("[yellow]Try: channels list [universe...][/]" + "\n")
        except Exception as exc:
            self.shell._handle_error(exc, "channels")

    def _show_channels_list(self, universes: list[int] = None) -> None:
        """Show Artnet channels for the specified universe(s).

        Args:
            universes: List of ArtNet universe numbers (default [0])
        """
        if universes is None:
            universes = [0]

        try:
            # Fetch mappings and devices without caching for fresh IP data
            mappings_response = self.shell._cached_get("/mappings", use_cache=False)
            mappings = _handle_response(mappings_response)

            devices_response = self.shell._cached_get("/devices", use_cache=False)
            devices = _handle_response(devices_response)

            # Create device lookup by ID
            device_lookup = {d["id"]: d for d in devices} if devices else {}

            # Filter mappings for the specified universes
            universe_mappings = [m for m in mappings if m.get("universe") in universes]

            if not universe_mappings:
                universes_str = ", ".join(str(u) for u in universes)
                self.shell._append_output(f"[yellow]No mappings found for universe(s) {universes_str}[/]\n")
                return

            # Build channel map with universe information
            # channel_map: {(universe, channel_num): (device_id, function, mapping_id)}
            channel_map = {}

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
                mapping_id = mapping.get("id", "N/A")
                universe = mapping.get("universe", 0)
                start_channel = mapping.get("channel", 1)
                channel_length = mapping.get("length", 1)
                fields_list = mapping.get("fields", [])

                # Determine channel functions from the fields list
                # Try to match against known templates, otherwise use the field names directly
                fields_key = "".join(fields_list).lower() if fields_list else ""
                functions = TEMPLATE_FUNCTIONS.get(fields_key, [])

                # If no template match, derive functions from individual field names
                if not functions and fields_list:
                    # Map individual fields to display names
                    field_display = {
                        "r": "Red", "g": "Green", "b": "Blue", "w": "White",
                        "brightness": "Brightness", "temperature": "Color Temp", "ct": "Color Temp"
                    }
                    functions = [field_display.get(f, f.capitalize()) for f in fields_list]
                elif not functions:
                    # Fallback for unknown mappings
                    functions = [f"Ch{i+1}" for i in range(channel_length)]

                # Populate channel map
                for i in range(channel_length):
                    channel_num = start_channel + i
                    if 1 <= channel_num <= 512:
                        function = functions[i] if i < len(functions) else f"Ch{i+1}"
                        # Store with (universe, channel) as key, (device_id, function, mapping_id) as value
                        channel_map[(universe, channel_num)] = (device_id, function, mapping_id)

            if not channel_map:
                universes_str = ", ".join(str(u) for u in universes)
                self.shell._append_output(f"[yellow]No channels populated for universe(s) {universes_str}[/]\n")
                return

            # Create table with unicode borders
            universes_str = ", ".join(str(u) for u in sorted(universes))
            table = Table(
                title=Text(f"Artnet Channels - Universe {universes_str}", justify="center"),
                show_header=True,
                header_style="bold cyan",
                box=box.ROUNDED
            )
            table.add_column("Universe", style="dim", width=8, justify="right")
            table.add_column("Channel", style="cyan", width=8, justify="right")
            table.add_column("Device ID", style="yellow", width=23)
            table.add_column("IP Address", style="green", width=15)
            table.add_column("Name", style="blue", width=30)
            table.add_column("Function", style="magenta", width=15)
            table.add_column("Mapping ID", style="blue", width=12, justify="right")

            # Add rows for populated channels (sorted by universe, then channel number)
            for (universe, channel_num) in sorted(channel_map.keys()):
                device_id, function, mapping_id = channel_map[(universe, channel_num)]

                # Look up IP address and name dynamically from fresh device data
                device = device_lookup.get(device_id, {})
                device_ip = device.get("ip", "N/A")
                device_name = device.get("name", "")
                name_display = device_name if device_name else "[dim]-[/]"

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
                    str(universe),
                    str(channel_num),
                    device_id[:23],
                    device_ip,
                    name_display,
                    function_style,
                    str(mapping_id)
                )

            self.shell._append_output(table)

            # Calculate summary statistics
            total_channels = len(channel_map)
            channel_nums = [ch for (u, ch) in channel_map.keys()]
            min_channel = min(channel_nums) if channel_nums else 0
            max_channel = max(channel_nums) if channel_nums else 0

            self.shell._append_output(f"\n[dim]Total: {total_channels} populated channel(s)[/]\n")
            self.shell._append_output(f"[dim]Channel range: {min_channel} - {max_channel}[/]\n")

        except Exception as exc:
            self.shell._append_output(f"[red]Error fetching channels: {exc}[/]\n")

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
            self.shell._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        # Handle help aliases: logs --help, logs ?
        if arg.strip() in ("--help", "?"):
            self.shell.do_help("logs")
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
                    self.shell._append_output("[yellow]Usage: logs search PATTERN [--regex] [--case-sensitive] [--lines N][/]" + "\n")
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
                self.shell._append_output(f"[cyan]Found {data['count']} matching log entries:[/]" + "\n")
                self.shell._capture_api_output(_print_output, data["logs"], self.config.output)

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
                self.shell._append_output(f"[cyan]Showing {data['lines']} of {data['total']} log entries:[/]" + "\n")
                self.shell._capture_api_output(_print_output, data["logs"], self.config.output)

        except Exception as exc:
            self.shell._handle_error(exc, "logs")

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

        # Enter log tail mode (async)
        asyncio.create_task(self.shell._enter_log_tail_mode(level=level_filter, logger=logger_filter))

    def do_monitor(self, arg: str) -> None:
        """
        Real-time monitoring commands.
        Usage: monitor dashboard
               monitor stats
        """
        if not self.client:
            self.shell._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        # Handle help aliases: monitor --help, monitor ?
        if arg.strip() in ("--help", "?"):
            self.shell.do_help("monitor")
            return

        args = shlex.split(arg)
        if not args:
            self.shell._append_output("[yellow]Usage: monitor dashboard|stats[/]" + "\n")
            return

        command = args[0]

        try:
            if command == "dashboard":
                self._monitor_dashboard()
            elif command == "stats":
                self._monitor_stats()
            else:
                self.shell._append_output(f"[red]Unknown monitor command: {command}[/]" + "\n")
                self.shell._append_output("[yellow]Try: monitor dashboard, monitor stats[/]" + "\n")
        except Exception as exc:
            self.shell._handle_error(exc, "monitor")

    def _monitor_dashboard(self) -> None:
        """Display live dashboard with system status using rich formatting."""
        try:
            # Get health and status
            self.shell._append_output("[bold cyan]Fetching dashboard data...[/]\n")
            health_data = _handle_response(self.client.get("/health"))
            status_data = _handle_response(self.client.get("/status"))

            # Overall status
            overall_status = health_data.get("status", "unknown")
            status_style = "bold green" if overall_status == "ok" else "bold red"
            status_indicator = "✓" if overall_status == "ok" else "✗"

            # Create header
            self.shell._append_output("\n")
            self.shell._append_output("[bold cyan]" + "═" * 60 + "[/]\n")
            self.shell._append_output("[bold cyan]Govee ArtNet Bridge - Dashboard[/]\n")
            self.shell._append_output("[bold cyan]" + "═" * 60 + "[/]\n")
            self.shell._append_output(f"Status: [{status_style}]{status_indicator} {overall_status.upper()}[/]" + "\n")
            self.shell._append_output("\n")

            # Devices table
            devices_table = Table(title=Text("Devices", justify="center"), show_header=True, header_style="bold magenta", box=box.ROUNDED)
            devices_table.add_column("Type", style="cyan")
            devices_table.add_column("Count", justify="right", style="yellow")

            discovered_count = status_data.get("discovered_count", 0)
            manual_count = status_data.get("manual_count", 0)
            active_count = status_data.get("active_count", 0)
            devices_table.add_row("Active", str(active_count))
            devices_table.add_row("Discovered", str(discovered_count))
            devices_table.add_row("Manual", str(manual_count))
            devices_table.add_row("[bold]Total[/]", f"[bold]{discovered_count + manual_count}[/]")

            self.shell._append_output(devices_table)
            self.shell._append_output("\n\n")

            # Queue info
            queue_depth = status_data.get("queue_depth", 0)
            queue_style = "green" if queue_depth < 100 else "yellow" if queue_depth < 500 else "red"
            self.shell._append_output(f"Message Queue Depth: [{queue_style}]{queue_depth}[/]" + "\n")
            self.shell._append_output("\n")

            # Subsystems table
            subsystems = health_data.get("subsystems", {})
            if subsystems:
                subsystems_table = Table(title=Text("Subsystems", justify="center"), show_header=True, header_style="bold magenta", box=box.ROUNDED)
                subsystems_table.add_column("Name", style="cyan")
                subsystems_table.add_column("Status", style="green")

                for name, data in subsystems.items():
                    sub_status = data.get("status", "unknown")
                    indicator = "✓" if sub_status == "ok" else "✗"
                    status_style = "green" if sub_status == "ok" else "red"
                    subsystems_table.add_row(name, f"[{status_style}]{indicator} {sub_status}[/]")

                self.shell._append_output(subsystems_table)
                self.shell._append_output("\n\n")

        except Exception as exc:
            self.shell._append_output(f"[bold red]Error fetching dashboard:[/] {exc}" + "\n")

    def _monitor_stats(self) -> None:
        """Display system statistics."""
        self.shell._append_output("[cyan]Fetching statistics...[/]" + "\n")
        try:
            status_data = _handle_response(self.client.get("/status"))
            self.shell._capture_api_output(_print_output, status_data, self.config.output)
        except Exception as exc:
            self.shell._append_output(f"[red]Error fetching stats: {exc}[/]" + "\n")
