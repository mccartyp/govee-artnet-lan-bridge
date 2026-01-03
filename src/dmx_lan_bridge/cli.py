"""Command-line client for interacting with the bridge HTTP API."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import signal
import string
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Optional

import httpx
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
ENV_PREFIX = "GOVEE_ARTNET_CLI_"


class CliError(Exception):
    """Raised when the CLI encounters an expected error condition."""


@dataclass(frozen=True)
class ClientConfig:
    """Configuration for the API client."""

    server_url: str
    api_key: Optional[str]
    api_bearer_token: Optional[str]
    output: str
    timeout: float = 10.0
    page_size: Optional[int] = None  # None means no pagination


# Global state for terminal resize handling
_current_config: Optional[ClientConfig] = None
_auto_pagination: bool = False


def _handle_terminal_resize(signum: int, frame: Any) -> None:
    """
    Handle terminal window resize events (SIGWINCH).

    Updates pagination size when auto-pagination is enabled.

    Args:
        signum: Signal number
        frame: Current stack frame
    """
    global _current_config

    if _auto_pagination and _current_config is not None:
        terminal_height = shutil.get_terminal_size().lines
        new_page_size = max(10, terminal_height - 2)

        # Update config with new page size
        _current_config = ClientConfig(
            server_url=_current_config.server_url,
            api_key=_current_config.api_key,
            api_bearer_token=_current_config.api_bearer_token,
            output=_current_config.output,
            timeout=_current_config.timeout,
            page_size=new_page_size,
        )


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(f"{ENV_PREFIX}{name}", default)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Command-line client for ArtNet LAN Bridge (multi-protocol). "
            "Uses ARTNET_LAN_CLI_* env vars for defaults and prints JSON (default) or YAML. "
            "Examples: `artnet-lan-cli devices list`, "
            "`artnet-lan-cli mappings create --device-id <id> --universe 0 --start-channel 1 --template rgb`."
        )
    )
    parser.add_argument(
        "--server-url",
        default=_env("SERVER_URL", DEFAULT_SERVER_URL),
        help=(
            f"Base URL for the bridge API (env: {ENV_PREFIX}SERVER_URL). "
            f"Defaults to {DEFAULT_SERVER_URL}."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=_env("API_KEY"),
        help=(
            f"API key for authentication (env: {ENV_PREFIX}API_KEY). Sets both "
            "'X-API-Key' and 'Authorization: ApiKey <key>' headers when provided."
        ),
    )
    parser.add_argument(
        "--api-bearer-token",
        default=_env("API_BEARER_TOKEN"),
        help=(
            f"Bearer token for authentication (env: {ENV_PREFIX}API_BEARER_TOKEN). "
            "Overrides Authorization header when set."
        ),
    )
    parser.add_argument(
        "--output",
        choices=["json", "yaml", "table"],
        default=_env("OUTPUT", "json"),
        help=(
            f"Output format for responses (env: {ENV_PREFIX}OUTPUT). "
            "Defaults to 'json'; use 'yaml' for YAML output, 'table' for formatted tables."
        ),
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=_env("PAGE_SIZE"),
        help=(
            f"Number of output lines before pausing (env: {ENV_PREFIX}PAGE_SIZE). "
            "Defaults to terminal height minus 2. Set to 0 to disable pagination."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_status_commands(subparsers)
    _add_device_commands(subparsers)
    _add_mapping_commands(subparsers)

    return parser


def _add_status_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    health = subparsers.add_parser(
        "health",
        help="Check API health (GET /health returns {'status': 'ok'} when healthy)",
        description="Checks bridge liveness; prints a short JSON/YAML status payload.",
    )
    health.set_defaults(func=_cmd_health)

    status = subparsers.add_parser(
        "status",
        help="Show API status/metrics (GET /status with queues, discovery info, etc.)",
        description="Returns JSON/YAML metrics including discovery state and queue depth.",
    )
    status.set_defaults(func=_cmd_status)


def _add_device_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    devices = subparsers.add_parser(
        "devices",
        help="Device management commands (list/add/update/enable/disable/test)",
        description=(
            "Manage discovered and manual devices. Responses are device JSON objects "
            "with id, ip, model, description, enabled, and capabilities."
        ),
    )
    device_sub = devices.add_subparsers(dest="device_command", required=True)

    # Add help subcommand
    help_cmd = device_sub.add_parser(
        "help",
        help="Show help for devices commands",
        description="Display detailed help for all device management commands",
    )
    help_cmd.set_defaults(func=lambda config, client, args: devices.print_help())

    list_cmd = device_sub.add_parser(
        "list",
        help="List devices (GET /devices -> array of device objects)",
        description=(
            "Shows discovered + manual devices. Output includes capabilities such as "
            "brightness/color and current enabled state."
        ),
    )
    list_cmd.set_defaults(func=_cmd_devices_list)

    add = device_sub.add_parser(
        "add",
        help="Add a manual device (POST /devices)",
        description=(
            "Creates a manual device entry. Example payload: "
            '`{"id": "AA:BB", "ip": "192.168.1.10", "model_number": "H6160", '
            '"capabilities": {"color": true, "brightness": true}}`. Existing discovery entries are untouched.'
        ),
    )
    add.add_argument("--id", required=True, help="Device identifier (e.g., MAC address)")
    add.add_argument("--ip", required=True, help="Device IP address")
    add.add_argument(
        "--protocol",
        default="govee",
        help="Device protocol (e.g., govee, lifx). Defaults to 'govee'.",
    )
    add.add_argument(
        "--model-number",
        dest="model_number",
        help="Device model number/sku shown in responses (alias: --model)",
    )
    add.add_argument(
        "--model",
        dest="model_number",
        help="Alias for --model-number to maintain compatibility",
    )
    add.add_argument("--device-type", help="Device type/category (e.g., led_strip, light_bar)")
    add.add_argument("--description", help="Optional human-readable label")
    add.add_argument("--length-meters", type=float, help="Approximate device length in meters (catalog metadata)")
    add.add_argument("--led-count", type=int, help="Total LED count for the device")
    add.add_argument(
        "--led-density-per-meter",
        type=float,
        help="LED density per meter to align with catalog metadata",
    )
    add.add_argument(
        "--has-segments",
        dest="has_zones",
        action="store_true",
        help="Mark the device as segmented (overrides catalog default)",
    )
    add.add_argument(
        "--no-segments",
        dest="has_zones",
        action="store_false",
        help="Mark the device as non-segmented",
    )
    add.set_defaults(has_zones=None)
    add.add_argument("--segment-count", type=int, help="Number of segments when segmented control is available")
    add.add_argument(
        "--capabilities",
        help=(
            "JSON string describing capabilities (e.g., "
            '\'{"color":true,"brightness":true}\').'
        ),
    )
    add.add_argument(
        "--enabled",
        action="store_true",
        default=None,
        help="Create device as enabled (default: leave unchanged/auto-detected).",
    )
    add.add_argument(
        "--disabled",
        action="store_true",
        help="Create device as disabled (overrides --enabled).",
    )
    add.set_defaults(func=_cmd_devices_add)

    update = device_sub.add_parser(
        "update",
        help="Update a manual device (PATCH /devices/{id})",
        description=(
            "Partially updates a manual device. Only provided fields are changed; "
            "omitting all fields raises an error."
        ),
    )
    update.add_argument("device_id", help="Device identifier")
    update.add_argument("--ip", help="Device IP address")
    update.add_argument(
        "--model-number",
        dest="model_number",
        help="Device model number/sku (alias: --model)",
    )
    update.add_argument(
        "--model",
        dest="model_number",
        help="Alias for --model-number to maintain compatibility",
    )
    update.add_argument("--device-type", help="Device type/category")
    update.add_argument("--description", help="Device description")
    update.add_argument("--length-meters", type=float, help="Device length in meters")
    update.add_argument("--led-count", type=int, help="Total LED count")
    update.add_argument("--led-density-per-meter", type=float, help="LED density per meter")
    update.add_argument(
        "--has-segments",
        dest="has_zones",
        action="store_true",
        help="Mark the device as segmented",
    )
    update.add_argument(
        "--no-segments",
        dest="has_zones",
        action="store_false",
        help="Mark the device as not segmented",
    )
    update.set_defaults(has_zones=None)
    update.add_argument("--segment-count", type=int, help="Segment count when segmented control is supported")
    update.add_argument(
        "--capabilities",
        help=(
            "JSON string describing capabilities to replace current values "
            "(same shape as devices add)."
        ),
    )
    update.add_argument("--enable", action="store_true", help="Enable the device")
    update.add_argument(
        "--disable",
        action="store_true",
        help="Disable the device (overrides --enable when both set).",
    )
    update.set_defaults(func=_cmd_devices_update)

    enable = device_sub.add_parser(
        "enable",
        help="Enable a device (PATCH /devices/{id} enabled=true)",
        description="Marks the device as enabled so it receives mapping updates.",
    )
    enable.add_argument("device_id", help="Device identifier")
    enable.set_defaults(func=_cmd_devices_enable)

    disable = device_sub.add_parser(
        "disable",
        help="Disable a device (PATCH /devices/{id} enabled=false)",
        description="Disables the device; mappings remain but are inactive until re-enabled.",
    )
    disable.add_argument("device_id", help="Device identifier")
    disable.set_defaults(func=_cmd_devices_disable)

    test = device_sub.add_parser(
        "test",
        help="Send a test payload to a device (POST /devices/{id}/test)",
        description=(
            "Enqueues a device-specific JSON payload for testing. Example: "
            "`--payload '{\"cmd\":\"turn\",\"turn\":\"on\"}'`. Does not persist mappings."
        ),
    )
    test.add_argument("device_id", help="Device identifier")
    test.add_argument(
        "--payload",
        required=True,
        help="JSON payload to enqueue (stringified). Must be valid JSON.",
    )
    test.set_defaults(func=_cmd_devices_test)

    command = device_sub.add_parser(
        "command",
        help="Send on/off/brightness/color/kelvin commands to a device (POST /devices/{id}/command)",
        description=(
            "Queues a simple command for a device with basic validation. Supports "
            "on/off via the Govee LAN turn command, brightness 0-255, RGB hex color, and color "
            "temperature slider (0-255 scaled)."
        ),
    )
    command.add_argument("device_id", help="Device identifier")
    command.add_argument(
        "--on",
        action="store_true",
        help="Turn the device on (sends the LAN turn command)",
    )
    command.add_argument(
        "--off",
        action="store_true",
        help="Turn the device off (sends the LAN turn command)",
    )
    command.add_argument(
        "--brightness",
        type=int,
        help="Brightness level (0-255). Required when setting an explicit brightness.",
    )
    command.add_argument(
        "--color",
        help="RGB hex color (e.g., ff3366 or #ff3366). Expands 3-digit shorthand values.",
    )
    command.add_argument(
        "--kelvin",
        type=int,
        help="Color temperature slider (0-255) scaled to the device-supported range.",
    )
    command.set_defaults(func=_cmd_devices_command)


def _add_mapping_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    mappings = subparsers.add_parser(
        "mappings",
        help="Mapping management commands (list/get/create/update/delete/channel-map)",
        description=(
            "Configure ArtNet -> device field mappings. Mapping JSON includes id, "
            "device_id, universe, channel range, and field details."
        ),
    )
    mapping_sub = mappings.add_subparsers(dest="mapping_command", required=True)

    # Add help subcommand
    help_cmd = mapping_sub.add_parser(
        "help",
        help="Show help for mappings commands",
        description="Display detailed help for all mapping management commands",
    )
    help_cmd.set_defaults(func=lambda config, client, args: mappings.print_help())

    list_cmd = mapping_sub.add_parser(
        "list",
        help="List mappings (GET /mappings -> array of mapping objects)",
        description="Shows all mappings with device_id, universe, channel, length, type, and fields.",
    )
    list_cmd.set_defaults(func=_cmd_mappings_list)

    get = mapping_sub.add_parser(
        "get",
        help="Get a mapping by ID (GET /mappings/{id})",
        description="Fetch a single mapping JSON object by numeric ID.",
    )
    get.add_argument("mapping_id", type=int, help="Mapping identifier")
    get.set_defaults(func=_cmd_mappings_get)

    create = mapping_sub.add_parser(
        "create",
        help="Create a mapping (POST /mappings; supports templates or manual ranges)",
        description=(
            "Creates mappings for a device. Template example: "
            "`--device-id <id> --universe 0 --start-channel 1 --template rgbw` "
            "expands to consecutive color fields. Manual example: "
            "`--channel 10 --length 3 --type range` for RGB. Prevents overlap unless "
            "--allow-overlap is set."
        ),
    )
    create.add_argument("--device-id", required=True, help="Device identifier to map")
    create.add_argument("--universe", required=True, type=int, help="DMX universe number")
    create.add_argument("--channel", type=int, help="Starting DMX channel")
    create.add_argument(
        "--start-channel",
        type=int,
        help="Starting channel when using a template (falls back to --channel).",
    )
    create.add_argument(
        "--length",
        type=int,
        help="Number of channels (defaults to 1 for discrete mappings; auto-set for templates).",
    )
    create.add_argument(
        "--type",
        dest="mapping_type",
        choices=["range", "discrete"],
        default="range",
        help="Mapping type (default: range). Use discrete for single-field channels.",
    )
    create.add_argument(
        "--template",
        help=(
            "Mapping template to expand (rgb, rgbw, brightness_rgb, master_only, "
            "rgbwa, rgbaw). Requires --start-channel/--channel."
        ),
    )
    create.add_argument(
        "--field",
        help="Payload field for discrete mappings (r, g, b, w, dimmer). Required for discrete.",
    )
    create.add_argument(
        "--allow-overlap",
        action="store_true",
        help="Allow overlapping ranges (default: overlaps rejected by server).",
    )
    create.set_defaults(func=_cmd_mappings_create)

    update = mapping_sub.add_parser(
        "update",
        help="Update a mapping (PUT /mappings/{id})",
        description=(
            "Replaces provided mapping fields. Use --allow-overlap/--disallow-overlap "
            "to control overlap behavior. At least one field is required."
        ),
    )
    update.add_argument("mapping_id", type=int, help="Mapping identifier")
    update.add_argument("--device-id", help="Device identifier")
    update.add_argument("--universe", type=int, help="DMX universe")
    update.add_argument("--channel", type=int, help="Starting DMX channel")
    update.add_argument("--length", type=int, help="Number of channels")
    update.add_argument(
        "--type",
        dest="mapping_type",
        choices=["range", "discrete"],
        help="Mapping type (range/discrete)",
    )
    update.add_argument(
        "--field",
        help="Payload field for discrete mappings (r, g, b, w, dimmer)",
    )
    update.add_argument("--allow-overlap", action="store_true", help="Allow overlapping ranges")
    update.add_argument(
        "--disallow-overlap",
        action="store_true",
        help="Explicitly disallow overlapping ranges",
    )
    update.set_defaults(func=_cmd_mappings_update)

    delete = mapping_sub.add_parser(
        "delete",
        help="Delete a mapping (DELETE /mappings/{id})",
        description="Deletes a mapping by ID and returns a JSON status object.",
    )
    delete.add_argument("mapping_id", type=int, help="Mapping identifier")
    delete.set_defaults(func=_cmd_mappings_delete)

    channel_map = mapping_sub.add_parser(
        "channel-map",
        help="Show the DMX channel map (GET /channel-map -> universe keyed map)",
        description=(
            "Displays a JSON/YAML map of universes to their channel ranges and mapping "
            "assignments for quick visualization."
        ),
    )
    channel_map.set_defaults(func=_cmd_mappings_channel_map)


def _load_config(args: argparse.Namespace) -> ClientConfig:
    global _current_config, _auto_pagination

    output = args.output or "json"
    if output not in {"json", "yaml", "table"}:
        raise CliError("Output format must be 'json', 'yaml', or 'table'")

    # Determine page size
    page_size = args.page_size
    if page_size is not None and isinstance(page_size, str):
        try:
            page_size = int(page_size)
        except ValueError:
            raise CliError(f"Invalid page size: {page_size}")

    if page_size is None:
        # Default to terminal height minus 2 (for prompt and spacing)
        terminal_height = shutil.get_terminal_size().lines
        page_size = max(10, terminal_height - 2)  # Minimum 10 lines
        _auto_pagination = True  # Enable auto-resize for auto-detected page size
    elif page_size == 0:
        page_size = None  # Disable pagination
        _auto_pagination = False
    else:
        _auto_pagination = False  # User specified explicit page size

    config = ClientConfig(
        server_url=args.server_url,
        api_key=args.api_key,
        api_bearer_token=args.api_bearer_token,
        output=output,
        page_size=page_size,
    )

    # Store global config for resize handler
    _current_config = config

    # Set up terminal resize handler (Unix-like systems only)
    if hasattr(signal, 'SIGWINCH'):
        signal.signal(signal.SIGWINCH, _handle_terminal_resize)

    return config


def _build_client(config: ClientConfig) -> httpx.Client:
    """
    Build HTTP client with connection pooling and retry-friendly configuration.

    Args:
        config: Client configuration

    Returns:
        Configured httpx.Client with connection pooling
    """
    headers: MutableMapping[str, str] = {}
    if config.api_key:
        headers["X-API-Key"] = config.api_key
        headers.setdefault("Authorization", f"ApiKey {config.api_key}")
    if config.api_bearer_token:
        headers["Authorization"] = f"Bearer {config.api_bearer_token}"

    # Connection pooling limits for better resource management
    limits = httpx.Limits(
        max_connections=10,  # Maximum total connections
        max_keepalive_connections=5,  # Maximum idle connections to keep alive
        keepalive_expiry=30.0,  # Keepalive connections expire after 30s
    )

    # Retry-friendly transport (httpx doesn't have built-in retries, but we can configure transport)
    transport = httpx.HTTPTransport(
        retries=3,  # Retry failed connections up to 3 times
        limits=limits,
    )

    return httpx.Client(
        base_url=config.server_url,
        headers=headers,
        timeout=config.timeout,
        transport=transport,
        follow_redirects=True,  # Follow redirects automatically
    )


def _paginate_output(text: str, config: Optional[ClientConfig]) -> None:
    """
    Print text with optional pagination.

    Args:
        text: Text to print
        config: Client configuration with page_size
    """
    # Use global config if available (to pick up resize updates), otherwise use passed config
    active_config = _current_config if _current_config is not None else config

    # Strip trailing newlines to prevent excessive blank space at bottom
    text = text.rstrip('\n')

    if not active_config or not active_config.page_size:
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

        if line_count >= active_config.page_size and i < len(lines) - 1:
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


def _is_device_list(data: Any) -> bool:
    """
    Check if data appears to be a device list.

    Args:
        data: Data to check

    Returns:
        True if data looks like a device list
    """
    if not isinstance(data, list) or len(data) == 0:
        return False
    if not isinstance(data[0], dict):
        return False
    # Check for device-specific keys
    first_item = data[0]
    device_keys = {"id", "ip", "enabled", "capabilities"}
    return len(device_keys & set(first_item.keys())) >= 3


def _print_device_cards(devices: list[dict[str, Any]], console: Console, config: Optional[ClientConfig]) -> None:
    """
    Print devices in a card-style format with multiple lines per device.

    Args:
        devices: List of device dictionaries
        console: Rich console instance
        config: Client configuration (for pagination)
    """
    line_count = 0

    for idx, device in enumerate(devices):
        # Create a table for this device with 2 columns
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Field", style="cyan", width=20)
        table.add_column("Value", style="yellow")

        # Key fields to display in order
        key_fields = [
            ("ID", "id"),
            ("IP", "ip"),
            ("Protocol", "protocol"),
            ("Model", "model_number"),
            ("Type", "device_type"),
            ("Name", "name"),
            ("Enabled", "enabled"),
            ("Manual", "manual"),
            ("Discovered", "discovered"),
            ("Configured", "configured"),
            ("Offline", "offline"),
            ("Stale", "stale"),
        ]

        # Add key fields
        for label, key in key_fields:
            if key in device and device[key] is not None:
                value = device[key]
                if isinstance(value, bool):
                    value_str = "✓" if value else "✗"
                    style = "green" if value else "red"
                    table.add_row(label, f"[{style}]{value_str}[/{style}]")
                else:
                    table.add_row(label, str(value))

        # Add capabilities as JSON if present
        if "capabilities" in device and device["capabilities"]:
            caps_str = json.dumps(device["capabilities"], indent=2) if isinstance(device["capabilities"], dict) else str(device["capabilities"])
            table.add_row("Capabilities", caps_str)

        # Add metadata fields if present
        metadata_fields = [
            ("LED Count", "led_count"),
            ("Length (m)", "length_meters"),
            ("Segments", "zone_count"),
            ("Last Seen", "last_seen"),
            ("First Seen", "first_seen"),
        ]

        for label, key in metadata_fields:
            if key in device and device[key] is not None:
                table.add_row(label, str(device[key]))

        # Print device header and table
        header_text = f"[bold magenta]Device {idx + 1} of {len(devices)}[/bold magenta]"
        console.print(header_text)
        console.print(table)

        # Add separator between devices
        if idx < len(devices) - 1:
            console.print("─" * 80)

        # Count lines for pagination (rough estimate: 2 lines per field + header + separator)
        estimated_lines = len([r for r in table.rows]) + 3
        line_count += estimated_lines

        # Check if we need to pause
        if config and config.page_size and line_count >= config.page_size and idx < len(devices) - 1:
            try:
                response = input("\n[Press Enter to continue, 'q' to quit] ")
                if response.lower().startswith('q'):
                    console.print("\n[dim][Output truncated][/dim]")
                    return
                line_count = 0
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim][Output interrupted][/dim]")
                return


def _print_output(data: Any, output: str, config: Optional[ClientConfig] = None) -> None:
    """
    Print output in specified format.

    Args:
        data: Data to print
        output: Output format (json, yaml, or table)
        config: Client configuration (for pagination)
    """
    if output == "yaml":
        stream = io.StringIO()
        yaml.safe_dump(data, stream, sort_keys=False)
        output_text = stream.getvalue()
        _paginate_output(output_text, config)
    elif output == "table":
        console = Console()
        _print_table(data, console, config)
    else:
        output_text = json.dumps(data, indent=2) + "\n"
        _paginate_output(output_text, config)


def _print_table(data: Any, console: Console, config: Optional[ClientConfig] = None) -> None:
    """
    Print data as a rich table.

    Args:
        data: Data to print (dict or list of dicts)
        console: Rich console instance
        config: Client configuration (for pagination and device detection)
    """
    if data is None:
        console.print("[dim]No data[/]")
        return

    # Handle device lists with special card format
    if _is_device_list(data):
        _print_device_cards(data, console, config)
        return

    # Handle list of items (most common case)
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        table = Table(show_header=True, header_style="bold magenta")

        # Add columns from first item
        for key in data[0].keys():
            table.add_column(str(key), style="cyan")

        # Add rows
        for item in data:
            table.add_row(*[str(v) for v in item.values()])

        console.print(table)

    # Handle single dict
    elif isinstance(data, dict):
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="yellow")

        for key, value in data.items():
            # Format nested structures as JSON
            if isinstance(value, (dict, list)):
                value_str = json.dumps(value, indent=2)
            else:
                value_str = str(value)
            table.add_row(str(key), value_str)

        console.print(table)

    # Fallback to JSON for other types
    else:
        console.print_json(data=data)


def _handle_response(response: httpx.Response) -> Any:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:  # pragma: no cover - CLI feedback path
        detail = None
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = response.text
        raise CliError(f"Request failed ({response.status_code}): {detail}") from exc
    if response.content:
        return response.json()
    return None


# Shared API helper functions to reduce duplication between CLI and shell


def _api_get(client: httpx.Client, endpoint: str, config: ClientConfig) -> Any:
    """
    Generic handler for GET requests with response handling and output.

    Args:
        client: HTTP client instance
        endpoint: API endpoint path (e.g., "/devices", "/health")
        config: Client configuration with output format

    Returns:
        Response data (parsed JSON)
    """
    data = _handle_response(client.get(endpoint))
    _print_output(data, config.output, config)
    return data


def _api_get_by_id(
    client: httpx.Client, endpoint: str, resource_id: str, config: ClientConfig
) -> Any:
    """
    Generic handler for GET requests with a resource ID.

    Args:
        client: HTTP client instance
        endpoint: Base endpoint (e.g., "/mappings")
        resource_id: Resource identifier
        config: Client configuration with output format

    Returns:
        Response data (parsed JSON)
    """
    data = _handle_response(client.get(f"{endpoint}/{resource_id}"))
    _print_output(data, config.output, config)
    return data


def _device_set_enabled(
    client: httpx.Client, device_id: str, enabled: bool, config: ClientConfig
) -> Any:
    """
    Set device enabled/disabled state.

    Args:
        client: HTTP client instance
        device_id: Device identifier
        enabled: True to enable, False to disable
        config: Client configuration with output format

    Returns:
        Updated device data
    """
    data = _handle_response(client.patch(f"/devices/{device_id}", json={"enabled": enabled}))
    _print_output(data, config.output, config)
    return data


def _api_delete(
    client: httpx.Client,
    endpoint: str,
    resource_id: str,
    config: ClientConfig,
    custom_output: Optional[Any] = None,
) -> None:
    """
    Generic DELETE handler with optional custom output.

    Args:
        client: HTTP client instance
        endpoint: Base endpoint (e.g., "/mappings")
        resource_id: Resource identifier
        config: Client configuration with output format
        custom_output: Optional custom data to output instead of default
    """
    _handle_response(client.delete(f"{endpoint}/{resource_id}"))
    if custom_output is not None:
        _print_output(custom_output, config.output, config)


def _cmd_health(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    _api_get(client, "/health", config)


def _cmd_status(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    _api_get(client, "/status", config)


def _cmd_devices_list(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    _api_get(client, "/devices", config)


def _cmd_devices_add(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    capabilities = _parse_json_arg(args.capabilities) if args.capabilities else None
    if capabilities is not None:
        capabilities = _validate_capabilities(capabilities)

    enabled: Optional[bool]
    if args.disabled:
        enabled = False
    elif args.enabled:
        enabled = True
    else:
        enabled = None
    payload: MutableMapping[str, Any] = {
        "id": args.id,
        "ip": args.ip,
        "description": args.description,
        "capabilities": capabilities,
    }
    if args.model_number:
        payload["model_number"] = args.model_number
    if args.device_type:
        payload["device_type"] = args.device_type
    if args.length_meters is not None:
        payload["length_meters"] = args.length_meters
    if args.led_count is not None:
        payload["led_count"] = args.led_count
    if args.led_density_per_meter is not None:
        payload["led_density_per_meter"] = args.led_density_per_meter
    if args.has_zones is not None:
        payload["has_zones"] = args.has_zones
    if args.zone_count is not None:
        payload["zone_count"] = args.zone_count
    if enabled is not None:
        payload["enabled"] = enabled

    _validate_device_payload(payload, "create")
    data = _handle_response(client.post("/devices", json=payload))
    _print_output(data, config.output, config)


def _cmd_devices_update(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    payload: MutableMapping[str, Any] = {}
    if args.ip:
        payload["ip"] = args.ip
    if args.model_number:
        payload["model_number"] = args.model_number
    if args.device_type:
        payload["device_type"] = args.device_type
    if args.description:
        payload["description"] = args.description
    if args.length_meters is not None:
        payload["length_meters"] = args.length_meters
    if args.led_count is not None:
        payload["led_count"] = args.led_count
    if args.led_density_per_meter is not None:
        payload["led_density_per_meter"] = args.led_density_per_meter
    if args.has_zones is not None:
        payload["has_zones"] = args.has_zones
    if args.zone_count is not None:
        payload["zone_count"] = args.zone_count
    if args.capabilities is not None:
        capabilities = _parse_json_arg(args.capabilities)
        payload["capabilities"] = _validate_capabilities(capabilities)
    if args.enable:
        payload["enabled"] = True
    if args.disable:
        payload["enabled"] = False
    if not payload:
        raise CliError("No updates provided")

    _validate_device_payload(payload, "update")
    data = _handle_response(client.patch(f"/devices/{args.device_id}", json=payload))
    _print_output(data, config.output, config)


def _cmd_devices_enable(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    _device_set_enabled(client, args.device_id, True, config)


def _cmd_devices_disable(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    _device_set_enabled(client, args.device_id, False, config)


def _cmd_devices_test(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    payload = _parse_json_arg(args.payload)
    data = _handle_response(
        client.post(f"/devices/{args.device_id}/test", json={"payload": payload})
    )
    _print_output(data, config.output, config)


def _cmd_devices_command(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    if args.on and args.off:
        raise CliError("Choose either --on or --off, not both.")
    if not any(
        [
            args.on,
            args.off,
            args.brightness is not None,
            args.color,
            args.kelvin is not None,
        ]
    ):
        raise CliError("At least one action is required (on, off, brightness, color, kelvin).")
    if args.brightness is not None:
        _validate_byte_range("brightness", args.brightness)
    if args.kelvin is not None:
        _validate_byte_range("kelvin", args.kelvin)
    color = _normalize_color_hex(args.color) if args.color else None
    payload: MutableMapping[str, Any] = {}
    if args.on:
        payload["on"] = True
    if args.off:
        payload["off"] = True
    if args.brightness is not None:
        payload["brightness"] = args.brightness
    if color:
        payload["color"] = color
    if args.kelvin is not None:
        payload["kelvin"] = args.kelvin
    data = _handle_response(
        client.post(f"/devices/{args.device_id}/command", json=payload)
    )
    _print_output(data, config.output, config)


def _cmd_mappings_list(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    _api_get(client, "/mappings", config)


def _cmd_mappings_get(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    _api_get_by_id(client, "/mappings", args.mapping_id, config)


def _cmd_mappings_create(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    payload: MutableMapping[str, Any] = {
        "device_id": args.device_id,
        "universe": args.universe,
        "allow_overlap": args.allow_overlap,
    }
    if args.template:
        start_channel = args.start_channel or args.channel
        if start_channel is None:
            raise CliError("Start channel is required when using a template")
        payload["template"] = args.template
        payload["start_channel"] = start_channel
        if args.channel is not None:
            payload["channel"] = args.channel
    else:
        channel = args.channel if args.channel is not None else args.start_channel
        if channel is None:
            raise CliError("Channel is required when not using a template")
        payload.update(
            {
                "channel": channel,
                "length": args.length if args.length is not None else 1,
                "mapping_type": args.mapping_type,
                "field": args.field,
            }
        )

    _validate_mapping_payload(payload, "create")
    data = _handle_response(client.post("/mappings", json=payload))
    _print_output(data, config.output, config)


def _cmd_mappings_update(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    payload: MutableMapping[str, Any] = {}
    if args.device_id:
        payload["device_id"] = args.device_id
    if args.universe is not None:
        payload["universe"] = args.universe
    if args.channel is not None:
        payload["channel"] = args.channel
    if args.length is not None:
        payload["length"] = args.length
    if args.mapping_type:
        payload["mapping_type"] = args.mapping_type
    if args.field is not None:
        payload["field"] = args.field
    if args.allow_overlap:
        payload["allow_overlap"] = True
    if args.disallow_overlap:
        payload["allow_overlap"] = False
    if not payload:
        raise CliError("No updates provided")

    _validate_mapping_payload(payload, "update")
    data = _handle_response(
        client.put(f"/mappings/{args.mapping_id}", json=payload)
    )
    _print_output(data, config.output, config)


def _cmd_mappings_delete(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    _api_delete(client, "/mappings", args.mapping_id, config, {"status": "deleted", "id": args.mapping_id})


def _cmd_mappings_channel_map(
    config: ClientConfig, client: httpx.Client, args: argparse.Namespace
) -> None:
    _api_get(client, "/channel-map", config)


def _parse_json_arg(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise CliError(f"Failed to parse JSON argument: {exc.msg} at position {exc.pos}") from exc


def _validate_capabilities(capabilities: Any) -> dict[str, Any]:
    """
    Validate capabilities JSON structure.

    Args:
        capabilities: Capabilities data to validate

    Returns:
        Validated capabilities dictionary

    Raises:
        CliError: If capabilities structure is invalid
    """
    if not isinstance(capabilities, dict):
        raise CliError("Capabilities must be a JSON object (dictionary)")

    valid_keys = {"color", "brightness", "temperature"}
    for key in capabilities.keys():
        if key not in valid_keys:
            raise CliError(
                f"Invalid capability key '{key}'. Valid keys: {', '.join(sorted(valid_keys))}"
            )

    for key, value in capabilities.items():
        if not isinstance(value, bool):
            raise CliError(f"Capability '{key}' must be a boolean value (true/false)")

    return capabilities


def _validate_device_payload(payload: dict[str, Any], operation: str = "create") -> None:
    """
    Validate device payload structure.

    Args:
        payload: Device payload to validate
        operation: Operation type ("create" or "update")

    Raises:
        CliError: If payload is invalid
    """
    # Required fields for create operation
    if operation == "create":
        if "id" not in payload or not payload["id"]:
            raise CliError("Device ID is required")
        if "ip" not in payload or not payload["ip"]:
            raise CliError("Device IP address is required")

    # Validate IP address format if present
    if "ip" in payload:
        ip = payload["ip"]
        parts = ip.split(".")
        if len(parts) != 4:
            raise CliError(f"Invalid IP address format: {ip}")
        try:
            for part in parts:
                num = int(part)
                if num < 0 or num > 255:
                    raise CliError(f"Invalid IP address: {ip} (octet out of range)")
        except ValueError:
            raise CliError(f"Invalid IP address: {ip}")

    # Validate numeric fields
    if "length_meters" in payload and payload["length_meters"] is not None:
        if payload["length_meters"] <= 0:
            raise CliError("Device length must be greater than 0")

    if "led_count" in payload and payload["led_count"] is not None:
        if payload["led_count"] <= 0:
            raise CliError("LED count must be greater than 0")

    if "led_density_per_meter" in payload and payload["led_density_per_meter"] is not None:
        if payload["led_density_per_meter"] <= 0:
            raise CliError("LED density must be greater than 0")

    if "zone_count" in payload and payload["zone_count"] is not None:
        if payload["zone_count"] <= 0:
            raise CliError("Segment count must be greater than 0")


def _validate_mapping_payload(payload: dict[str, Any], operation: str = "create") -> None:
    """
    Validate mapping payload structure.

    Args:
        payload: Mapping payload to validate
        operation: Operation type ("create" or "update")

    Raises:
        CliError: If payload is invalid
    """
    # Required fields for create operation
    if operation == "create":
        if "device_id" not in payload or not payload["device_id"]:
            raise CliError("Device ID is required for mapping")
        if "universe" not in payload or payload["universe"] is None:
            raise CliError("Universe is required for mapping")

    # Validate universe
    if "universe" in payload and payload["universe"] is not None:
        universe = payload["universe"]
        if not isinstance(universe, int) or universe < 0 or universe > 32767:
            raise CliError(f"Universe must be between 0 and 32767, got: {universe}")

    # Validate channel
    if "channel" in payload and payload["channel"] is not None:
        channel = payload["channel"]
        if not isinstance(channel, int) or channel < 1 or channel > 512:
            raise CliError(f"Channel must be between 1 and 512, got: {channel}")

    # Validate start_channel
    if "start_channel" in payload and payload["start_channel"] is not None:
        start_channel = payload["start_channel"]
        if not isinstance(start_channel, int) or start_channel < 1 or start_channel > 512:
            raise CliError(f"Start channel must be between 1 and 512, got: {start_channel}")

    # Validate length
    if "length" in payload and payload["length"] is not None:
        length = payload["length"]
        if not isinstance(length, int) or length < 1:
            raise CliError(f"Length must be at least 1, got: {length}")

    # Validate template if present
    if "template" in payload:
        valid_templates = {"rgb", "rgbw", "brightness", "temperature"}
        template = payload["template"]
        if template not in valid_templates:
            raise CliError(
                f"Invalid template '{template}'. Valid templates: {', '.join(sorted(valid_templates))}"
            )


def _validate_byte_range(name: str, value: int) -> None:
    if value < 0 or value > 255:
        raise CliError(f"{name.capitalize()} must be between 0 and 255.")


def _normalize_color_hex(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith("#"):
        normalized = normalized[1:]
    if len(normalized) == 3:
        normalized = "".join(ch * 2 for ch in normalized)
    if len(normalized) != 6 or any(ch not in string.hexdigits for ch in normalized):
        raise CliError("Color must be a hex value like ff3366 or #ff3366.")
    return normalized.lower()


def _check_api_available(client: httpx.Client) -> bool:
    """
    Check if the API is available and responding.

    Args:
        client: HTTP client

    Returns:
        True if API is available, False otherwise
    """
    try:
        response = client.get("/health", timeout=5.0)
        return response.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
        return False


def _ensure_api_available(client: httpx.Client, config: ClientConfig) -> None:
    """
    Ensure API is available, raise friendly error if not.

    Args:
        client: HTTP client
        config: Client configuration

    Raises:
        CliError: If API is not available
    """
    if not _check_api_available(client):
        raise CliError(
            f"Unable to connect to the bridge API at {config.server_url}. "
            "Please check that the bridge is running and the URL is correct. "
            "You can verify with: curl {config.server_url}/health"
        )


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(args=argv)

    try:
        config = _load_config(args)
        client = _build_client(config)
        with client:
            # Check API availability before executing commands (except health check itself)
            if args.command != "health":
                _ensure_api_available(client, config)

            func: Callable[[ClientConfig, httpx.Client, argparse.Namespace], None] = args.func
            func(config, client, args)
    except CliError as exc:  # pragma: no cover - CLI feedback path
        sys.stderr.write(f"Error: {exc}\n")
        sys.exit(1)
    except httpx.RequestError as exc:  # pragma: no cover - CLI feedback path
        sys.stderr.write(f"Connection error: {exc}\n")
        sys.stderr.write(f"Make sure the bridge is running at {config.server_url}\n")
        sys.exit(1)
    except httpx.TimeoutException as exc:  # pragma: no cover - CLI feedback path
        sys.stderr.write(f"Request timeout: {exc}\n")
        sys.stderr.write(f"The bridge at {config.server_url} is not responding\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
