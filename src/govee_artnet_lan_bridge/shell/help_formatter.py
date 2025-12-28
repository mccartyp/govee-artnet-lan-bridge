"""Help system for the Govee ArtNet shell.

This module handles formatting and displaying help information for
shell commands, including command-specific help and the main help table.
"""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

from prompt_toolkit.document import Document
from rich import box
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from .core import GoveeShell


class HelpFormatter:
    """Manages help formatting and display for shell commands."""

    def __init__(self, shell: GoveeShell):
        """
        Initialize the help formatter.

        Args:
            shell: Reference to the GoveeShell instance
        """
        self.shell = shell

    def format_command_help(self, command: str, docstring: str) -> str:
        """
        Format command help with colors and styling using rich.

        Args:
            command: The command name
            docstring: The command's docstring

        Returns:
            Formatted help text with ANSI color codes
        """
        buffer = StringIO()
        temp_console = Console(file=buffer, force_terminal=True, width=self.shell.console.width)

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

    def show_command_help(self, main_command: str, subcommand: str | None = None) -> None:
        """
        Show help for a specific command or subcommand.

        Args:
            main_command: The main command name
            subcommand: Optional subcommand name
        """
        # Handle special subcommand help cases
        if subcommand:
            if main_command == "mappings" and subcommand == "create":
                self._show_mappings_create_help()
                return
            else:
                # For other subcommands, show a note and fall through to main command help
                self.shell._append_output(f"[yellow]No specific help for '{main_command} {subcommand}'[/]\n")
                self.shell._append_output(f"[dim]Showing help for '{main_command}' instead...[/]\n\n")

        # Show help for the main command
        handler = self.shell.commands.get(main_command)
        if handler:
            # Get the docstring from the handler
            docstring = handler.__doc__
            if docstring:
                # Format with colors and styling (returns ANSI-formatted text)
                help_text = self.format_command_help(main_command, docstring)
                # Append directly to buffer (already ANSI-formatted)
                current_text = self.shell.output_buffer.text
                new_text = current_text + help_text
                if not help_text.endswith('\n'):
                    new_text += '\n'
                # Respect follow-tail mode
                cursor_pos = len(new_text) if self.shell.follow_tail else min(self.shell.output_buffer.cursor_position, len(new_text))
                self.shell.output_buffer.set_document(
                    Document(text=new_text, cursor_position=cursor_pos),
                    bypass_readonly=True
                )
                self.shell.app.invalidate()
            else:
                # Rich markup, use _append_output
                self.shell._append_output(f"\n[yellow]No help available for command '{main_command}'[/]\n")
        else:
            self.shell._append_output(f"[red]Unknown command: {main_command}[/]" + "\n")
            self.shell._append_output("[dim]Type 'help' to see all available commands.[/]" + "\n")

    def show_full_help(self) -> None:
        """Display the full command reference help table."""
        # Capture output to a string buffer for pagination
        buffer = StringIO()
        temp_console = Console(file=buffer, force_terminal=True, width=self.shell.console.width)

        temp_console.print("═" * self.shell.console.width)
        temp_console.print("Govee ArtNet Bridge Shell - Command Reference", style="bold cyan", justify="center")
        temp_console.print("═" * self.shell.console.width)

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
            "devices list\ndevices list --state active\ndevices list detailed --id AA:BB\ndevices enable <id>\ndevices disable <id>\ndevices set-name <id> \"Name\"\ndevices command <id> --on\ndevices command <id> --brightness 200"
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

        # Append to output buffer (already ANSI-formatted)
        output = buffer.getvalue()
        current_text = self.shell.output_buffer.text
        new_text = current_text + output
        if not output.endswith('\n'):
            new_text += '\n'
        # Respect follow-tail mode
        cursor_pos = len(new_text) if self.shell.follow_tail else min(self.shell.output_buffer.cursor_position, len(new_text))
        self.shell.output_buffer.set_document(
            Document(text=new_text, cursor_position=cursor_pos),
            bypass_readonly=True
        )
        self.shell.app.invalidate()

    def _show_mappings_create_help(self) -> None:
        """Show detailed help for the 'mappings create' command."""
        self.shell._append_output("[cyan]Mappings Create Help[/]\n")
        self.shell._append_output("\n[bold]Template-based (recommended):[/]\n")
        self.shell._append_output("  mappings create --device-id <id> [--universe <num>] --template <name> --start-channel <num>\n")
        self.shell._append_output("\n[bold]Available templates:[/]\n")
        self.shell._append_output("  • rgb             - 3 channels: Red, Green, Blue\n")
        self.shell._append_output("  • rgbw            - 4 channels: Red, Green, Blue, White\n")
        self.shell._append_output("  • brightness_rgb  - 4 channels: Brightness, Red, Green, Blue\n")
        self.shell._append_output("  • master_only     - 1 channel: Brightness\n")
        self.shell._append_output("  • rgbwa           - 5 channels: Red, Green, Blue, White, Brightness\n")
        self.shell._append_output("  • rgbaw           - 5 channels: Brightness, Red, Green, Blue, White\n")
        self.shell._append_output("  • full            - 6 channels: Brightness, Red, Green, Blue, White, Color Temp\n")
        self.shell._append_output("\n[bold]Manual configuration for single fields:[/]\n")
        self.shell._append_output("  mappings create --device-id <id> --universe <num> --channel <num> --type discrete --field <field>\n")
        self.shell._append_output("\n[bold]Options:[/]\n")
        self.shell._append_output("  --device-id <id>        Device identifier (required)\n")
        self.shell._append_output("  --universe <num>        ArtNet universe (default: 0)\n")
        self.shell._append_output("  --template <name>       Use a template for multi-channel mapping\n")
        self.shell._append_output("  --start-channel <num>   Starting Artnet channel for template\n")
        self.shell._append_output("  --channel <num>         Artnet channel for manual mapping\n")
        self.shell._append_output("  --length <num>          Number of channels (for manual range)\n")
        self.shell._append_output("  --type <type>           Mapping type: range or discrete\n")
        self.shell._append_output("  --field <field>         Field name (r, g, b, w, brightness, ct)\n")
        self.shell._append_output("  --allow-overlap         Allow overlapping channel ranges\n")
