import asyncio
import struct
from pathlib import Path

from govee_artnet_lan_bridge.artnet import (
    ARTNET_HEADER,
    ArtNetPacket,
    ArtNetService,
    DeviceMapping,
    DeviceMappingSpec,
    UniverseMapping,
    _apply_gamma_dimmer,
    _parse_artnet_packet,
)
from govee_artnet_lan_bridge.config import Config, ManualDevice
from govee_artnet_lan_bridge.db import apply_migrations
from govee_artnet_lan_bridge.devices import DeviceStore, MappingRecord


def build_artnet_packet(universe: int, payload: bytes) -> bytes:
    opcode = struct.pack("<H", 0x5000)
    prot_vers = b"\x00\x0e"
    seq = b"\x01"
    phys = b"\x00"
    universe_bytes = struct.pack("<H", universe)
    length = struct.pack(">H", len(payload))
    return ARTNET_HEADER + opcode + prot_vers + seq + phys + universe_bytes + length + payload


def test_parse_artnet_packet_round_trip() -> None:
    payload = bytes([1, 2, 3, 4])
    packet_bytes = build_artnet_packet(2, payload)
    packet = _parse_artnet_packet(packet_bytes)
    assert packet is not None
    assert packet.universe == 2
    assert packet.length == len(payload)
    assert packet.data == payload


def test_apply_gamma_and_dimmer() -> None:
    # With gamma > 1 the output should be darker, then scaled by dimmer
    adjusted = _apply_gamma_dimmer(200, gamma=2.0, dimmer=0.5)
    assert 0 <= adjusted <= 200
    assert adjusted < 200


def test_universe_mapping_apply() -> None:
    record = MappingRecord(
        device_id="dev-1",
        universe=0,
        channel=1,
        length=3,
        mapping_type="range",
        field=None,
        fields=("r", "g", "b"),
        capabilities={"mode": "rgb", "order": ["r", "g", "b"], "gamma": 1.0, "dimmer": 1.0},
    )
    mapping = DeviceMapping(
        record=record,
        spec=DeviceMappingSpec(mode="rgb", order=("r", "g", "b"), gamma=1.0, dimmer=1.0),
    )
    universe_map = UniverseMapping(0, [mapping])
    updates = universe_map.apply(bytes([10, 20, 30]))
    assert len(updates) == 1
    assert updates[0].device_id == "dev-1"
    assert updates[0].payload["color"] == {"r": 10, "g": 20, "b": 30}


def test_universe_mapping_merges_discrete_fields() -> None:
    red_record = MappingRecord(
        device_id="dev-merge",
        universe=0,
        channel=1,
        length=1,
        mapping_type="discrete",
        field="r",
        fields=("r",),
        capabilities={"gamma": 1.0, "dimmer": 1.0},
    )
    brightness_record = MappingRecord(
        device_id="dev-merge",
        universe=0,
        channel=2,
        length=1,
        mapping_type="discrete",
        field="dimmer",
        fields=("dimmer",),
        capabilities={"gamma": 1.0, "dimmer": 1.0},
    )
    red_mapping = DeviceMapping(
        record=red_record,
        spec=DeviceMappingSpec(mode="discrete", order=("r",), gamma=1.0, dimmer=1.0),
    )
    brightness_mapping = DeviceMapping(
        record=brightness_record,
        spec=DeviceMappingSpec(mode="discrete", order=("dimmer",), gamma=1.0, dimmer=1.0),
    )
    universe_map = UniverseMapping(0, [red_mapping, brightness_mapping])
    updates = universe_map.apply(bytes([50, 100]))
    assert len(updates) == 1
    payload = updates[0].payload
    assert payload["color"] == {"r": 50}
    assert payload["brightness"] == 100


def test_universe_mapping_range_and_discrete_merge() -> None:
    range_record = MappingRecord(
        device_id="dev-range",
        universe=0,
        channel=1,
        length=3,
        mapping_type="range",
        field=None,
        fields=("r", "g", "b"),
        capabilities={"mode": "rgb", "order": ["r", "g", "b"], "gamma": 1.0, "dimmer": 1.0},
    )
    brightness_record = MappingRecord(
        device_id="dev-range",
        universe=0,
        channel=4,
        length=1,
        mapping_type="discrete",
        field="dimmer",
        fields=("dimmer",),
        capabilities={"gamma": 1.0, "dimmer": 1.0},
    )
    range_mapping = DeviceMapping(
        record=range_record,
        spec=DeviceMappingSpec(mode="rgb", order=("r", "g", "b"), gamma=1.0, dimmer=1.0),
    )
    brightness_mapping = DeviceMapping(
        record=brightness_record,
        spec=DeviceMappingSpec(mode="discrete", order=("dimmer",), gamma=1.0, dimmer=1.0),
    )
    universe_map = UniverseMapping(0, [range_mapping, brightness_mapping])
    updates = universe_map.apply(bytes([5, 15, 25, 40]))
    assert len(updates) == 1
    payload = updates[0].payload
    assert payload["color"] == {"r": 5, "g": 15, "b": 25}
    assert payload["brightness"] == 40


def test_artnet_reuses_last_payloads(tmp_path: Path) -> None:
    asyncio.run(_run_artnet_reuse(tmp_path))


async def _run_artnet_reuse(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    config = Config(
        db_path=db_path,
        dry_run=True,
        device_queue_poll_interval=0.01,
        device_idle_wait=0.01,
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

    initial = {"dev-1": {"color": {"r": 1, "g": 2, "b": 3}}}
    artnet = ArtNetService(config, store, initial_last_payloads=initial)
    artnet._debounce_seconds = 0
    await artnet.start()
    try:
        packet = ArtNetPacket(universe=0, sequence=1, physical=0, length=3, data=bytes([1, 2, 3]))
        artnet.handle_packet(packet, ("127.0.0.1", config.artnet_port))
        await asyncio.sleep(0.05)
        assert await store.pending_device_ids() == []
    finally:
        await artnet.stop()
        await store.stop()
