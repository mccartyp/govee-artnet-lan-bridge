import struct

from govee_artnet_lan_bridge.artnet import (
    ARTNET_HEADER,
    DeviceMapping,
    DeviceMappingSpec,
    UniverseMapping,
    _apply_gamma_dimmer,
    _parse_artnet_packet,
)
from govee_artnet_lan_bridge.devices import MappingRecord


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
