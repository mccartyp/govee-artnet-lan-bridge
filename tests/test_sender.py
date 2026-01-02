import asyncio

import pytest

from dmx_lan_bridge.config import Config, ManualDevice
from dmx_lan_bridge.db import apply_migrations
from dmx_lan_bridge.devices import DeviceStateUpdate, DeviceStore
from dmx_lan_bridge.sender import DeviceSenderService


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


@pytest.mark.asyncio
async def test_rate_limit_throttles_sends(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    config = Config(
        db_path=db_path,
        dry_run=True,
        device_queue_poll_interval=0.01,
        device_idle_wait=0.0,
        device_backoff_base=0.0,
        device_backoff_factor=1.0,
        device_backoff_max=0.1,
        device_max_send_rate=0.0,
        rate_limit_per_second=2.0,
        rate_limit_burst=1,
    )
    store = DeviceStore(config.db_path)
    await store.create_manual_device(
        ManualDevice(id="dev-rate-limit", ip="127.0.0.1", capabilities={"transport": "udp"})
    )

    send_times = []

    async def _fake_send(self, *_args, **_kwargs):
        send_times.append(asyncio.get_event_loop().time())
        return True

    monkeypatch.setattr(DeviceSenderService, "_send_with_retries", _fake_send)

    for idx in range(3):
        await store.enqueue_state(
            DeviceStateUpdate(device_id="dev-rate-limit", payload={"seq": idx})
        )

    sender = DeviceSenderService(config, store)
    await sender.start()

    try:
        await _wait_for_drain(store, timeout=2.0)
    finally:
        await sender.stop()

    assert len(send_times) == 3
    spacing = [send_times[i + 1] - send_times[i] for i in range(len(send_times) - 1)]
    assert spacing[0] >= 0.45
    assert spacing[1] >= 0.45


@pytest.mark.asyncio
async def test_rate_limit_allows_burst(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    config = Config(
        db_path=db_path,
        dry_run=True,
        device_queue_poll_interval=0.01,
        device_idle_wait=0.0,
        device_backoff_base=0.0,
        device_backoff_factor=1.0,
        device_backoff_max=0.1,
        device_max_send_rate=0.0,
        rate_limit_per_second=10.0,
        rate_limit_burst=3,
    )
    store = DeviceStore(config.db_path)
    await store.create_manual_device(
        ManualDevice(id="dev-rate-burst", ip="127.0.0.1", capabilities={"transport": "udp"})
    )

    send_times = []

    async def _fake_send(self, *_args, **_kwargs):
        send_times.append(asyncio.get_event_loop().time())
        return True

    monkeypatch.setattr(DeviceSenderService, "_send_with_retries", _fake_send)

    for idx in range(4):
        await store.enqueue_state(
            DeviceStateUpdate(device_id="dev-rate-burst", payload={"seq": idx})
        )

    sender = DeviceSenderService(config, store)
    await sender.start()

    try:
        await _wait_for_drain(store, timeout=2.0)
    finally:
        await sender.stop()

    assert len(send_times) == 4
    spacing = [send_times[i + 1] - send_times[i] for i in range(len(send_times) - 1)]
    assert spacing[0] < 0.3
    assert spacing[1] < 0.3
    assert spacing[2] >= 0.08
