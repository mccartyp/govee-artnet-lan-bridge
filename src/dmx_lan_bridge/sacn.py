"""sACN/E1.31 (Streaming ACN) input protocol implementation.

Implements the ANSI E1.31 (sACN) protocol for receiving DMX data over IP networks.
Supports multicast and unicast modes with priority-based source selection.
"""

from __future__ import annotations

import asyncio
import random
import socket
import struct
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from .config import Config
from .events import EVENT_MAPPING_CREATED, EVENT_MAPPING_DELETED, EVENT_MAPPING_UPDATED, SystemEvent
from .logging import get_logger


# sACN/E1.31 Protocol Constants
SACN_PORT = 5568
ACN_PACKET_IDENTIFIER = b"ASC-E1.17\x00\x00\x00"
VECTOR_ROOT_E131_DATA = 0x00000004
VECTOR_E131_DATA_PACKET = 0x00000002
VECTOR_DMP_SET_PROPERTY = 0x02

# E1.31 Specification values
DEFAULT_PRIORITY = 100
MIN_PRIORITY = 0
MAX_PRIORITY = 200
MAX_UNIVERSE = 63999


@dataclass(frozen=True)
class SacnPacket:
    """Parsed sACN/E1.31 packet."""

    universe: int       # Universe number (1-63999)
    sequence: int       # Sequence number (0-255, wraps)
    priority: int       # Priority (0-200, higher wins)
    data: bytes        # DMX data (up to 512 channels)
    source_name: str   # Source name (up to 64 chars)
    cid: bytes         # Component ID (16-byte UUID)
    sync_address: int  # Synchronization universe (0 = no sync)
    preview: bool      # Preview data flag (non-live data)
    stream_terminated: bool  # Stream termination flag


