"""Command-line client for interacting with the bridge HTTP API."""

from __future__ import annotations

import argparse
import json
import os
import string
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Optional

import httpx
import yaml


DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
ENV_PREFIX = "GOVEE_ARTNET_"


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


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(f"{ENV_PREFIX}{name}", default)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "CLI for the Govee Artnet LAN bridge API. Uses GOVEE_ARTNET_* env vars "
            "for defaults and prints JSON (default) or YAML. Examples: "
            "`govee-artnet devices list`, `govee-artnet mappings create "
            "--device-id <id> --universe 0 --start-channel 1 --template rgb`."
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
        "--shell",
        "-i",
        action="store_true",
        help="Start interactive shell mode",
    )

    subparsers = parser.add_subparsers(dest="command", required=False)

    # Add shell command
    shell_parser = subparsers.add_parser(
        "shell",
        help="Start interactive shell mode",
        description="Launch an interactive shell for managing the bridge",
    )
    shell_parser.set_defaults(func=lambda config, client, args: None)  # Handled specially

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
        dest="has_segments",
        action="store_true",
        help="Mark the device as segmented (overrides catalog default)",
    )
    add.add_argument(
        "--no-segments",
        dest="has_segments",
        action="store_false",
        help="Mark the device as non-segmented",
    )
    add.set_defaults(has_segments=None)
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
        dest="has_segments",
        action="store_true",
        help="Mark the device as segmented",
    )
    update.add_argument(
        "--no-segments",
        dest="has_segments",
        action="store_false",
        help="Mark the device as not segmented",
    )
    update.set_defaults(has_segments=None)
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
        help="Payload field for discrete mappings (r, g, b, w, brightness). Required for discrete.",
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
        help="Payload field for discrete mappings (r, g, b, w, brightness)",
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
    output = args.output or "json"
    if output not in {"json", "yaml"}:
        raise CliError("Output format must be 'json' or 'yaml'")

    return ClientConfig(
        server_url=args.server_url,
        api_key=args.api_key,
        api_bearer_token=args.api_bearer_token,
        output=output,
    )


def _build_client(config: ClientConfig) -> httpx.Client:
    headers: MutableMapping[str, str] = {}
    if config.api_key:
        headers["X-API-Key"] = config.api_key
        headers.setdefault("Authorization", f"ApiKey {config.api_key}")
    if config.api_bearer_token:
        headers["Authorization"] = f"Bearer {config.api_bearer_token}"

    return httpx.Client(base_url=config.server_url, headers=headers, timeout=config.timeout)


def _print_output(data: Any, output: str) -> None:
    if output == "yaml":
        yaml.safe_dump(data, sys.stdout, sort_keys=False)
    else:
        json.dump(data, sys.stdout, indent=2)
        sys.stdout.write("\n")


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


def _cmd_health(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    data = _handle_response(client.get("/health"))
    _print_output(data, config.output)


def _cmd_status(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    data = _handle_response(client.get("/status"))
    _print_output(data, config.output)


def _cmd_devices_list(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    data = _handle_response(client.get("/devices"))
    _print_output(data, config.output)


def _cmd_devices_add(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    capabilities = _parse_json_arg(args.capabilities) if args.capabilities else None
    enabled: Optional[bool]
    if args.disabled:
        enabled = False
    elif args.enabled:
        enabled = True
    else:
        enabled = None
    payload: Mapping[str, Any] = {
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
    if args.has_segments is not None:
        payload["has_segments"] = args.has_segments
    if args.segment_count is not None:
        payload["segment_count"] = args.segment_count
    if enabled is not None:
        payload["enabled"] = enabled
    data = _handle_response(client.post("/devices", json=payload))
    _print_output(data, config.output)


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
    if args.has_segments is not None:
        payload["has_segments"] = args.has_segments
    if args.segment_count is not None:
        payload["segment_count"] = args.segment_count
    if args.capabilities is not None:
        payload["capabilities"] = _parse_json_arg(args.capabilities)
    if args.enable:
        payload["enabled"] = True
    if args.disable:
        payload["enabled"] = False
    if not payload:
        raise CliError("No updates provided")
    data = _handle_response(client.patch(f"/devices/{args.device_id}", json=payload))
    _print_output(data, config.output)


def _cmd_devices_enable(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    data = _handle_response(
        client.patch(f"/devices/{args.device_id}", json={"enabled": True})
    )
    _print_output(data, config.output)


def _cmd_devices_disable(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    data = _handle_response(
        client.patch(f"/devices/{args.device_id}", json={"enabled": False})
    )
    _print_output(data, config.output)


def _cmd_devices_test(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    payload = _parse_json_arg(args.payload)
    data = _handle_response(
        client.post(f"/devices/{args.device_id}/test", json={"payload": payload})
    )
    _print_output(data, config.output)


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
    _print_output(data, config.output)


def _cmd_mappings_list(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    data = _handle_response(client.get("/mappings"))
    _print_output(data, config.output)


def _cmd_mappings_get(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    data = _handle_response(client.get(f"/mappings/{args.mapping_id}"))
    _print_output(data, config.output)


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
    data = _handle_response(client.post("/mappings", json=payload))
    _print_output(data, config.output)


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
    data = _handle_response(
        client.put(f"/mappings/{args.mapping_id}", json=payload)
    )
    _print_output(data, config.output)


def _cmd_mappings_delete(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    _handle_response(client.delete(f"/mappings/{args.mapping_id}"))
    _print_output({"status": "deleted", "id": args.mapping_id}, config.output)


def _cmd_mappings_channel_map(
    config: ClientConfig, client: httpx.Client, args: argparse.Namespace
) -> None:
    data = _handle_response(client.get("/channel-map"))
    _print_output(data, config.output)


def _parse_json_arg(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise CliError("Failed to parse JSON argument") from exc


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


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(args=argv)

    try:
        config = _load_config(args)

        # Check for shell mode
        if args.shell or args.command == "shell":
            from .shell import run_shell
            run_shell(config)
            return

        # Check if command was provided
        if not args.command:
            parser.print_help()
            sys.exit(1)

        client = _build_client(config)
        with client:
            func: Callable[[ClientConfig, httpx.Client, argparse.Namespace], None] = args.func
            func(config, client, args)
    except CliError as exc:  # pragma: no cover - CLI feedback path
        sys.stderr.write(f"Error: {exc}\n")
        sys.exit(1)
    except httpx.RequestError as exc:  # pragma: no cover - CLI feedback path
        sys.stderr.write(f"HTTP request failed: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
