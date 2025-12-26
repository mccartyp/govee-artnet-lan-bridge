import pytest

from govee_artnet_lan_bridge.capabilities import load_embedded_catalog
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
            capabilities={"color_modes": [], "brightness": True},
        )
    )

    with pytest.raises(ValueError):
        await store.create_mapping(device_id="dev-unsupported", universe=0, channel=1, length=3)


@pytest.mark.asyncio
async def test_duplicate_field_assignments_blocked(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="dev-fields",
            ip="127.0.0.1",
            capabilities={"mode": "rgb", "order": ["r", "g", "b"], "brightness": True},
        )
    )
    await store.create_mapping(
        device_id="dev-fields",
        universe=0,
        channel=1,
        length=3,
    )
    with pytest.raises(ValueError) as excinfo:
        await store.create_mapping(
            device_id="dev-fields",
            universe=0,
            channel=10,
            length=1,
            mapping_type="discrete",
            field="r",
        )
    assert "Field(s) already mapped" in str(excinfo.value)


@pytest.mark.asyncio
async def test_channel_map_reports_fields(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="dev-map",
            ip="127.0.0.1",
            description="Fixture",
            capabilities={"mode": "rgb", "order": ["r", "g", "b"], "brightness": True},
        )
    )
    await store.create_mapping(
        device_id="dev-map",
        universe=0,
        channel=1,
        length=3,
    )
    await store.create_mapping(
        device_id="dev-map",
        universe=0,
        channel=4,
        length=1,
        mapping_type="discrete",
        field="brightness",
    )
    channel_map = await store.channel_map()
    assert 0 in channel_map
    entries = channel_map[0]
    assert any(entry["mapping_type"] == "range" and set(entry["fields"]) == {"r", "g", "b"} for entry in entries)
    brightness_entries = [entry for entry in entries if entry.get("field") == "brightness"]
    assert brightness_entries
    assert brightness_entries[0]["device_description"] == "Fixture"
    assert brightness_entries[0]["device_ip"] == "127.0.0.1"


@pytest.mark.asyncio
async def test_record_discovery_defaults_to_disabled(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)

    await store.record_discovery(
        DiscoveryResult(
            id="dev-discovered",
            ip="10.0.0.2",
            model_number="H6000",
            capabilities={"color_modes": ["color"], "brightness": True},
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
            model_number="H6001",
            capabilities={"color_modes": ["color"], "brightness": True},
        )
    )
    await store.update_device(
        "dev-rediscover",
        enabled=False,
        model_number="H6001",
        capabilities={"color_modes": ["color"], "brightness": True},
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
            model_number="H6001",
            capabilities={
                "color_modes": ["color", "ct"],
                "brightness": True,
                "color_temp_range": (1800, 6500),
            },
        )
    )

    device = await store.device("dev-rediscover")
    assert device is not None
    assert device.ip == "10.0.0.4"
    assert device.enabled is False
    assert device.configured is False
    assert device.last_seen != old_last_seen
    assert device.capabilities.get("color_temp_range") == [1800, 6500]


@pytest.mark.asyncio
async def test_catalog_hydrates_missing_capabilities(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    catalog = load_embedded_catalog()
    store = DeviceStore(db_path, capability_catalog=catalog)

    await store.record_discovery(
        DiscoveryResult(
            id="dev-catalog",
            ip="10.0.0.5",
            model_number="H6050",
        )
    )

    device = await store.device("dev-catalog")
    assert device is not None
    assert device.model_number == "H6050"
    assert device.device_type == "led_strip"
    assert "color" in device.capabilities.get("color_modes", [])
    assert device.capabilities.get("brightness") is True
    assert device.capabilities.get("device_type") == "led_strip"


@pytest.mark.asyncio
async def test_manual_device_remains_unconfigured_until_mapped(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="dev-configured",
            ip="127.0.0.10",
            capabilities={"mode": "rgb", "order": ["r", "g", "b"], "brightness": True},
        )
    )

    device = await store.device("dev-configured")
    assert device is not None
    assert device.configured is False

    mapping = await store.create_mapping(
        device_id="dev-configured",
        universe=0,
        channel=1,
        length=3,
    )

    device = await store.device("dev-configured")
    assert device is not None
    assert device.configured is True

    await store.delete_mapping(mapping.id)
    device = await store.device("dev-configured")
    assert device is not None
    assert device.configured is False


@pytest.mark.asyncio
async def test_remapping_updates_configured_flags(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="dev-source",
            ip="127.0.0.20",
            capabilities={"mode": "rgb", "order": ["r", "g", "b"], "brightness": True},
        )
    )
    await store.create_manual_device(
        ManualDevice(
            id="dev-target",
            ip="127.0.0.21",
            capabilities={"mode": "rgb", "order": ["r", "g", "b"], "brightness": True},
        )
    )
    mapping = await store.create_mapping(
        device_id="dev-source",
        universe=0,
        channel=1,
        length=3,
    )

    await store.update_mapping(mapping.id, device_id="dev-target")

    source = await store.device("dev-source")
    target = await store.device("dev-target")
    assert source is not None
    assert source.configured is False
    assert target is not None
    assert target.configured is True


@pytest.mark.asyncio
async def test_poll_targets_use_catalog_when_missing_capabilities(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    catalog = load_embedded_catalog()
    store = DeviceStore(db_path, capability_catalog=catalog)
    await store.create_manual_device(
        ManualDevice(
            id="dev-poll",
            ip="127.0.0.30",
            model_number="H7001",
        )
    )

    def _clear_capabilities(conn) -> None:
        conn.execute("UPDATE devices SET capabilities = NULL WHERE id = ?", ("dev-poll",))
        conn.commit()

    await store.db.run(_clear_capabilities)

    targets = await store.poll_targets()
    target = next(target for target in targets if target.id == "dev-poll")
    assert "ct" in target.capabilities.get("color_modes", [])
    assert target.capabilities.get("brightness") is True
    assert target.device_type == "led_strip"


@pytest.mark.asyncio
async def test_template_expansion_creates_expected_mappings(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="dev-template",
            ip="127.0.0.1",
            capabilities={"mode": "rgb", "order": ["r", "g", "b"], "brightness": True},
        )
    )

    rows = await store.create_template_mappings(
        device_id="dev-template",
        universe=1,
        start_channel=5,
        template="brightness_rgb",
    )

    assert len(rows) == 2
    first, second = rows
    assert first.mapping_type == "discrete"
    assert first.field == "brightness"
    assert first.channel == 5
    assert first.length == 1
    assert second.mapping_type == "range"
    assert second.channel == 6
    assert second.length == 3


@pytest.mark.asyncio
async def test_template_validation_rejects_incompatible_device(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="dev-template-unsupported",
            ip="127.0.0.1",
            capabilities={"color_modes": [], "brightness": False},
        )
    )

    with pytest.raises(ValueError) as excinfo:
        await store.create_template_mappings(
            device_id="dev-template-unsupported",
            universe=0,
            start_channel=1,
            template="brightness_rgb",
        )

    assert "brightness" in str(excinfo.value)
