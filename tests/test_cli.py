import json
from argparse import Namespace

import httpx
import pytest

from govee_artnet_lan_bridge.cli import CliError, ClientConfig, _cmd_mappings_create


def _client_with_capture(captured: dict) -> httpx.Client:
    def _handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(201, json=[])

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
