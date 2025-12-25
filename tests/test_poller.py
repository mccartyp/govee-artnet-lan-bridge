import asyncio
import json
from pathlib import Path
from typing import Optional

import pytest

from govee_artnet_lan_bridge.config import Config, ManualDevice
from govee_artnet_lan_bridge.db import apply_migrations
from govee_artnet_lan_bridge.devices import DeviceStore
from govee_artnet_lan_bridge.health import HealthMonitor
from govee_artnet_lan_bridge.poller import DevicePollerService


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode("utf-8")
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        if self.transport:
            self.transport.sendto(self.payload, addr)


@pytest.mark.asyncio
async def test_poller_marks_device_online_with_state(tmp_path: Path) -> None:
    port = 49001
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="poll-ok",
            ip="127.0.0.1",
            model="MOCK",
            capabilities={},
        )
    )
    loop = asyncio.get_running_loop()
    responder = _Responder({"device": "poll-ok", "state": {"brightness": 42}})
    transport, _ = await loop.create_datagram_endpoint(
        lambda: responder, local_addr=("127.0.0.1", port)
    )

    config = Config(
        db_path=db_path,
        device_poll_enabled=True,
        device_poll_interval=0.1,
        device_poll_timeout=0.2,
        device_poll_rate_per_second=100.0,
        device_poll_rate_burst=10,
        device_poll_port=port,
    )
    health = HealthMonitor(("poller",), failure_threshold=2, cooldown_seconds=0.1)
    poller = DevicePollerService(config, store, health=health)

    await poller.start()
    await asyncio.sleep(0.2)
    await poller.stop()
    transport.close()

    device = await store.device("poll-ok")
    assert device is not None
    assert device.offline is False
    assert device.poll_last_success_at is not None
    assert device.poll_state == {"brightness": 42}


@pytest.mark.asyncio
async def test_poller_marks_device_offline_after_failures(tmp_path: Path) -> None:
    port = 49002
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="poll-fail",
            ip="127.0.0.1",
            model="MOCK",
            capabilities={},
        )
    )

    config = Config(
        db_path=db_path,
        device_poll_enabled=True,
        device_poll_interval=0.1,
        device_poll_timeout=0.1,
        device_poll_offline_threshold=1,
        device_poll_rate_per_second=100.0,
        device_poll_rate_burst=10,
        device_poll_port=port,
    )
    health = HealthMonitor(("poller",), failure_threshold=2, cooldown_seconds=0.1)
    poller = DevicePollerService(config, store, health=health)

    await poller.start()
    await asyncio.sleep(0.2)
    await poller.stop()

    device = await store.device("poll-fail")
    assert device is not None
    assert device.offline is True
    assert device.poll_failure_count >= 1
    assert device.poll_last_failure_at is not None
