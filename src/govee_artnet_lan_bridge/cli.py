"""Command-line client for interacting with the bridge HTTP API."""

from __future__ import annotations

import argparse
import json
import os
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
    parser = argparse.ArgumentParser(description="CLI for the Govee Artnet LAN bridge API")
    parser.add_argument(
        "--server-url",
        default=_env("SERVER_URL", DEFAULT_SERVER_URL),
        help="Base URL for the bridge API (env: GOVEE_ARTNET_SERVER_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=_env("API_KEY"),
        help="API key for authentication (env: GOVEE_ARTNET_API_KEY)",
    )
    parser.add_argument(
        "--api-bearer-token",
        default=_env("API_BEARER_TOKEN"),
        help="Bearer token for authentication (env: GOVEE_ARTNET_API_BEARER_TOKEN)",
    )
    parser.add_argument(
        "--output",
        choices=["json", "yaml"],
        default=_env("OUTPUT", "json"),
        help="Output format (env: GOVEE_ARTNET_OUTPUT)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_status_commands(subparsers)
    _add_device_commands(subparsers)
    _add_mapping_commands(subparsers)

    return parser


def _add_status_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    health = subparsers.add_parser("health", help="Check API health")
    health.set_defaults(func=_cmd_health)

    status = subparsers.add_parser("status", help="Show API status/metrics")
    status.set_defaults(func=_cmd_status)


def _add_device_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    devices = subparsers.add_parser("devices", help="Device management commands")
    device_sub = devices.add_subparsers(dest="device_command", required=True)

    list_cmd = device_sub.add_parser("list", help="List devices")
    list_cmd.set_defaults(func=_cmd_devices_list)

    add = device_sub.add_parser("add", help="Add a manual device")
    add.add_argument("--id", required=True, help="Device identifier")
    add.add_argument("--ip", required=True, help="Device IP address")
    add.add_argument("--model", help="Device model")
    add.add_argument("--description", help="Device description")
    add.add_argument("--capabilities", help="JSON string describing capabilities")
    add.add_argument("--enabled", action="store_true", default=None, help="Create device as enabled")
    add.add_argument("--disabled", action="store_true", help="Create device as disabled")
    add.set_defaults(func=_cmd_devices_add)

    update = device_sub.add_parser("update", help="Update a manual device")
    update.add_argument("device_id", help="Device identifier")
    update.add_argument("--ip", help="Device IP address")
    update.add_argument("--model", help="Device model")
    update.add_argument("--description", help="Device description")
    update.add_argument("--capabilities", help="JSON string describing capabilities")
    update.add_argument("--enable", action="store_true", help="Enable the device")
    update.add_argument("--disable", action="store_true", help="Disable the device")
    update.set_defaults(func=_cmd_devices_update)

    enable = device_sub.add_parser("enable", help="Enable a device")
    enable.add_argument("device_id", help="Device identifier")
    enable.set_defaults(func=_cmd_devices_enable)

    disable = device_sub.add_parser("disable", help="Disable a device")
    disable.add_argument("device_id", help="Device identifier")
    disable.set_defaults(func=_cmd_devices_disable)

    test = device_sub.add_parser("test", help="Send a test payload to a device")
    test.add_argument("device_id", help="Device identifier")
    test.add_argument("--payload", required=True, help="JSON payload to enqueue")
    test.set_defaults(func=_cmd_devices_test)


def _add_mapping_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    mappings = subparsers.add_parser("mappings", help="Mapping management commands")
    mapping_sub = mappings.add_subparsers(dest="mapping_command", required=True)

    list_cmd = mapping_sub.add_parser("list", help="List mappings")
    list_cmd.set_defaults(func=_cmd_mappings_list)

    get = mapping_sub.add_parser("get", help="Get a mapping by ID")
    get.add_argument("mapping_id", type=int, help="Mapping identifier")
    get.set_defaults(func=_cmd_mappings_get)

    create = mapping_sub.add_parser("create", help="Create a mapping")
    create.add_argument("--device-id", required=True, help="Device identifier")
    create.add_argument("--universe", required=True, type=int, help="DMX universe")
    create.add_argument("--channel", required=True, type=int, help="Starting DMX channel")
    create.add_argument("--length", required=True, type=int, help="Number of channels")
    create.add_argument("--allow-overlap", action="store_true", help="Allow overlapping ranges")
    create.set_defaults(func=_cmd_mappings_create)

    update = mapping_sub.add_parser("update", help="Update a mapping")
    update.add_argument("mapping_id", type=int, help="Mapping identifier")
    update.add_argument("--device-id", help="Device identifier")
    update.add_argument("--universe", type=int, help="DMX universe")
    update.add_argument("--channel", type=int, help="Starting DMX channel")
    update.add_argument("--length", type=int, help="Number of channels")
    update.add_argument("--allow-overlap", action="store_true", help="Allow overlapping ranges")
    update.add_argument(
        "--disallow-overlap",
        action="store_true",
        help="Explicitly disallow overlapping ranges",
    )
    update.set_defaults(func=_cmd_mappings_update)

    delete = mapping_sub.add_parser("delete", help="Delete a mapping")
    delete.add_argument("mapping_id", type=int, help="Mapping identifier")
    delete.set_defaults(func=_cmd_mappings_delete)


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
        "model": args.model,
        "description": args.description,
        "capabilities": capabilities,
    }
    if enabled is not None:
        payload["enabled"] = enabled
    data = _handle_response(client.post("/devices", json=payload))
    _print_output(data, config.output)


def _cmd_devices_update(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    payload: MutableMapping[str, Any] = {}
    if args.ip:
        payload["ip"] = args.ip
    if args.model:
        payload["model"] = args.model
    if args.description:
        payload["description"] = args.description
    if args.capabilities:
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


def _cmd_mappings_list(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    data = _handle_response(client.get("/mappings"))
    _print_output(data, config.output)


def _cmd_mappings_get(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    data = _handle_response(client.get(f"/mappings/{args.mapping_id}"))
    _print_output(data, config.output)


def _cmd_mappings_create(config: ClientConfig, client: httpx.Client, args: argparse.Namespace) -> None:
    payload = {
        "device_id": args.device_id,
        "universe": args.universe,
        "channel": args.channel,
        "length": args.length,
        "allow_overlap": args.allow_overlap,
    }
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


def _parse_json_arg(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise CliError("Failed to parse JSON argument") from exc


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(args=argv)

    try:
        config = _load_config(args)
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
