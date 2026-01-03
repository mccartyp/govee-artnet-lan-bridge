import asyncio
from pathlib import Path

from dmx_lan_bridge.artnet import ArtNetPacket, ArtNetService
from dmx_lan_bridge.config import Config, ManualDevice
from dmx_lan_bridge.db import apply_migrations
from dmx_lan_bridge.devices import DeviceStore
from dmx_lan_bridge.dmx import DmxMappingService
from dmx_lan_bridge.sender import DeviceSenderService


def test_dry_run_pipeline(tmp_path: Path) -> None:
    asyncio.run(_run_pipeline(tmp_path))


async def _run_pipeline(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    config = Config(
        db_path=db_path,
        dry_run=True,
        device_queue_poll_interval=0.01,
        device_idle_wait=0.01,
        device_max_send_rate=100.0,
        device_backoff_base=0.01,
    )
    store = DeviceStore(config.db_path)
    await store.create_manual_device(
        ManualDevice(id="dev-1", ip="127.0.0.1", capabilities={"transport": "udp"})
    )
    await store.create_mapping(
        device_id="dev-1",
        universe=0,
        channel=1,
        length=3,
        allow_overlap=True,
    )

    dmx_mapper = DmxMappingService(config, store)
    dmx_mapper._debounce_seconds = 0  # expedite flush in tests

    artnet = ArtNetService(config, dmx_mapper=dmx_mapper)
    await artnet.start()

    sender = DeviceSenderService(config, store)
    await sender.start()

    try:
        packet = ArtNetPacket(universe=0, sequence=1, physical=0, length=3, data=bytes([1, 2, 3]))
        artnet.handle_packet(packet, ("127.0.0.1", config.artnet_port))
        await asyncio.sleep(0.05)

        # sender should have drained the queue even in dry-run mode
        assert await store.pending_device_ids() == []
        device_info = await store.device_info("dev-1")
        assert device_info is not None
        assert device_info.offline is False
    finally:
        await artnet.stop()
        await sender.stop()