def _parse_sacn_packet(data: bytes, logger=None) -> Optional[SacnPacket]:
    """Parse sACN/E1.31 packet.

    Returns parsed packet or None if invalid/unsupported packet type.

    Packet Structure (E1.31-2018):
    - Root Layer (38 bytes)
    - Framing Layer (77 bytes)
    - DMP Layer (11 bytes + DMX data)
    """
    if len(data) < 126:  # Minimum valid packet size
        return None

    try:
        offset = 0

        # ===== Root Layer =====
        # Preamble Size (2 bytes) - should be 0x0010
        preamble_size = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        if preamble_size != 0x0010:
            return None

        # Post-amble Size (2 bytes) - should be 0x0000
        postamble_size = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        if postamble_size != 0x0000:
            return None

        # ACN Packet Identifier (12 bytes)
        packet_id = data[offset:offset + 12]
        offset += 12
        if packet_id != ACN_PACKET_IDENTIFIER:
            return None

        # Flags and Length (2 bytes)
        flags_length = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        # flags = (flags_length & 0xF000) >> 12
        # root_length = flags_length & 0x0FFF

        # Vector (4 bytes) - should be VECTOR_ROOT_E131_DATA
        vector = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        if vector != VECTOR_ROOT_E131_DATA:
            return None  # Not an E1.31 data packet

        # CID - Component Identifier (16 bytes UUID)
        cid = data[offset:offset + 16]
        offset += 16

        # ===== Framing Layer =====
        # Flags and Length (2 bytes)
        flags_length = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        # framing_flags = (flags_length & 0xF000) >> 12
        # framing_length = flags_length & 0x0FFF

        # Vector (4 bytes) - should be VECTOR_E131_DATA_PACKET
        vector = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        if vector != VECTOR_E131_DATA_PACKET:
            return None

        # Source Name (64 bytes UTF-8)
        source_name_bytes = data[offset:offset + 64]
        offset += 64
        # Null-terminate at first null byte
        null_index = source_name_bytes.find(b'\x00')
        if null_index != -1:
            source_name_bytes = source_name_bytes[:null_index]
        try:
            source_name = source_name_bytes.decode('utf-8', errors='replace')
        except Exception:
            source_name = ""

        # Priority (1 byte)
        priority = struct.unpack_from("B", data, offset)[0]
        offset += 1
        if not (MIN_PRIORITY <= priority <= MAX_PRIORITY):
            priority = DEFAULT_PRIORITY  # Clamp to valid range

        # Synchronization Address (2 bytes)
        sync_address = struct.unpack_from(">H", data, offset)[0]
        offset += 2

        # Sequence Number (1 byte)
        sequence = struct.unpack_from("B", data, offset)[0]
        offset += 1

        # Options (1 byte)
        options = struct.unpack_from("B", data, offset)[0]
        offset += 1
        preview = bool(options & 0x80)  # Bit 7: Preview_Data
        stream_terminated = bool(options & 0x40)  # Bit 6: Stream_Terminated

        # Universe (2 bytes)
        universe = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        if universe == 0 or universe > MAX_UNIVERSE:
            return None  # Invalid universe

        # ===== DMP Layer =====
        # Flags and Length (2 bytes)
        flags_length = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        # dmp_flags = (flags_length & 0xF000) >> 12
        dmp_length = flags_length & 0x0FFF

        # Vector (1 byte) - should be VECTOR_DMP_SET_PROPERTY
        vector = struct.unpack_from("B", data, offset)[0]
        offset += 1
        if vector != VECTOR_DMP_SET_PROPERTY:
            return None

        # Address Type & Data Type (1 byte)
        offset += 1

        # First Property Address (2 bytes) - should be 0
        first_address = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        if first_address != 0:
            return None

        # Address Increment (2 bytes) - should be 1
        address_increment = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        if address_increment != 1:
            return None

        # Property value count (2 bytes)
        property_count = struct.unpack_from(">H", data, offset)[0]
        offset += 2

        # DMX START Code (1 byte) - included in property_count
        # Property count includes START code, so DMX channels = property_count - 1
        dmx_channel_count = property_count - 1
        if dmx_channel_count < 0 or dmx_channel_count > 512:
            return None

        # Extract DMX data (skip START code)
        start_code = struct.unpack_from("B", data, offset)[0]
        offset += 1
        if start_code != 0:
            return None  # Only support NULL START code (0x00)

        # DMX channel data
        dmx_data = data[offset:offset + dmx_channel_count]

        packet = SacnPacket(
            universe=universe,
            sequence=sequence,
            priority=priority,
            data=dmx_data,
            source_name=source_name,
            cid=cid,
            sync_address=sync_address,
            preview=preview,
            stream_terminated=stream_terminated,
        )

        # DEBUG: Log successfully parsed packet
        if logger:
            logger.debug(
                "Parsed E1.31 packet",
                extra={
                    "universe": universe,
                    "sequence": sequence,
                    "priority": priority,
                    "source_name": source_name,
                    "cid": cid.hex(),
                    "sync_address": sync_address,
                    "preview": preview,
                    "stream_terminated": stream_terminated,
                    "dmx_channels": dmx_channel_count,
                    "dmx_data_sample": list(dmx_data[:16]) if len(dmx_data) >= 16 else list(dmx_data),
                },
            )

        return packet

    except (struct.error, IndexError):
        return None


