import pytest

from govee_artnet_lan_bridge.config import ManualDevice
from govee_artnet_lan_bridge.db import apply_migrations
from govee_artnet_lan_bridge.devices import DeviceStore


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
