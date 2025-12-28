"""Configuration and session management command handlers."""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

from rich import box
from rich.table import Table
from rich.text import Text

from ...cli import ClientConfig
from . import CommandHandler


class ConfigCommandHandler(CommandHandler):
    """Handler for configuration and session management commands."""

    def do_output(self, arg: str) -> None:
        """
        Set output format: json, yaml, or table.
        Usage: output json|yaml|table
        """
        args = shlex.split(arg)
        if not args or args[0] not in ("json", "yaml", "table"):
            self.shell._append_output("[yellow]Usage: output json|yaml|table[/]" + "\n")
            self.shell._append_output(f"[dim]Current format: {self.config.output}[/]" + "\n")
            return

        new_format = args[0]
        self.shell.config = ClientConfig(
            server_url=self.config.server_url,
            api_key=self.config.api_key,
            api_bearer_token=self.config.api_bearer_token,
            output=new_format,
            timeout=self.config.timeout,
            page_size=self.config.page_size,
        )
        self.shell._append_output(f"[green]Output format set to: {new_format}[/]" + "\n")

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
            self.shell._append_output("Usage: bookmark add|list|delete|use <name> [value]" + "\n")
            return

        command = args[0]

        if command == "add" and len(args) >= 3:
            name = args[1]
            value = args[2]
            self.shell.bookmarks[name] = value
            self.shell._save_json(self.shell.bookmarks_file, self.shell.bookmarks)
            self.shell._append_output(f"[green]Bookmark '{name}' added: {value}[/]" + "\n")

        elif command == "list":
            if not self.shell.bookmarks:
                self.shell._append_output("[dim]No bookmarks saved[/]" + "\n")
                return

            table = Table(title=Text("Bookmarks", justify="center"), show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("Name", style="cyan")
            table.add_column("Value", style="yellow")

            for name, value in self.shell.bookmarks.items():
                table.add_row(name, value)

            self.shell._append_output(table + "\n")

        elif command == "delete" and len(args) >= 2:
            name = args[1]
            if name in self.shell.bookmarks:
                del self.shell.bookmarks[name]
                self.shell._save_json(self.shell.bookmarks_file, self.shell.bookmarks)
                self.shell._append_output(f"[green]Bookmark '{name}' deleted[/]" + "\n")
            else:
                self.shell._append_output(f"[red]Bookmark '{name}' not found[/]" + "\n")

        elif command == "use" and len(args) >= 2:
            name = args[1]
            if name in self.shell.bookmarks:
                value = self.shell.bookmarks[name]
                # Detect if it's a URL or device ID
                if value.startswith("http://") or value.startswith("https://"):
                    self.shell.do_connect(value)
                else:
                    self.shell._append_output(f"[cyan]Bookmark value: {value}[/]" + "\n")
                    self.shell._append_output("[dim]Use this value in your commands[/]" + "\n")
            else:
                self.shell._append_output(f"[red]Bookmark '{name}' not found[/]" + "\n")

        else:
            self.shell._append_output("Usage: bookmark add|list|delete|use <name> [value]" + "\n")

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
            self.shell._append_output("Usage: alias add|list|delete <name> [command]" + "\n")
            return

        command = args[0]

        if command == "add" and len(args) >= 3:
            name = args[1]
            value = " ".join(args[2:])
            self.shell.aliases[name] = value
            self.shell._save_json(self.shell.aliases_file, self.shell.aliases)
            self.shell._append_output(f"[green]Alias '{name}' -> '{value}' added[/]" + "\n")

        elif command == "list":
            if not self.shell.aliases:
                self.shell._append_output("[dim]No aliases defined[/]" + "\n")
                return

            table = Table(title=Text("Aliases", justify="center"), show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("Alias", style="cyan")
            table.add_column("Command", style="yellow")

            for name, value in self.shell.aliases.items():
                table.add_row(name, value)

            self.shell._append_output(table + "\n")

        elif command == "delete" and len(args) >= 2:
            name = args[1]
            if name in self.shell.aliases:
                del self.shell.aliases[name]
                self.shell._save_json(self.shell.aliases_file, self.shell.aliases)
                self.shell._append_output(f"[green]Alias '{name}' deleted[/]" + "\n")
            else:
                self.shell._append_output(f"[red]Alias '{name}' not found[/]" + "\n")

        else:
            self.shell._append_output("Usage: alias add|list|delete <name> [command]" + "\n")

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
            self.shell._append_output("Usage: cache stats|clear" + "\n")
            return

        command = args[0]

        if command == "stats":
            stats = self.cache.get_stats()
            total_requests = stats["hits"] + stats["misses"]
            hit_rate = (stats["hits"] / total_requests * 100) if total_requests > 0 else 0

            table = Table(title=Text("Cache Statistics", justify="center"), show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="yellow")

            table.add_row("Hits", str(stats["hits"]))
            table.add_row("Misses", str(stats["misses"]))
            table.add_row("Hit Rate", f"{hit_rate:.1f}%")
            table.add_row("Cache Size", str(stats["size"]))
            table.add_row("TTL (seconds)", str(self.cache.default_ttl))

            self.shell._append_output(table + "\n")

        elif command == "clear":
            self.cache.clear()
            self.shell._append_output("[green]Cache cleared successfully[/]" + "\n")

        else:
            self.shell._append_output("Usage: cache stats|clear" + "\n")

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
        if not self.client:
            self.shell._append_output("[red]Not connected. Use 'connect' first.[/]" + "\n")
            return

        args = shlex.split(arg)
        if not args:
            self.shell._append_output("[yellow]Usage: watch <target> [--interval SECONDS][/]" + "\n")
            self.shell._append_output("[yellow]Valid targets: devices, mappings, logs, dashboard[/]\n")
            return

        # Parse arguments
        command = args[0]
        interval = 5.0

        i = 1
        while i < len(args):
            if args[i] == "--interval":
                if i + 1 < len(args):
                    try:
                        interval = float(args[i + 1])
                        if interval < 0.5:
                            self.shell._append_output("[yellow]Minimum interval is 0.5 seconds[/]\n")
                            interval = 0.5
                        i += 1  # Skip next arg
                    except ValueError:
                        self.shell._append_output(f"[red]Invalid interval value: {args[i + 1]}[/]\n")
                        return
                else:
                    self.shell._append_output("[red]--interval requires a value[/]\n")
                    return
            i += 1

        # Validate target
        valid_targets = ["devices", "mappings", "logs", "dashboard"]
        if command not in valid_targets:
            self.shell._append_output(f"[red]Unknown watch target: {command}[/]\n")
            self.shell._append_output(f"[yellow]Valid targets: {', '.join(valid_targets)}[/]\n")
            return

        try:
            # Always enter continuous watch mode with overlay window
            asyncio.create_task(self.shell._enter_watch_mode(target=command, interval=interval))

        except Exception as exc:
            self.shell._handle_error(exc, f"watch {command}")

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
            self.shell._append_output("Usage: batch <filename>" + "\n")
            return

        filename = args[0]
        file_path = Path(filename)

        if not file_path.exists():
            self.shell._append_output(f"[red]File not found: {filename}[/]" + "\n")
            return

        try:
            with open(file_path, "r") as f:
                lines = f.readlines()

            self.shell._append_output(f"[cyan]Executing {len(lines)} commands from {filename}[/]" + "\n")
            self.shell._append_output("\n")

            for i, line in enumerate(lines, 1):
                line = line.strip()

                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue

                self.shell._append_output(f"[dim]({i}) {line}[/]" + "\n")
                self.shell.onecmd(line)
                self.shell._append_output("\n")

            self.shell._append_output("[green]Batch execution complete[/]" + "\n")

        except Exception as exc:
            self.shell._append_output(f"[red]Error executing batch: {exc}[/]" + "\n")

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
            self.shell._append_output("Usage: session save|load|list|delete <name>" + "\n")
            return

        command = args[0]
        sessions_file = self.shell.data_dir / "sessions.json"
        sessions = self.shell._load_json(sessions_file, {})

        if command == "save" and len(args) >= 2:
            name = args[1]
            session_data = {
                "server_url": self.config.server_url,
                "output": self.config.output,
                "page_size": self.config.page_size,
            }
            sessions[name] = session_data
            self.shell._save_json(sessions_file, sessions)
            self.shell._append_output(f"[green]Session '{name}' saved[/]" + "\n")

        elif command == "load" and len(args) >= 2:
            name = args[1]
            if name in sessions:
                session_data = sessions[name]
                self.shell.config = ClientConfig(
                    server_url=session_data["server_url"],
                    api_key=self.config.api_key,
                    api_bearer_token=self.config.api_bearer_token,
                    output=session_data["output"],
                    timeout=self.config.timeout,
                    page_size=session_data.get("page_size", self.config.page_size),
                )
                self.shell._connect()
                self.shell._append_output(f"[green]Session '{name}' loaded[/]" + "\n")
                self.shell._append_output(f"  Server: {self.config.server_url}" + "\n")
                self.shell._append_output(f"  Output: {self.config.output}" + "\n")
                if self.config.page_size:
                    self.shell._append_output(f"  Pagination: {self.config.page_size} lines" + "\n")
            else:
                self.shell._append_output(f"[red]Session '{name}' not found[/]" + "\n")

        elif command == "list":
            if not sessions:
                self.shell._append_output("[dim]No sessions saved[/]" + "\n")
                return

            table = Table(title=Text("Sessions", justify="center"), show_header=True, header_style="bold magenta", box=box.ROUNDED)
            table.add_column("Name", style="cyan")
            table.add_column("Server URL", style="yellow")
            table.add_column("Output Format", style="green")

            for name, data in sessions.items():
                table.add_row(name, data["server_url"], data["output"])

            self.shell._append_output(table + "\n")

        elif command == "delete" and len(args) >= 2:
            name = args[1]
            if name in sessions:
                del sessions[name]
                self.shell._save_json(sessions_file, sessions)
                self.shell._append_output(f"[green]Session '{name}' deleted[/]" + "\n")
            else:
                self.shell._append_output(f"[red]Session '{name}' not found[/]" + "\n")

        else:
            self.shell._append_output("Usage: session save|load|list|delete <name>" + "\n")
