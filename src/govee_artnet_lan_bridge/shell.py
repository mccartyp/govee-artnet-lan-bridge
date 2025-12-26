"""Interactive shell for govee-artnet CLI."""

from __future__ import annotations

import cmd
import json
import shlex
import sys
from typing import Any, Optional

import httpx
import yaml

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
               logs search PATTERN [--regex]
        Examples:
            logs
            logs --lines 50
            logs --level ERROR
            logs --logger govee.discovery
            logs search "device discovered"
            logs search "error.*timeout" --regex
        """
        if not self.client:
            print("Not connected. Use 'connect' first.")
            return

        args = shlex.split(arg)

        try:
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

    def do_clear(self, arg: str) -> None:
        """Clear the screen."""
        import os
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
