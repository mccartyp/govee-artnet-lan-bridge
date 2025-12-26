"""Discovery service for Govee devices."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import struct
import time
from typing import Any, Dict, Mapping, Optional, Tuple

from .config import Config
from .devices import DeviceStore, DiscoveryResult
from .logging import get_logger
from .metrics import observe_discovery_cycle, record_discovery_error, record_discovery_response


def _create_multicast_socket(address: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with contextlib.suppress(AttributeError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    sock.bind(("", port))

    group = socket.inet_aton(address)
    mreq = struct.pack("4s4s", group, socket.inet_aton("0.0.0.0"))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setblocking(False)
    return sock


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Asyncio protocol handling discovery responses."""

    def __init__(
        self,
        config: Config,
        store: DeviceStore,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.config = config
        self.store = store
        self.loop = loop
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.logger = get_logger("govee.discovery.protocol")
        self._seen: Dict[str, str] = {}
        self._probe_payload = self.config.discovery_probe_payload.encode("utf-8")

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self.logger.info(
            "Discovery transport ready",
            extra={"local": transport.get_extra_info("sockname")},
        )

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            self.logger.error(
                "Discovery transport error",
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        self.logger.info("Discovery transport closed")
        self.transport = None

    def reset_cycle(self) -> None:
        self._seen.clear()

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            message = data.decode("utf-8")
        except UnicodeDecodeError:
            record_discovery_error("non_utf8")
            self.logger.debug("Ignoring non-UTF8 discovery response", extra={"from": addr})
            return
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            record_discovery_error("non_json")
            self.logger.debug("Ignoring non-JSON discovery response", extra={"from": addr})
            return

        self.logger.debug("Received discovery response", extra={"from": addr, "payload": payload})

        parsed = _parse_payload(payload, addr)
        if parsed is None:
            record_discovery_error("invalid_payload")
            self.logger.warning(
                "Failed to parse discovery response",
                extra={"from": addr, "payload": payload}
            )
            return
        previous_ip = self._seen.get(parsed.id)
        self._seen[parsed.id] = parsed.ip
        if previous_ip and previous_ip == parsed.ip:
            self.logger.debug("Ignoring duplicate discovery response", extra={"device_id": parsed.id, "ip": parsed.ip})
            return

        self.logger.info("Discovered device", extra={"device_id": parsed.id, "ip": parsed.ip, "model": parsed.model})
        record_discovery_response("multicast")
        self.loop.create_task(self.store.record_discovery(parsed))

    def send_probe(self, target: Tuple[str, int]) -> None:
        if not self.transport:
            self.logger.warning("Cannot send probe; transport not ready", extra={"target": target})
            return
        try:
            self.transport.sendto(self._probe_payload, target)
        except OSError as exc:
            self.logger.error(
                "Failed to send discovery probe",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"target": target},
            )


def _parse_payload(
    payload: Any, addr: Tuple[str, int]
) -> Optional[DiscoveryResult]:
    if not isinstance(payload, Mapping):
        return None

    # Check for "msg" wrapper (standard Govee response format)
    data: Mapping[str, Any]
    if "msg" in payload and isinstance(payload["msg"], Mapping):
        msg = payload["msg"]
        # Verify it's a scan response
        if msg.get("cmd") != "scan":
            return None
        # Extract data from msg.data
        if "data" not in msg or not isinstance(msg["data"], Mapping):
            return None
        data = msg["data"]  # type: ignore[assignment]
    elif "data" in payload and isinstance(payload["data"], Mapping):
        # Fallback: check for top-level "data" field
        data = payload["data"]  # type: ignore[assignment]
    else:
        # Last resort: treat entire payload as data
        data = payload

    device_id = (
        data.get("device")
        or data.get("id")
        or data.get("device_id")
        or data.get("deviceId")
    )
    if not device_id:
        return None

    ip = data.get("ip") or addr[0]
    model = data.get("model") or data.get("sku") or data.get("type")
    description = data.get("description") or data.get("name")
    capabilities = data.get("capabilities") or data.get("capability") or data.get("features")
    return DiscoveryResult(
        id=str(device_id),
        ip=str(ip),
        model=str(model) if model is not None else None,
        description=str(description) if description is not None else None,
        capabilities=capabilities,
        manual=False,
    )


class DiscoveryService:
    """High-level discovery coordinator."""

    def __init__(self, config: Config, store: DeviceStore) -> None:
        self.config = config
        self.store = store
        self.logger = get_logger("govee.discovery")
        self._protocol: Optional[DiscoveryProtocol] = None
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._socket: Optional[socket.socket] = None

    async def start(self) -> None:
        if self.config.dry_run:
            self.logger.info("Discovery service running in dry-run mode; sockets not opened.")
            return
        loop = asyncio.get_running_loop()
        sock = _create_multicast_socket(
            self.config.discovery_multicast_address,
            self.config.discovery_reply_port,
        )
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: DiscoveryProtocol(self.config, self.store, loop),
            sock=sock,
        )
        self._transport = transport  # type: ignore[assignment]
        self._protocol = protocol  # type: ignore[assignment]
        self._socket = sock
        self.logger.info(
            "Discovery service started",
            extra={
                "multicast": self.config.discovery_multicast_address,
                "probe_port": self.config.discovery_multicast_port,
                "reply_port": self.config.discovery_reply_port,
            },
        )

    async def stop(self) -> None:
        if self._transport:
            self._transport.close()
        if self._socket:
            self._socket.close()
        self._protocol = None
        self._transport = None
        self._socket = None
        self.logger.info("Discovery service stopped")

    async def run_cycle(self) -> None:
        started = time.perf_counter()
        result = "ok"
        try:
            if not self._protocol and not self.config.dry_run:
                await self.start()
            if self.config.dry_run:
                self.logger.debug("Skipping discovery probes in dry-run mode")
                await self.store.mark_stale(self.config.discovery_stale_after)
                result = "dry_run"
                return

            assert self._protocol is not None
            self._protocol.reset_cycle()

            target = (
                self.config.discovery_multicast_address,
                self.config.discovery_multicast_port,
            )
            self._protocol.send_probe(target)
            if self.config.manual_unicast_probes:
                for device_id, ip in await self.store.manual_probe_targets():
                    self.logger.debug(
                        "Sending unicast probe",
                        extra={"device_id": device_id, "ip": ip},
                    )
                    self._protocol.send_probe((ip, self.config.discovery_multicast_port))

            try:
                await asyncio.sleep(self.config.discovery_response_timeout)
            finally:
                await self.store.mark_stale(self.config.discovery_stale_after)
        except Exception:
            result = "error"
            raise
        finally:
            observe_discovery_cycle(result, time.perf_counter() - started)
