import pytest

from govee_artnet_lan_bridge.config import ManualDevice
from govee_artnet_lan_bridge.db import apply_migrations
from govee_artnet_lan_bridge.devices import DeviceStore, DiscoveryResult


@pytest.mark.asyncio
async def test_create_mapping_rejects_unsupported_mode(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="dev-unsupported",
            ip="127.0.0.1",
            capabilities={"color_modes": [], "supports_brightness": True},
        )
    )

    with pytest.raises(ValueError):
        await store.create_mapping(device_id="dev-unsupported", universe=0, channel=1, length=3)


@pytest.mark.asyncio
async def test_record_discovery_defaults_to_disabled(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)

    await store.record_discovery(
        DiscoveryResult(
            id="dev-discovered",
            ip="10.0.0.2",
            model="H6000",
            capabilities={"color_modes": ["color"], "supports_brightness": True},
        )
    )

    device = await store.device("dev-discovered")
    assert device is not None
    assert device.discovered is True
    assert device.configured is False
    assert device.enabled is False
    assert device.manual is False


@pytest.mark.asyncio
async def test_rediscovery_preserves_user_enabled_state(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)

    await store.record_discovery(
        DiscoveryResult(
            id="dev-rediscover",
            ip="10.0.0.3",
            model="H6001",
            capabilities={"color_modes": ["color"], "supports_brightness": True},
        )
    )
    await store.update_device(
        "dev-rediscover",
        enabled=False,
        model="H6001",
        capabilities={"color_modes": ["color"], "supports_brightness": True},
    )

    old_last_seen = "2000-01-01 00:00:00"

    def _set_last_seen(conn) -> None:
        conn.execute(
            "UPDATE devices SET last_seen = ? WHERE id = ?",
            (old_last_seen, "dev-rediscover"),
        )
        conn.commit()

    await store.db.run(_set_last_seen)

    await store.record_discovery(
        DiscoveryResult(
            id="dev-rediscover",
            ip="10.0.0.4",
            model="H6001",
            capabilities={
                "color_modes": ["color", "ct"],
                "supports_brightness": True,
                "color_temp_range": (1800, 6500),
            },
        )
    )

    device = await store.device("dev-rediscover")
    assert device is not None
    assert device.ip == "10.0.0.4"
    assert device.enabled is False
    assert device.configured is True
    assert device.last_seen != old_last_seen
    assert device.capabilities.get("color_temp_range") == [1800, 6500]
