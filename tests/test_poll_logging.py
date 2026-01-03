import asyncio
import logging
from pathlib import Path

import pytest

import dmx_lan_bridge.poller as poller_module
from dmx_lan_bridge.config import Config, ManualDevice
from dmx_lan_bridge.db import apply_migrations
from dmx_lan_bridge.devices import DeviceStore, PollTarget
from dmx_lan_bridge.poller import DevicePollerService


class _DummyHandler:
    def __init__(self, parsed_state):
        self._state = parsed_state

    def supports_polling(self) -> bool:
        return True

    def build_poll_request(self) -> bytes:
        return b"noop"

    def parse_poll_response(self, data: bytes):
        return self._state


class _DummyStore:
    def __init__(self) -> None:
        self.successes: list[tuple] = []
        self.failures: list[tuple] = []

    async def record_poll_success(self, device_id, state, **kwargs):
        self.successes.append((device_id, state, kwargs))

    async def record_poll_failure(self, device_id, offline_threshold, **kwargs):
        self.failures.append((device_id, offline_threshold, kwargs))


@pytest.mark.asyncio
async def test_poller_logs_success_and_timeout(monkeypatch, caplog, tmp_path: Path) -> None:
    config = Config(db_path=tmp_path / "bridge.sqlite3", device_poll_enabled=True)
    store = _DummyStore()
    poller = DevicePollerService(config, store)
    target = PollTarget(
        id="poll-log",
        ip="127.0.0.1",
        protocol="dummy",
        port=4003,
        model_number=None,
        device_type=None,
        length_meters=None,
        led_count=None,
        led_density_per_meter=None,
        has_zones=None,
        zone_count=None,
        capabilities={},
        offline=False,
        poll_failure_count=0,
    )

    async def _noop_rate_limit() -> None:
        return None

    monkeypatch.setattr(poller, "_acquire_rate_limit", _noop_rate_limit)

    # Success path
    success_handler = _DummyHandler({"ok": True})
    monkeypatch.setattr(poller_module, "get_protocol_handler", lambda protocol: success_handler)
    monkeypatch.setattr(poller, "_send_poll", lambda tgt, handler: asyncio.sleep(0, result=b"{}"))

    caplog.set_level(logging.DEBUG, logger="artnet.poller")
    await poller._poll_target(target)

    assert any("Poll response received" in record.message for record in caplog.records)
    assert any("Poll response parsed" in record.message for record in caplog.records)
    assert store.successes

    # Timeout path
    caplog.clear()
    timeout_handler = _DummyHandler(None)
    monkeypatch.setattr(poller_module, "get_protocol_handler", lambda protocol: timeout_handler)

    async def _timeout_send_poll(tgt, handler):
        return None

    monkeypatch.setattr(poller, "_send_poll", _timeout_send_poll)

    await poller._poll_target(target)

    assert any("Poll timed out waiting for response" in record.message for record in caplog.records)
    assert store.failures


@pytest.mark.asyncio
async def test_device_store_logs_poll_transitions(tmp_path: Path, caplog) -> None:
    db_path = tmp_path / "bridge.sqlite3"
    apply_migrations(db_path)
    store = DeviceStore(db_path)
    await store.create_manual_device(
        ManualDevice(
            id="log-device",
            ip="127.0.0.1",
            capabilities={"color_modes": ["color"], "brightness": True},
        )
    )

    caplog.set_level(logging.DEBUG, logger="devices.store")

    await store.record_poll_failure("log-device", offline_threshold=1, ip="127.0.0.1", protocol="dummy", port=4003, failure_reason="timeout")
    assert any("Recorded poll failure" in record.message for record in caplog.records)

    caplog.clear()

    await store.record_poll_success("log-device", state={}, ip="127.0.0.1", protocol="dummy", port=4003)
    assert any("Recorded poll success" in record.message for record in caplog.records)
