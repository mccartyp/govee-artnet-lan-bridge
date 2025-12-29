import json
from argparse import Namespace
from typing import Any

import httpx
import pytest

from govee_artnet_lan_bridge.cli import (
    CliError,
    ClientConfig,
    _cmd_devices_command,
    _cmd_mappings_create,
)


def _client_with_capture(captured: dict, status: int = 201, response_json: Any = None) -> httpx.Client:
    def _handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode())
        captured["url"] = str(request.url)
        return httpx.Response(status, json=response_json if response_json is not None else [])

    transport = httpx.MockTransport(_handler)
    return httpx.Client(transport=transport, base_url="http://test")


def test_cli_template_payload_includes_start_channel() -> None:
    captured: dict = {}
    config = ClientConfig(server_url="http://test", api_key=None, api_bearer_token=None, output="json")
    args = Namespace(
        device_id="cli-template",
        universe=1,
        channel=None,
        start_channel=5,
        template="rgb",
        length=None,
        mapping_type="range",
        field=None,
        allow_overlap=False,
    )

    with _client_with_capture(captured) as client:
        _cmd_mappings_create(config, client, args)

    assert captured["json"]["template"] == "rgb"
    assert captured["json"]["start_channel"] == 5
    assert captured["json"]["device_id"] == "cli-template"
    assert captured["json"]["universe"] == 1


def test_cli_requires_channel_without_template() -> None:
    config = ClientConfig(server_url="http://test", api_key=None, api_bearer_token=None, output="json")
    args = Namespace(
        device_id="cli-template",
        universe=1,
        channel=None,
        start_channel=None,
        template=None,
        length=None,
        mapping_type="range",
        field=None,
        allow_overlap=False,
    )

    with _client_with_capture({}) as client:
        with pytest.raises(CliError):
            _cmd_mappings_create(config, client, args)


def test_cli_device_command_payload_and_url() -> None:
    captured: dict = {}
    config = ClientConfig(server_url="http://test", api_key=None, api_bearer_token=None, output="json")
    args = Namespace(
        device_id="device-123",
        on=True,
        off=False,
        brightness=25,
        color="#0f0f0f",
        kelvin=3500,
    )

    with _client_with_capture(captured, status=202, response_json={"status": "queued"}) as client:
        _cmd_devices_command(config, client, args)

    assert captured["url"].endswith("/devices/device-123/command")
    assert captured["json"] == {
        "on": True,
        "brightness": 25,
        "color": "0f0f0f",
        "kelvin": 3500,
    }
