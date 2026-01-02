import asyncio
import json
from pathlib import Path
from typing import Optional

import pytest

from dmx_lan_bridge.config import Config, ManualDevice
from dmx_lan_bridge.db import apply_migrations
from dmx_lan_bridge.devices import DeviceStore
from dmx_lan_bridge.health import HealthMonitor
from dmx_lan_bridge.poller import DevicePollerService
from dmx_lan_bridge.udp_protocol import GoveeProtocol


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode("utf-8")
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        if self.transport:
            self.transport.sendto(self.payload, addr)


class _RedirectResponder(asyncio.DatagramProtocol):
    def __init__(self, payload: dict[str, object], reply_port: int) -> None:
        self.payload = json.dumps(payload).encode("utf-8")
        self.reply_port = reply_port
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        if self.transport:
            self.transport.sendto(self.payload, ("127.0.0.1", self.reply_port))


@pytest.mark.asyncio
async def test_poller_marks_device_online_with_state(tmp_path: Path) -> None:
    reply_port = 49001
    device_port = 49003
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="poll-ok",
            ip="127.0.0.1",
            model_number="MOCK",
            capabilities={"device_port": device_port},
        )
    )
    loop = asyncio.get_running_loop()

    # Create mock protocol that responds on reply_port
    config = Config(
        db_path=db_path,
        device_poll_enabled=True,
        device_poll_interval=0.1,
        device_poll_timeout=0.2,
        device_poll_rate_per_second=100.0,
        device_poll_rate_burst=10,
        device_poll_port=device_port,
        discovery_reply_port=reply_port,
        discovery_multicast_address="239.255.255.250",
    )

    # Create protocol and responder
    protocol = GoveeProtocol(config, loop)
    responder_payload = {
        "msg": {
            "cmd": "devStatus",
            "data": {
                "device": "poll-ok",
                "state": {
                    "onOff": 1,
                    "brightness": 42,
                    "color": {"r": 12, "g": 34, "b": 56, "w": 78},
                    "colorTemp": 3200,
                    "temp": 23.5,
                    "workMode": "music",
                    "lightingEffects": ["sunrise"],
                    "ext": {"seg": 1},
                },
                "property": [{"fwVersion": "1.2.3"}],
            },
        }
    }
    responder = _Responder(responder_payload)

    # Start protocol on reply_port
    protocol_transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("127.0.0.1", reply_port)
    )

    # Start responder on device_port to simulate device
    device_transport, _ = await loop.create_datagram_endpoint(
        lambda: responder, local_addr=("127.0.0.1", device_port)
    )

    health = HealthMonitor(("poller",), failure_threshold=2, cooldown_seconds=0.1)
    poller = DevicePollerService(config, store, protocol=protocol, health=health)

    await poller.start()
    await asyncio.sleep(0.3)
    await poller.stop()
    protocol_transport.close()
    device_transport.close()

    device = await store.device("poll-ok")
    assert device is not None
    assert device.offline is False
    assert device.poll_health == "healthy"
    assert device.poll_last_success_at is not None
    assert device.poll_state == {
        "device": "poll-ok",
        "firmware": "1.2.3",
        "power": True,
        "brightness": 42,
        "color_temperature": 3200,
        "temperature": 23.5,
        "mode": "music",
        "effects": ["sunrise"],
        "color": {"r": 12, "g": 34, "b": 56, "w": 78},
        "ext": {"seg": 1},
    }