def _create_sacn_socket(config: Config, multicast: bool = True) -> socket.socket:
    """Create UDP socket for sACN reception.

    Args:
        config: Configuration object
        multicast: If True, join multicast groups; if False, bind for unicast

    Returns:
        Configured socket for sACN reception
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Enable receiving multicast
    if multicast:
        # Allow multiple processes to bind to same port
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass  # SO_REUSEPORT not available on all platforms

    # Bind to sACN port
    sock.bind(("0.0.0.0", config.sacn_port if hasattr(config, 'sacn_port') else SACN_PORT))
    sock.setblocking(False)

    return sock


class SacnProtocol(asyncio.DatagramProtocol):
    """Async datagram protocol for receiving sACN packets."""

    def __init__(self, handler: SacnService):
        self.handler = handler
        self.logger = get_logger("sacn.protocol")

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        """Called when sACN packet is received."""
        packet = _parse_sacn_packet(data, logger=self.logger)
        if packet:
            self.handler.handle_packet(packet, addr)

    def error_received(self, exc: Exception) -> None:
        self.logger.error("sACN listener error", exc_info=(type(exc), exc, exc.__traceback__))
        self.handler.notify_error(exc)


class SacnService:
    """sACN/E1.31 input protocol service.

    Receives sACN packets, converts them to unified DMX frames,
    and forwards to the DmxMappingService for device mapping.

    Supports both multicast and unicast reception modes.
    """

    def __init__(
        self,
        config: Config,
        dmx_mapper: Optional[any] = None,  # DmxMappingService
    ) -> None:
        self.config = config
        self.dmx_mapper = dmx_mapper
        self.logger = get_logger("sacn.input")
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional[SacnProtocol] = None
        self._error_event: asyncio.Event = asyncio.Event()
        self._log_sample_rate = max(0.0, min(1.0, config.noisy_log_sample_rate))
        self._source_id = f"sacn-{id(self)}"  # Unique source identifier
        self._multicast_groups: set[str] = set()
        self._multicast_sock: Optional[socket.socket] = None
        self._refresh_lock = asyncio.Lock()
        self._mapping_event_unsubscribes: list[Callable[[], None]] = []

    async def start(self) -> None:
        """Start sACN listener on configured port."""
        self._error_event.clear()

        if self.config.dry_run:
            self.logger.info("sACN input running in dry-run mode; listener not started.")
            return

        if not self.dmx_mapper:
            raise RuntimeError("sACN service requires a DmxMappingService instance")

        loop = asyncio.get_running_loop()

        # Determine multicast vs unicast mode
        multicast_enabled = getattr(self.config, 'sacn_multicast', True)

        sock = _create_sacn_socket(self.config, multicast=multicast_enabled)
        self._multicast_sock = sock

        # Join multicast groups for configured universes if multicast enabled
        if multicast_enabled:
            await self._refresh_multicast_memberships()
            await self._subscribe_mapping_events()

        transport, protocol = await loop.create_datagram_endpoint(
            lambda: SacnProtocol(self),
            sock=sock,
        )

        self._transport = transport  # type: ignore[assignment]
        self._protocol = protocol  # type: ignore[assignment]

        port = getattr(self.config, 'sacn_port', SACN_PORT)
        self.logger.info(
            "sACN input protocol started",
            extra={
                "port": port,
                "mode": "multicast" if multicast_enabled else "unicast",
                "multicast_groups": len(self._multicast_groups),
            },
        )

    async def stop(self) -> None:
        """Stop sACN listener and leave multicast groups."""
        await self._leave_multicast_groups()
        await self._unsubscribe_mapping_events()
        if self._transport:
            self._transport.close()
        self._transport = None
        self._protocol = None
        self._multicast_sock = None
        self._multicast_groups.clear()
        self.logger.info("sACN input protocol stopped")
        self._error_event.set()

    def handle_packet(self, packet: SacnPacket, addr: Tuple[str, int]) -> None:
        """Handle incoming sACN packet by converting to DMX frame.

        Converts sACN-specific packet format to protocol-agnostic DmxFrame
        and forwards to DmxMappingService for processing.

        Args:
            packet: Parsed sACN packet
            addr: Source address (IP, port)
        """
        if random.random() <= self._log_sample_rate:
            self.logger.debug(
                "Received sACN packet",
                extra={
                    "universe": packet.universe,
                    "sequence": packet.sequence,
                    "priority": packet.priority,
                    "source_name": packet.source_name,
                    "preview": packet.preview,
                    "stream_terminated": packet.stream_terminated,
                    "data_length": len(packet.data),
                    "from": addr,
                },
            )

        # Ignore preview data (non-live)
        if packet.preview:
            if random.random() <= self._log_sample_rate:
                self.logger.debug(
                    "Ignoring sACN preview data",
                    extra={"universe": packet.universe, "source_name": packet.source_name}
                )
            return

        # Handle stream termination
        if packet.stream_terminated:
            self.logger.info(
                "sACN stream terminated",
                extra={"universe": packet.universe, "source_name": packet.source_name}
            )
            # Source will timeout naturally via PriorityMerger
            return

        # Ensure packet has exactly 512 DMX channels (pad if needed)
        dmx_data = packet.data
        original_length = len(dmx_data)
        if len(dmx_data) < 512:
            dmx_data = dmx_data + b"\x00" * (512 - len(dmx_data))
        elif len(dmx_data) > 512:
            dmx_data = dmx_data[:512]

        # DEBUG: Log DMX data before mapping
        if random.random() <= self._log_sample_rate:
            self.logger.debug(
                "E1.31 DMX data before mapping",
                extra={
                    "universe": packet.universe,
                    "original_length": original_length,
                    "padded_length": len(dmx_data),
                    "dmx_data_sample": list(dmx_data[:32]),
                    "non_zero_channels": sum(1 for b in dmx_data if b != 0),
                },
            )

        # Convert sACN packet to unified DMX frame
        from .dmx import DmxFrame

        # Create unique source ID based on CID and universe
        cid_hex = packet.cid.hex()[:8]
        source_id = f"sacn-{cid_hex}-u{packet.universe}"

        frame = DmxFrame(
            universe=packet.universe,
            data=dmx_data,
            sequence=packet.sequence,
            source_protocol="sacn",
            priority=packet.priority,  # Use native sACN priority!
            timestamp=time.perf_counter(),
            source_id=source_id,
        )

        # DEBUG: Log DmxFrame before forwarding to mapper
        if random.random() <= self._log_sample_rate:
            self.logger.debug(
                "E1.31 DmxFrame before forwarding to mapper",
                extra={
                    "universe": frame.universe,
                    "sequence": frame.sequence,
                    "priority": frame.priority,
                    "source_protocol": frame.source_protocol,
                    "source_id": frame.source_id,
                    "timestamp": frame.timestamp,
                    "data_length": len(frame.data),
                    "data_sample": list(frame.data[:32]),
                },
            )

        # Forward to DMX mapping service (async call from sync context)
        if self.dmx_mapper:
            asyncio.create_task(self.dmx_mapper.process_dmx_frame(frame))

    @property
    def error_event(self) -> asyncio.Event:
        """Event set when sACN listener encounters an error."""
        return self._error_event

    def notify_error(self, exc: Exception) -> None:
        """Called by protocol when error occurs."""
        self.logger.warning("sACN listener reported error", extra={"error": str(exc)})
        self._error_event.set()

    @staticmethod
    def _get_multicast_address(universe: int) -> str:
        """Get multicast address for a universe.

        E1.31 multicast addressing: 239.255.(universe >> 8).(universe & 0xFF)

        Args:
            universe: Universe number (1-63999)

        Returns:
            Multicast IP address string
        """
        if not (1 <= universe <= MAX_UNIVERSE):
            raise ValueError(f"Universe must be 1-{MAX_UNIVERSE}, got {universe}")

        octet3 = (universe >> 8) & 0xFF
        octet4 = universe & 0xFF
        return f"239.255.{octet3}.{octet4}"

    def _get_event_bus(self):
        """Return the event bus from the DMX mapper if available."""

        return getattr(self.dmx_mapper, "event_bus", None) if self.dmx_mapper else None

    async def _subscribe_mapping_events(self) -> None:
        """Subscribe to mapping change events to refresh multicast memberships."""

        event_bus = self._get_event_bus()
        if not event_bus:
            return

        for event_type in (EVENT_MAPPING_CREATED, EVENT_MAPPING_UPDATED, EVENT_MAPPING_DELETED):
            unsubscribe = await event_bus.subscribe(event_type, self._handle_mapping_event)
            self._mapping_event_unsubscribes.append(unsubscribe)

        self.logger.info("Subscribed to mapping events for sACN multicast refresh")

    async def _unsubscribe_mapping_events(self) -> None:
        """Unsubscribe from mapping change events."""

        for unsubscribe in self._mapping_event_unsubscribes:
            unsubscribe()
        self._mapping_event_unsubscribes.clear()

    async def _handle_mapping_event(self, event: SystemEvent) -> None:
        """Refresh multicast memberships when mappings change."""

        if not getattr(self.config, 'sacn_multicast', True):
            return

        if random.random() <= self._log_sample_rate:
            self.logger.debug(
                "sACN refreshing multicast memberships due to mapping change",
                extra={"event_type": event.event_type, "data": event.data},
            )

        await self._refresh_multicast_memberships(use_config_fallback=False)

    def _desired_multicast_addresses(self, use_config_fallback: bool) -> dict[str, int]:
        """Determine desired multicast addresses keyed by address -> universe."""

        universes: list[int] = []

        if self.dmx_mapper and hasattr(self.dmx_mapper, "get_active_universes"):
            try:
                universes = list(self.dmx_mapper.get_active_universes())
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning(
                    "Failed to read active universes from DMX mapper",
                    extra={"error": str(exc)},
                )

        if not universes and use_config_fallback:
            universes = list(getattr(self.config, 'sacn_universes', (1,)))

        valid_universes = [u for u in universes if 1 <= u <= MAX_UNIVERSE]
        if len(valid_universes) != len(universes):
            self.logger.warning(
                "Ignoring out-of-range universes for sACN multicast",
                extra={"universes": universes},
            )

        return {self._get_multicast_address(universe): universe for universe in valid_universes}

    async def _refresh_multicast_memberships(self, use_config_fallback: bool = True) -> None:
        """Join/leave multicast groups based on active universes."""

        if not getattr(self.config, 'sacn_multicast', True):
            return

        sock = self._multicast_sock or (
            self._transport.get_extra_info('socket') if self._transport else None  # type: ignore[union-attr]
        )
        if not sock:
            self.logger.debug("sACN multicast refresh skipped; socket unavailable")
            return

        async with self._refresh_lock:
            desired = self._desired_multicast_addresses(use_config_fallback)
            desired_addrs = set(desired.keys())
            current_addrs = set(self._multicast_groups)

            to_join = desired_addrs - current_addrs
            to_leave = current_addrs - desired_addrs

            for addr in to_join:
                universe = desired[addr]
                try:
                    mreq = struct.pack("4sL", socket.inet_aton(addr), socket.INADDR_ANY)
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                    self._multicast_groups.add(addr)
                    self.logger.info(
                        "Joined sACN multicast group",
                        extra={"multicast_addr": addr, "universe": universe},
                    )
                except OSError as exc:  # pragma: no cover - platform specific
                    self.logger.warning(
                        "Failed to join sACN multicast group",
                        extra={"multicast_addr": addr, "universe": universe, "error": str(exc)},
                    )

            for addr in to_leave:
                try:
                    mreq = struct.pack("4sL", socket.inet_aton(addr), socket.INADDR_ANY)
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
                    self._multicast_groups.discard(addr)
                    self.logger.info(
                        "Left sACN multicast group",
                        extra={"multicast_addr": addr},
                    )
                except OSError as exc:  # pragma: no cover - platform specific
                    self.logger.warning(
                        "Failed to leave sACN multicast group",
                        extra={"multicast_addr": addr, "error": str(exc)},
                    )

    async def _leave_multicast_groups(self) -> None:
        """Drop all joined multicast groups."""

        if not self._multicast_groups:
            return

        if not getattr(self.config, 'sacn_multicast', True):
            self._multicast_groups.clear()
            return

        sock = self._multicast_sock or (
            self._transport.get_extra_info('socket') if self._transport else None  # type: ignore[union-attr]
        )
        if not sock:
            self._multicast_groups.clear()
            return

        async with self._refresh_lock:
            for addr in list(self._multicast_groups):
                try:
                    mreq = struct.pack("4sL", socket.inet_aton(addr), socket.INADDR_ANY)
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
                    self.logger.info("Left sACN multicast group", extra={"multicast_addr": addr})
                except OSError as exc:  # pragma: no cover - platform specific
                    self.logger.warning(
                        "Failed to leave sACN multicast group",
                        extra={"multicast_addr": addr, "error": str(exc)},
                    )
            self._multicast_groups.clear()
