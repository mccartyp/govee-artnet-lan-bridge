import asyncio
from pathlib import Path
from tempfile import NamedTemporaryFile

from govee_artnet_lan_bridge.config import Config
from govee_artnet_lan_bridge.db import apply_migrations
from govee_artnet_lan_bridge.devices import DeviceStore
from govee_artnet_lan_bridge.discovery import DiscoveryProtocol, _parse_payload


def test_parse_payload_handles_common_keys() -> None:
    addr = ("1.2.3.4", 4003)
    payload = {"data": {"device": "dev-1", "ip": "1.2.3.4", "model": "H6123"}}
    parsed = _parse_payload(payload, addr)
    assert parsed is not None
    assert parsed.id == "dev-1"
    assert parsed.ip == "1.2.3.4"
    assert parsed.model == "H6123"
    assert parsed.manual is False


def test_parse_payload_falls_back_to_socket_ip() -> None:
    addr = ("4.3.2.1", 4003)
    payload = {"deviceId": "dev-2", "description": "strip"}
    parsed = _parse_payload(payload, addr)
    assert parsed is not None
    assert parsed.id == "dev-2"
    assert parsed.ip == "4.3.2.1"
    assert parsed.description == "strip"


def test_parse_payload_handles_msg_wrapper() -> None:
    addr = ("192.168.1.100", 4002)
    payload = {
        "msg": {
            "cmd": "scan",
            "data": {
                "device": "AB:CD:EF:12:34:56:78:90",
                "sku": "H6104",
                "ip": "192.168.1.100",
            }
        }
    }
    parsed = _parse_payload(payload, addr)
    assert parsed is not None
    assert parsed.id == "AB:CD:EF:12:34:56:78:90"
    assert parsed.ip == "192.168.1.100"
    assert parsed.model == "H6104"
    assert parsed.manual is False


def test_protocol_records_discovery_result() -> None:
    with NamedTemporaryFile() as db_file:
        config = Config(db_path=Path(db_file.name), dry_run=True)
        apply_migrations(config.db_path)
        store = DeviceStore(config.db_path)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            protocol = DiscoveryProtocol(config, store, loop)
            protocol.datagram_received(
                b'{"device":"dev-3","ip":"5.5.5.5","model":"H7000"}',
                ("5.5.5.5", 4003),
            )
            loop.run_until_complete(asyncio.sleep(0))
            device = loop.run_until_complete(store.device("dev-3"))
            assert device is not None
            assert device.ip == "5.5.5.5"
            assert device.model == "H7000"
        finally:
            asyncio.set_event_loop(None)
            loop.close()
