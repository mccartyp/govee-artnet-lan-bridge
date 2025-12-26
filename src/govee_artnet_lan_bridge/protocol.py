"""Shared UDP protocol for Govee device communication."""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import contextlib
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from .config import Config
from .logging import get_logger


MessageHandler = Callable[[Mapping[str, Any], Tuple[str, int]], None]


def _create_multicast_socket(address: str, port: int) -> socket.socket:
    """Create a multicast UDP socket bound to the specified port."""
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


class GoveeProtocol(asyncio.DatagramProtocol):
    """Central protocol dispatcher for all Govee UDP responses on port 4002."""

    def __init__(self, config: Config, loop: asyncio.AbstractEventLoop) -> None:
        self.config = config
        self.loop = loop
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.logger = get_logger("govee.protocol")
        self._handlers: Dict[str, MessageHandler] = {}
        self._default_handler: Optional[MessageHandler] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self.logger.info(
            "Govee protocol ready",
            extra={"local": transport.get_extra_info("sockname")},
        )

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            self.logger.error(
                "Govee protocol transport error",
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        self.logger.info("Govee protocol transport closed")
        self.transport = None

    def register_handler(self, cmd: str, handler: MessageHandler) -> None:
        """Register a handler for a specific message cmd type."""
        self._handlers[cmd] = handler
        self.logger.debug(f"Registered handler for cmd: {cmd}")

    def register_default_handler(self, handler: MessageHandler) -> None:
        """Register a fallback handler for unrecognized message types."""
        self._default_handler = handler
        self.logger.debug("Registered default handler")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            message = data.decode("utf-8")
        except UnicodeDecodeError:
            self.logger.debug("Ignoring non-UTF8 message", extra={"from": addr})
            return

        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self.logger.debug("Ignoring non-JSON message", extra={"from": addr})
            return

        if not isinstance(payload, Mapping):
            self.logger.debug("Ignoring non-dict payload", extra={"from": addr})
            return

        # Extract cmd from msg wrapper
        cmd = None
        if "msg" in payload and isinstance(payload["msg"], Mapping):
            cmd = payload["msg"].get("cmd")

        self.logger.debug(
            "Received message",
            extra={"from": addr, "cmd": cmd, "payload": payload},
        )

        # Dispatch to registered handler
        if cmd and cmd in self._handlers:
            try:
                self._handlers[cmd](payload, addr)
            except Exception as exc:
                self.logger.exception(
                    "Handler error",
                    extra={"cmd": cmd, "from": addr},
                )
        elif self._default_handler:
            try:
                self._default_handler(payload, addr)
            except Exception as exc:
                self.logger.exception(
                    "Default handler error",
                    extra={"from": addr},
                )
        else:
            self.logger.debug(
                "No handler for message",
                extra={"cmd": cmd, "from": addr},
            )

    def send_to(self, data: bytes, target: Tuple[str, int]) -> None:
        """Send data to a target address."""
        if not self.transport:
            self.logger.warning(
                "Cannot send; transport not ready", extra={"target": target}
            )
            return
        try:
            self.transport.sendto(data, target)
        except OSError as exc:
            self.logger.error(
                "Failed to send message",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"target": target},
            )


class GoveeProtocolService:
    """Service managing the shared Govee UDP protocol."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger("govee.protocol.service")
        self._protocol: Optional[GoveeProtocol] = None
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._socket: Optional[socket.socket] = None

    async def start(self) -> None:
        """Start the shared UDP listener on port 4002."""
        if self.config.dry_run:
            self.logger.info("Protocol service running in dry-run mode; socket not opened.")
            return

        loop = asyncio.get_running_loop()
        sock = _create_multicast_socket(
            self.config.discovery_multicast_address,
            self.config.discovery_reply_port,
        )
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: GoveeProtocol(self.config, loop),
            sock=sock,
        )
        self._transport = transport  # type: ignore[assignment]
        self._protocol = protocol  # type: ignore[assignment]
        self._socket = sock
        self.logger.info(
            "Protocol service started",
            extra={
                "multicast": self.config.discovery_multicast_address,
                "port": self.config.discovery_reply_port,
            },
        )

    async def stop(self) -> None:
        """Stop the shared UDP listener."""
        if self._transport:
            self._transport.close()
        if self._socket:
            self._socket.close()
        self._protocol = None
        self._transport = None
        self._socket = None
        self.logger.info("Protocol service stopped")

    @property
    def protocol(self) -> Optional[GoveeProtocol]:
        """Get the protocol instance for handler registration."""
        return self._protocol