@pytest.mark.asyncio
async def test_poller_receives_response_on_shared_bus(tmp_path: Path) -> None:
    reply_port = 49007
    device_port = 49008
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="poll-bus",
            ip="127.0.0.1",
            model_number="MOCK",
            capabilities={"device_port": device_port},
        )
    )
    loop = asyncio.get_running_loop()

    config = Config(
        db_path=db_path,
        device_poll_enabled=True,
        device_poll_interval=0.1,
        device_poll_timeout=0.2,
        device_poll_rate_per_second=100.0,
        device_poll_rate_burst=10,
        device_poll_port=device_port,
        discovery_reply_port=reply_port,
        discovery_multicast_address="239.255.255.250",
    )

    protocol = GoveeProtocol(config, loop)
    protocol_transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("127.0.0.1", reply_port)
    )

    responder_payload = {
        "msg": {
            "cmd": "devStatus",
            "data": {
                "device": "poll-bus",
                "state": {"onOff": 1},
            },
        }
    }
    device_transport, _ = await loop.create_datagram_endpoint(
        lambda: _RedirectResponder(responder_payload, reply_port),
        local_addr=("127.0.0.1", device_port),
    )

    health = HealthMonitor(("poller",), failure_threshold=2, cooldown_seconds=0.1)
    poller = DevicePollerService(config, store, protocol=protocol, health=health)

    await poller.start()
    await asyncio.sleep(0.3)
    await poller.stop()
    protocol_transport.close()
    device_transport.close()

    device = await store.device("poll-bus")
    assert device is not None
    assert device.offline is False
    assert device.poll_health == "healthy"
    assert device.poll_last_success_at is not None


@pytest.mark.asyncio
async def test_poller_marks_device_offline_after_failures(tmp_path: Path) -> None:
    reply_port = 49002
    device_port = 49004
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="poll-fail",
            ip="127.0.0.1",
            model_number="MOCK",
            capabilities={"device_port": device_port},
        )
    )
    loop = asyncio.get_running_loop()

    config = Config(
        db_path=db_path,
        device_poll_enabled=True,
        device_poll_interval=0.1,
        device_poll_timeout=0.1,
        device_poll_rate_per_second=100.0,
        device_poll_rate_burst=10,
        device_poll_port=device_port,
        discovery_reply_port=reply_port,
        discovery_multicast_address="239.255.255.250",
    )

    # Create protocol (no responder, so poll will timeout)
    protocol = GoveeProtocol(config, loop)
    protocol_transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("127.0.0.1", reply_port)
    )

    health = HealthMonitor(("poller",), failure_threshold=2, cooldown_seconds=0.1)
    poller = DevicePollerService(config, store, protocol=protocol, health=health)

    await poller.start()
    await asyncio.sleep(0.3)
    await poller.stop()
    protocol_transport.close()

    device = await store.device("poll-fail")
    assert device is not None
    assert device.offline is True
    assert device.poll_health == "offline"
    assert device.poll_failure_count >= 1
    assert device.poll_last_failure_at is not None


@pytest.mark.asyncio
async def test_poller_marks_device_degraded_before_offline(tmp_path: Path) -> None:
    reply_port = 49005
    device_port = 49006
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="poll-degraded",
            ip="127.0.0.1",
            model_number="MOCK",
            capabilities={"device_port": device_port},
        )
    )
    loop = asyncio.get_running_loop()

    config = Config(
        db_path=db_path,
        device_poll_enabled=True,
        device_poll_interval=0.3,
        device_poll_timeout=0.05,
        device_poll_rate_per_second=100.0,
        device_poll_rate_burst=10,
        device_poll_port=device_port,
        discovery_reply_port=reply_port,
        discovery_multicast_address="239.255.255.250",
    )

    protocol = GoveeProtocol(config, loop)
    protocol_transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("127.0.0.1", reply_port)
    )

    health = HealthMonitor(("poller",), failure_threshold=2, cooldown_seconds=0.1)
    poller = DevicePollerService(config, store, protocol=protocol, health=health)

    await poller.start()
    await asyncio.sleep(0.15)
    await poller.stop()

    device = await store.device("poll-degraded")
    assert device is not None
    assert device.offline is False
    assert device.poll_health == "degraded"
    assert device.poll_failure_count >= 1

    # Restart to accumulate enough failures to go offline
    await poller.start()
    await asyncio.sleep(0.35)
    await poller.stop()
    protocol_transport.close()

    device = await store.device("poll-degraded")
    assert device is not None
    assert device.offline is True
    assert device.poll_health == "offline"
