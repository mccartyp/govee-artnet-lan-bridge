import asyncio

import pytest

from govee_artnet_lan_bridge.config import Config, ManualDevice
from govee_artnet_lan_bridge.db import apply_migrations
from govee_artnet_lan_bridge.devices import DeviceStateUpdate, DeviceStore
from govee_artnet_lan_bridge.sender import DeviceSenderService


async def _wait_for_drain(store: DeviceStore, timeout: float = 1.0) -> None:
    start = asyncio.get_event_loop().time()
    while True:
        pending = await store.pending_device_ids()
        if not pending:
            return
        if asyncio.get_event_loop().time() - start > timeout:
            raise TimeoutError("Queue did not drain in time")
        await asyncio.sleep(0.01)


def _fast_config(db_path) -> Config:
    return Config(
        db_path=db_path,
        dry_run=True,
        device_queue_poll_interval=0.01,
        device_idle_wait=0.01,
        device_backoff_base=0.01,
        device_backoff_factor=1.0,
        device_backoff_max=0.1,
    )


@pytest.mark.asyncio
async def test_queue_drains_when_device_missing_ip(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    config = _fast_config(db_path)
    store = DeviceStore(config.db_path)
    await store.create_manual_device(
        ManualDevice(id="dev-missing-ip", ip="", capabilities={"transport": "udp"})
    )
    await store.enqueue_state(
        DeviceStateUpdate(device_id="dev-missing-ip", payload={"foo": "bar"})
    )

    sender = DeviceSenderService(config, store)
    await sender.start()

    try:
        await _wait_for_drain(store)
    finally:
        await sender.stop()

    assert await store.pending_device_ids() == []
    dead_letters = await store.dead_letters("dev-missing-ip")
    assert len(dead_letters) == 1
    assert dead_letters[0].reason == "missing_ip"


@pytest.mark.asyncio
async def test_queue_drains_when_device_disabled_or_stale(tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    config = _fast_config(db_path)
    store = DeviceStore(config.db_path)
    device = await store.create_manual_device(
        ManualDevice(id="dev-disabled", ip="127.0.0.1", capabilities={"transport": "udp"})
    )
    assert device.enabled is True
    await store.enqueue_state(
        DeviceStateUpdate(device_id="dev-disabled", payload={"foo": "bar"})
    )

    await store.update_device("dev-disabled", enabled=False)

    sender = DeviceSenderService(config, store)
    await sender.start()

    try:
        await _wait_for_drain(store)
    finally:
        await sender.stop()

    assert await store.pending_device_ids() == []
    dead_letters = await store.dead_letters("dev-disabled")
    assert len(dead_letters) == 1
    assert dead_letters[0].reason == "device_unavailable"
