"""Device management command handlers."""

from __future__ import annotations

import shlex
import string
from typing import Any, MutableMapping, Optional

from rich import box
from rich.table import Table
from rich.text import Text

from ...cli import _device_set_enabled, _handle_response
from . import CommandHandler


class DeviceCommandHandler(CommandHandler):
    """Handler for device-related commands."""

    def do_devices(self, arg: str) -> None:
        """
        Device commands: list, list detailed, enable, disable, set-name, set-capabilities, command.
        Usage: devices list [--id ID] [--ip IP] [--state STATE]              # Show simplified 2-line view
               devices list detailed [--id ID] [--ip IP] [--state STATE]     # Show full device details
               devices enable <device_id>
               devices disable <device_id>
               devices set-name <device_id> <name>                          # Set device name (use "" to clear)
               devices set-capabilities <device_id> --brightness <bool> --color <bool> --white <bool> --color-temp <bool>
               devices command <device_id> [--on|--off] [--brightness N] [--color HEX] [--ct N]
        Examples:
            devices list
            devices list --id AA:BB:CC:DD:EE:FFC
            devices list --ip 192.168.1.100
            devices list --state active
            devices list detailed --state offline
            devices set-name AA:BB:CC:DD:EE:FF "Kitchen Light"
            devices set-name AA:BB:CC:DD:EE:FF ""                            # Clear name
            devices set-capabilities AA:BB:CC:DD:EE:FF --brightness true --color true --white false
            devices command AA:BB:CC:DD:EE:FF --on --brightness 200 --color #FF00FF
            devices command AA:BB:CC:DD:EE:FF --off
            devices command AA:BB:CC:DD:EE:FF --color ff8800 --brightness 128
            devices command AA:BB:CC:DD:EE:FF --ct 128
        """
        if not self.client:
            self.shell._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        # Handle help aliases: devices --help, devices ?
        if arg.strip() in ("--help", "?"):
            self.shell.do_help("devices")
            return

        args = shlex.split(arg)
        if not args:
            self.shell._append_output("[yellow]Usage: devices <command> [args...][/]" + "\n")
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
                self.shell._capture_api_output(_device_set_enabled, self.client, device_id, True, self.config)
                # Invalidate devices cache after mutation
                self.shell._invalidate_cache("/devices")
            elif command == "disable" and len(args) >= 2:
                device_id = args[1]
                self.shell._capture_api_output(_device_set_enabled, self.client, device_id, False, self.config)
                # Invalidate devices cache after mutation
                self.shell._invalidate_cache("/devices")
            elif command == "set-name" and len(args) >= 3:
                device_id = args[1]
                name = args[2]
                # Empty string clears the name (set to NULL)
                if name == "":
                    name = None
                # Call API to update device name
                payload = {"name": name}
                response = self.client.patch(f"/devices/{device_id}", json=payload)
                if response.status_code == 200:
                    self.shell._invalidate_cache("/devices")
                    if name:
                        self.shell._append_output(f"[green]Device name set to '{name}'[/]\n")
                    else:
                        self.shell._append_output(f"[green]Device name cleared[/]\n")
                else:
                    self.shell._append_output(f"[red]Failed to set device name: {response.status_code}[/]\n")
            elif command == "set-capabilities" and len(args) >= 2:
                device_id = args[1]

                # Parse capability flags
                capabilities = {}
                i = 2
                while i < len(args):
                    if args[i] == "--brightness" and i + 1 < len(args):
                        capabilities["brightness"] = args[i + 1].lower() in ("true", "1", "yes")
                        i += 2
                    elif args[i] == "--color" and i + 1 < len(args):
                        capabilities["color"] = args[i + 1].lower() in ("true", "1", "yes")
                        i += 2
                    elif args[i] == "--white" and i + 1 < len(args):
                        capabilities["white"] = args[i + 1].lower() in ("true", "1", "yes")
                        i += 2
                    elif args[i] == "--color-temp" and i + 1 < len(args):
                        capabilities["color_temp"] = args[i + 1].lower() in ("true", "1", "yes")
                        i += 2
                    else:
                        self.shell._append_output(f"[red]Unknown flag: {args[i]}[/]\n")
                        return

                if not capabilities:
                    self.shell._append_output("[red]Error: At least one capability flag must be provided[/]\n")
                    self.shell._append_output("[yellow]Available flags: --brightness, --color, --white, --color-temp[/]\n")
                    return

                # Call API to update device capabilities
                payload = {"capabilities": capabilities}
                response = self.client.patch(f"/devices/{device_id}", json=payload)
                if response.status_code == 200:
                    self.shell._invalidate_cache("/devices")
                    caps_list = ", ".join([f"{k}={v}" for k, v in capabilities.items()])
                    self.shell._append_output(f"[green]Device capabilities updated: {caps_list}[/]\n")
                else:
                    self.shell._append_output(f"[red]Failed to update device capabilities: {response.status_code}[/]\n")
            elif command == "command" and len(args) >= 2:
                device_id = args[1]

                # Parse command flags
                power_on = False
                power_off = False
                brightness = None
                color = None
                ct = None

                i = 2
                while i < len(args):
                    if args[i] == "--on":
                        power_on = True
                        i += 1
                    elif args[i] == "--off":
                        power_off = True
                        i += 1
                    elif args[i] == "--brightness" and i + 1 < len(args):
                        try:
                            brightness = int(args[i + 1])
                        except ValueError:
                            self.shell._append_output(f"[red]Invalid brightness value: {args[i + 1]}[/]\n")
                            return
                        i += 2
                    elif args[i] == "--color" and i + 1 < len(args):
                        color = args[i + 1]
                        i += 2
                    elif args[i] in ("--ct", "--kelvin") and i + 1 < len(args):
                        try:
                            ct = int(args[i + 1])
                        except ValueError:
                            self.shell._append_output(f"[red]Invalid color temperature value: {args[i + 1]}[/]\n")
                            return
                        i += 2
                    else:
                        self.shell._append_output(f"[red]Unknown flag: {args[i]}[/]\n")
                        return

                # Validate input
                if power_on and power_off:
                    self.shell._append_output("[red]Choose either --on or --off, not both[/]\n")
                    return

                if not any([power_on, power_off, brightness is not None, color, ct is not None]):
                    self.shell._append_output("[red]At least one action is required (--on, --off, --brightness, --color, --ct)[/]\n")
                    return

                # Validate brightness range
                if brightness is not None:
                    if brightness < 0 or brightness > 255:
                        self.shell._append_output("[red]Brightness must be between 0 and 255[/]\n")
                        return

                # Validate color temperature range
                if ct is not None:
                    if ct < 0 or ct > 255:
                        self.shell._append_output("[red]Color temperature must be between 0 and 255[/]\n")
                        return

                # Normalize color hex
                if color:
                    color = self._normalize_color_hex(color)
                    if color is None:
                        self.shell._append_output("[red]Color must be a hex value like ff3366, #ff3366, or #F0F[/]\n")
                        return

                # Build payload
                payload: MutableMapping[str, Any] = {}
                if power_on:
                    payload["on"] = True
                if power_off:
                    payload["off"] = True
                if brightness is not None:
                    payload["brightness"] = brightness
                if color:
                    payload["color"] = color
                if ct is not None:
                    payload["kelvin"] = ct

                # Send command to API
                response = self.client.post(f"/devices/{device_id}/command", json=payload)
                if response.status_code == 200:
                    self.shell._append_output(f"[green]Command sent successfully to {device_id}[/]\n")
                else:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("detail", f"HTTP {response.status_code}")
                        self.shell._append_output(f"[red]Failed to send command: {error_msg}[/]\n")
                    except Exception:
                        self.shell._append_output(f"[red]Failed to send command: HTTP {response.status_code}[/]\n")
            else:
                self.shell._append_output(f"[red]Unknown or incomplete command: devices {arg}[/]" + "\n")
                self.shell._append_output("[yellow]Try: devices list, devices enable <id>, devices disable <id>, devices set-name <id> <name>, devices command <id> [flags][/]" + "\n")
        except Exception as exc:
            self.shell._handle_error(exc, "devices")

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
                self.shell._append_output("[yellow]No devices found[/]\n")
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
                self.shell._append_output("[yellow]No devices match the filters[/]\n")
                return

            # Create simplified table (reordered columns, removed Enabled/Configured, added Last Seen)
            table = Table(title=Text("Devices", justify="center"), show_header=True, header_style="bold cyan", box=box.ROUNDED)
            table.add_column("Device ID", style="cyan", width=23, no_wrap=True)
            table.add_column("IP Address", style="green", width=15)
            table.add_column("Name", style="blue", width=20)
            table.add_column("Model", style="yellow", width=15)
            table.add_column("State", style="white", width=20)
            table.add_column("Last Seen", style="magenta", width=18)

            # Add device rows
            for device in devices:
                device_id = device.get("id", "N/A")[:23]  # Truncate long IDs
                model = device.get("model_number", "Unknown")
                ip_address = device.get("ip", "N/A")
                name = device.get("name", "")
                name_display = name if name else "[dim]-[/]"

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
                    name_display,
                    model,
                    state_str,
                    last_seen_age
                )

            self.shell._append_output(table)
            self.shell._append_output(f"\n[dim]Total: {len(devices)} device(s). Use 'devices list detailed' for full info.[/]\n")

        except Exception as exc:
            self.shell._append_output(f"[red]Error fetching devices: {exc}[/]\n")

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
                self.shell._append_output("[yellow]No devices found[/]\n")
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
                self.shell._append_output("[yellow]No devices match the filters[/]\n")
                return

            # Print each device as a card
            for idx, device in enumerate(devices):
                # Create a table for this device
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Field", style="bold cyan", width=20)
                table.add_column("Value", style="yellow")

                # Key fields to display in order with colors
                key_fields = [
                    ("Device ID", "id"),
                    ("IP", "ip"),
                    ("Name", "name"),
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
                self.shell._append_output(header_text + "\n")
                self.shell._append_output(table)

                # Add separator between devices
                if idx < len(devices) - 1:
                    self.shell._append_output("\n[dim]" + "─" * 80 + "[/]\n")

            self.shell._append_output(f"\n[dim]Total: {len(devices)} device(s).[/]\n")

        except Exception as exc:
            self.shell._append_output(f"[red]Error fetching devices: {exc}[/]\n")

    def _normalize_color_hex(self, value: str) -> Optional[str]:
        """Normalize a color hex string.

        Args:
            value: Hex color string (e.g., "ff3366", "#ff3366", "F0F")

        Returns:
            Normalized hex string (lowercase, without #), or None if invalid
        """
        normalized = value.strip()
        if normalized.startswith("#"):
            normalized = normalized[1:]
        if len(normalized) == 3:
            # Expand shorthand (e.g., "F0F" -> "FF00FF")
            normalized = "".join(ch * 2 for ch in normalized)
        if len(normalized) != 6 or any(ch not in string.hexdigits for ch in normalized):
            return None
        return normalized.lower()
