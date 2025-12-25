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
            model="API-Model",
            capabilities={"color_modes": ["color"], "supports_brightness": True},
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
            capabilities={"mode": "rgb", "order": ["r", "g", "b"], "supports_brightness": True},
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
        field="brightness",
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
