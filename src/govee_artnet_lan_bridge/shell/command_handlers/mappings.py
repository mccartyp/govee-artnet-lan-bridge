"""Mapping management command handlers."""

from __future__ import annotations

import shlex
from typing import Any

from rich import box
from rich.table import Table
from rich.text import Text

from ...cli import _api_delete, _api_get_by_id, _handle_response
from ..ui_components import FIELD_DESCRIPTIONS
from . import CommandHandler


class MappingCommandHandler(CommandHandler):
    """Handler for mapping-related commands."""

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
        if not self.client:
            self.shell._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        # Handle help aliases: mappings --help, mappings ?
        if arg.strip() in ("--help", "?"):
            self.shell.do_help("mappings")
            return

        args = shlex.split(arg)
        if not args:
            self.shell._append_output("[yellow]Usage: mappings <command> [args...][/]" + "\n")
            return

        command = args[0]

        try:
            if command == "list":
                self._show_mappings_list()
            elif command == "get" and len(args) >= 2:
                mapping_id = args[1]
                self.shell._capture_api_output(_api_get_by_id, self.client, "/mappings", mapping_id, self.config)
            elif command == "create":
                # Handle help aliases: mappings create --help, mappings create ?
                if len(args) >= 2 and args[1] in ("--help", "?"):
                    self.shell.do_help("mappings create")
                    return
                self._create_mapping(args[1:])
            elif command == "delete" and len(args) >= 2:
                mapping_id = args[1]
                self.shell._capture_api_output(_api_delete, self.client, "/mappings", mapping_id, self.config)
                # Invalidate mappings cache after mutation
                self.shell._invalidate_cache("/mappings")
                self.shell._invalidate_cache("/channel-map")
                self.shell._append_output(f"[green]Mapping {mapping_id} deleted[/]" + "\n")
            elif command == "channel-map":
                from ...cli import _api_get
                self.shell._capture_api_output(_api_get, self.client, "/channel-map", self.config)
            else:
                self.shell._append_output(f"[red]Unknown or incomplete command: mappings {arg}[/]" + "\n")
                self.shell._append_output("[yellow]Try: mappings list, mappings get <id>, mappings create --help, mappings delete <id>, mappings channel-map[/]" + "\n")
        except Exception as exc:
            self.shell._handle_error(exc, "mappings")

    def _show_mappings_list(self) -> None:
        """Show mappings list with unicode table borders."""
        try:
            response = self.client.get("/mappings")
            mappings = _handle_response(response)

            if not mappings:
                self.shell._append_output("[yellow]No mappings found[/]\n")
                return

            # Fetch devices to get names
            devices_response = self.client.get("/devices")
            devices = _handle_response(devices_response)
            device_lookup = {d["id"]: d for d in devices} if devices else {}

            # Create table with unicode borders
            table = Table(title=Text("ArtNet Mappings", justify="center"), show_header=True, header_style="bold cyan", box=box.ROUNDED)
            table.add_column("Mapping ID", style="cyan", width=12)
            table.add_column("Device ID", style="yellow", width=23)
            table.add_column("Name", style="blue", width=15)
            table.add_column("Universe", style="green", width=8, justify="right")
            table.add_column("Channel", style="magenta", width=8, justify="right")
            table.add_column("Length", style="blue", width=6, justify="right")
            table.add_column("Fields", style="white", width=25)

            # Add mapping rows
            for mapping in mappings:
                # Format fields list for display with pretty names
                fields = mapping.get("fields", [])
                if fields:
                    pretty_fields = [FIELD_DESCRIPTIONS.get(f, f.capitalize()) for f in fields]
                    fields_str = ", ".join(pretty_fields)
                else:
                    fields_str = "N/A"

                # Look up device name
                device_id = mapping.get("device_id", "N/A")
                device = device_lookup.get(device_id, {})
                device_name = device.get("name", "")
                name_display = device_name if device_name else "[dim]-[/]"

                table.add_row(
                    str(mapping.get("id", "N/A")),
                    str(device_id)[:23],
                    name_display,
                    str(mapping.get("universe", "N/A")),
                    str(mapping.get("channel", "N/A")),
                    str(mapping.get("length", "N/A")),
                    fields_str
                )

            self.shell._append_output(table)
            self.shell._append_output(f"\n[dim]Total: {len(mappings)} mapping(s)[/]\n")

        except Exception as exc:
            self.shell._append_output(f"[red]Error fetching mappings: {exc}[/]\n")

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
                self.shell._append_output("[cyan]Mappings Create Help[/]\n")
                self.shell._append_output("\n[bold]Template-based (recommended for multi-channel mappings):[/]\n")
                self.shell._append_output("  mappings create --device-id <id> [--universe <num>] --template <name> --start-channel <num>\n")
                self.shell._append_output("\n[bold]Available templates:[/]\n")
                self.shell._append_output("  • rgb             - 3 channels: Red, Green, Blue\n")
                self.shell._append_output("  • rgbw            - 4 channels: Red, Green, Blue, White\n")
                self.shell._append_output("  • brightness_rgb  - 4 channels: Brightness, Red, Green, Blue\n")
                self.shell._append_output("  • rgbwa           - 5 channels: Red, Green, Blue, White, Brightness\n")
                self.shell._append_output("  • rgbaw           - 5 channels: Brightness, Red, Green, Blue, White\n")
                self.shell._append_output("  • brgbwct         - 6 channels: Brightness, Red, Green, Blue, White, Color Temp\n")
                self.shell._append_output("\n[bold]Single channel mappings (recommended for individual control):[/]\n")
                self.shell._append_output("  mappings create --device-id <id> [--universe <num>] --channel <num> --field <field>\n")
                self.shell._append_output("\n[bold]Multi-channel range mappings:[/]\n")
                self.shell._append_output("  mappings create --device-id <id> [--universe <num>] --channel <num> --length <num>\n")
                self.shell._append_output("\n[bold]Available fields (for single channel mappings):[/]\n")
                self.shell._append_output("  • power              - Power on/off (DMX >= 128 = on, < 128 = off) [all devices]\n")
                self.shell._append_output("  • brightness         - Brightness control (0-255) [requires brightness capability]\n")
                self.shell._append_output("  • r (or red)         - Red channel only [requires color capability]\n")
                self.shell._append_output("  • g (or green)       - Green channel only [requires color capability]\n")
                self.shell._append_output("  • b (or blue)        - Blue channel only [requires color capability]\n")
                self.shell._append_output("  • w (or white)       - White channel only [requires white capability]\n")
                self.shell._append_output("  • ct (or color_temp) - Color temperature in Kelvin [requires color_temp capability]\n")
                self.shell._append_output("\n[bold]Notes:[/]\n")
                self.shell._append_output("  • Universe defaults to 0 if omitted\n")
                self.shell._append_output("  • Templates are for multi-channel mappings only\n")
                self.shell._append_output("  • Use single channel mappings for individual field control\n")
                self.shell._append_output("  • Device capabilities are validated - mappings will fail if unsupported\n")
                self.shell._append_output("  • Use 'devices list' to check device capabilities\n")
                self.shell._append_output("\n[bold]Examples:[/]\n")
                self.shell._append_output("  # Template-based multi-channel mapping\n")
                self.shell._append_output("  mappings create --device-id AA:BB:CC:DD:EE:FF --template rgb --start-channel 1\n")
                self.shell._append_output("  mappings create --device-id @kitchen --universe 1 --template rgbw --start-channel 10\n")
                self.shell._append_output("\n  # Single channel mappings\n")
                self.shell._append_output("  mappings create --device-id AA:BB:CC:DD:EE:FF --channel 1 --field power\n")
                self.shell._append_output("  mappings create --device-id AA:BB:CC:DD:EE:FF --channel 5 --field brightness\n")
                self.shell._append_output("  mappings create --device-id @kitchen --channel 20 --field w\n")
                self.shell._append_output("  mappings create --device-id @kitchen --channel 21 --field red\n")
                self.shell._append_output("\n  # Manual multi-channel range mapping\n")
                self.shell._append_output("  mappings create --device-id AA:BB:CC:DD:EE:FF --channel 1 --length 3\n")
                return
            elif arg == "--device-id" and i + 1 < len(args):
                device_id = self.shell._resolve_bookmark(args[i + 1])
                i += 2
            elif arg == "--universe" and i + 1 < len(args):
                try:
                    universe = int(args[i + 1])
                except ValueError:
                    self.shell._append_output(f"[red]Invalid universe number: {args[i + 1]}[/]\n")
                    return
                i += 2
            elif arg == "--start-channel" and i + 1 < len(args):
                try:
                    start_channel = int(args[i + 1])
                except ValueError:
                    self.shell._append_output(f"[red]Invalid start channel: {args[i + 1]}[/]\n")
                    return
                i += 2
            elif arg == "--channel" and i + 1 < len(args):
                try:
                    channel = int(args[i + 1])
                except ValueError:
                    self.shell._append_output(f"[red]Invalid channel: {args[i + 1]}[/]\n")
                    return
                i += 2
            elif arg == "--length" and i + 1 < len(args):
                try:
                    length = int(args[i + 1])
                except ValueError:
                    self.shell._append_output(f"[red]Invalid length: {args[i + 1]}[/]\n")
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
                self.shell._append_output(f"[red]Unknown argument: {arg}[/]\n")
                self.shell._append_output("[yellow]Use 'mappings create --help' for usage information[/]\n")
                return

        # Validate required fields
        if not device_id:
            self.shell._append_output("[red]Error: --device-id is required[/]\n")
            return

        # Build payload
        payload: dict[str, Any] = {
            "device_id": device_id,
            "universe": universe,
            "allow_overlap": allow_overlap,
        }

        if template:
            # Template-based mapping
            # Note: Template validation is done by the backend
            # Valid templates: rgb, rgbw, brightness_rgb, master_only, rgbwa, rgbaw, full

            if start_channel is None and channel is None:
                self.shell._append_output("[red]Error: --start-channel (or --channel) is required when using a template[/]\n")
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
                    self.shell._append_output("[red]Error: --channel is required when not using a template[/]\n")
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
            self.shell._invalidate_cache("/mappings")
            self.shell._invalidate_cache("/channel-map")

            # Show success message with details
            if template:
                self.shell._append_output(f"[green]✓ Created {template} mapping for device {device_id}[/]\n")
            else:
                self.shell._append_output(f"[green]✓ Created mapping for device {device_id}[/]\n")

            # Show the created mapping details
            if isinstance(data, list):
                # Template-based mapping returns a list of channel mappings
                mapping_ids = [str(m.get('id', 'N/A')) for m in data]
                self.shell._append_output(f"[dim]Created {len(data)} channel mappings (IDs: {', '.join(mapping_ids)})[/]\n")
            else:
                # Manual mapping returns a single mapping object
                self.shell._append_output(f"[dim]Mapping ID: {data.get('id', 'N/A')}[/]\n")

        except Exception as exc:
            self.shell._append_output(f"[red]Error creating mapping: {exc}[/]\n")
