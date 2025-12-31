import json

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from govee_artnet_lan_bridge.api import create_app
from govee_artnet_lan_bridge.config import Config, ManualDevice
from govee_artnet_lan_bridge.db import apply_migrations
from govee_artnet_lan_bridge.devices import DeviceStore, DiscoveryResult


def test_reload_endpoint_triggers_callback() -> None:
    calls: list[str] = []

    async def _trigger() -> None:
        calls.append("hit")

    app = create_app(Config(), store=object(), health=None, reload_callback=_trigger)
    client = TestClient(app)
    response = client.post("/reload")
    assert response.status_code == 202
    assert response.json()["status"] == "reload_requested"
    assert calls == ["hit"]


def test_reload_endpoint_without_callback() -> None:
    app = create_app(Config(), store=object(), health=None, reload_callback=None)
    client = TestClient(app)
    response = client.post("/reload")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_devices_endpoint_reflects_discovery_state(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.record_discovery(
        DiscoveryResult(
            id="api-dev-1",
            ip="10.0.1.1",
            model_number="API-Model",
            capabilities={"color_modes": ["color"], "brightness": True},
        )
    )

    app = create_app(Config(), store=store, health=None, reload_callback=None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/devices")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    device = payload[0]
    assert device["id"] == "api-dev-1"
    assert device["discovered"] is True
    assert device["configured"] is False
    assert device["enabled"] is False


@pytest.mark.asyncio
async def test_channel_map_endpoint(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="api-map",
            ip="10.0.1.2",
            description="API Fixture",
            capabilities={"mode": "rgb", "order": ["r", "g", "b"], "brightness": True},
        )
    )
    await store.create_mapping(
        device_id="api-map",
        universe=0,
        channel=1,
        length=3,
    )
    await store.create_mapping(
        device_id="api-map",
        universe=0,
        channel=4,
        length=1,
        mapping_type="discrete",
        field="dimmer",
    )

    app = create_app(Config(), store=store, health=None, reload_callback=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/channel-map")

    assert response.status_code == 200
    payload = response.json()
    assert "0" in payload
    entries = payload["0"]
    assert any(entry["mapping_type"] == "range" for entry in entries)
    brightness_entries = [entry for entry in entries if entry.get("field") == "brightness"]
    assert brightness_entries
    assert brightness_entries[0]["device_description"] == "API Fixture"
    assert brightness_entries[0]["fields"] == ["brightness"]
    assert any(set(entry["fields"]) == {"r", "g", "b"} for entry in entries if entry["mapping_type"] == "range")


@pytest.mark.asyncio
async def test_mappings_endpoint_includes_fields(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="api-mappings",
            ip="10.0.3.1",
            description="API Fixture",
            capabilities={"mode": "rgb", "order": ["r", "g", "b"], "brightness": True},
        )
    )
    await store.create_mapping(
        device_id="api-mappings",
        universe=1,
        channel=1,
        length=3,
    )
    await store.create_mapping(
        device_id="api-mappings",
        universe=1,
        channel=4,
        length=1,
        mapping_type="discrete",
        field="dimmer",
    )

    app = create_app(Config(), store=store, health=None, reload_callback=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/mappings")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    range_entry = next(entry for entry in payload if entry["mapping_type"] == "range")
    discrete_entry = next(entry for entry in payload if entry["mapping_type"] == "discrete")
    assert set(range_entry["fields"]) == {"r", "g", "b"}
    assert discrete_entry["field"] == "brightness"
    assert discrete_entry["fields"] == ["brightness"]


@pytest.mark.asyncio
async def test_template_mapping_creation_via_api(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="api-template",
            ip="10.0.2.1",
            capabilities={"mode": "rgbw", "order": ["r", "g", "b", "w"], "brightness": True},
        )
    )

    app = create_app(Config(), store=store, health=None, reload_callback=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/mappings",
            json={
                "device_id": "api-template",
                "universe": 0,
                "template": "rgbaw",
                "start_channel": 10,
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert {entry["mapping_type"] for entry in payload} == {"discrete", "range"}
    channels = [entry["channel"] for entry in payload]
    assert channels == [10, 11]


@pytest.mark.asyncio
async def test_template_validation_returns_actionable_error(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="api-template-unsupported",
            ip="10.0.2.2",
            capabilities={"color_modes": [], "brightness": False},
        )
    )

    app = create_app(Config(), store=store, health=None, reload_callback=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/mappings",
            json={
                "device_id": "api-template-unsupported",
                "universe": 0,
                "template": "brightness_rgb",
                "start_channel": 1,
            },
        )

    assert response.status_code == 400
    assert "brightness" in response.json()["detail"]


@pytest.mark.asyncio
async def test_command_endpoint_enqueues_sanitized_payload(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="cmd-device",
            ip="10.0.5.1",
            capabilities={
                "color_modes": ["color", "ct"],
                "brightness": True,
                "color_temp_range": [2000, 6500],
            },
        )
    )

    app = create_app(Config(), store=store, health=None, reload_callback=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/devices/cmd-device/command",
            json={"on": True, "brightness": 10, "color": "336699", "kelvin": 128},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert len(payload["payloads"]) == 2

    turn_state = await store.next_state("cmd-device")
    assert turn_state is not None
    turn_payload = json.loads(turn_state.payload)
    assert turn_payload["msg"]["cmd"] == "turn"
    assert turn_payload["msg"]["data"]["value"] == 1
    await store.delete_state(turn_state.id)

    state = await store.next_state("cmd-device")
    assert state is not None
    queued = json.loads(state.payload)
    assert queued["msg"]["cmd"] == "devControl"
    assert queued["msg"]["data"]["brightness"] == 10
    assert queued["msg"]["data"]["color"] == {"r": 51, "g": 102, "b": 153}
    expected_kelvin = int(round(2000 + (6500 - 2000) * (128 / 255)))
    assert queued["msg"]["data"]["color_temp"] == expected_kelvin


@pytest.mark.asyncio
async def test_command_endpoint_turn_only(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="cmd-turn",
            ip="10.0.5.2",
            capabilities={"brightness": True},
        )
    )

    app = create_app(Config(), store=store, health=None, reload_callback=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/devices/cmd-turn/command",
            json={"off": True},
        )

    assert response.status_code == 202
    state = await store.next_state("cmd-turn")
    assert state is not None
    payload = json.loads(state.payload)
    assert payload["msg"]["cmd"] == "turn"
    assert payload["msg"]["data"]["value"] == 0
