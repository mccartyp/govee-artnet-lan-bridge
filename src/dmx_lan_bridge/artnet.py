"""ArtNet listener and DMX mapping helpers."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import math
import socket
import struct
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple
from uuid import uuid4
import time
import random

from .config import Config
from .devices import DeviceStateUpdate, DeviceStore, MappingRecord
from .events import EVENT_MAPPING_CREATED, EVENT_MAPPING_DELETED, EVENT_MAPPING_UPDATED, SystemEvent
from .logging import get_logger
from .metrics import observe_artnet_ingest, record_artnet_packet, record_artnet_update

ARTNET_HEADER = b"Art-Net\x00"
OPCODE_ARTDMX = 0x5000
ARTNET_HEADER_LENGTH = 18
MAX_DMX_CHANNELS = 512
DEFAULT_DEBOUNCE_SECONDS = 0.05


def _create_artnet_socket(port: int) -> socket.socket:
    """Create a UDP socket for ArtNet with proper reuse options."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with contextlib.suppress(AttributeError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", port))
    sock.setblocking(False)
    return sock


@dataclass(frozen=True)
class ArtNetPacket:
    """Parsed ArtNet ArtDMX payload."""

    universe: int
    sequence: int
    physical: int
    length: int
    data: bytes


@dataclass(frozen=True)
class DeviceMappingSpec:
    """How to translate a DMX slice into a device payload."""

    mode: str
    order: Tuple[str, ...]
    gamma: float = 1.0
    dimmer: float = 1.0

    @property
    def required_channels(self) -> int:
        return len(self.order)


@dataclass(frozen=True)
class DeviceMapping:
    """Mapping with hydrated capabilities and parsing helpers."""

    record: MappingRecord
    spec: DeviceMappingSpec

    def slice_for(self, dmx_data: bytes) -> Optional[bytes]:
        start_index = max(0, self.record.channel - 1)
        end_index = start_index + self.record.length
        if end_index > len(dmx_data):
            return None
        return dmx_data[start_index:end_index]


def _parse_artnet_packet(data: bytes) -> Optional[ArtNetPacket]:
    if len(data) < ARTNET_HEADER_LENGTH:
        return None
    if not data.startswith(ARTNET_HEADER):
        return None

    opcode = struct.unpack_from("<H", data, 8)[0]
    if opcode != OPCODE_ARTDMX:
        return None

    # ArtDMX framing: [header][opcode_le][prot_vers_hi][prot_vers_lo][seq][phys][universe_le][length_be]
    sequence = data[12]
    physical = data[13]
    universe = struct.unpack_from("<H", data, 14)[0]
    length = struct.unpack_from(">H", data, 16)[0]

    payload = data[ARTNET_HEADER_LENGTH:]
    if length > MAX_DMX_CHANNELS or length != len(payload):
        return None
    return ArtNetPacket(
        universe=universe,
        sequence=sequence,
        physical=physical,
        length=length,
        data=payload,
    )


def _coerce_mode(capabilities: Any, length: int) -> str:
    default_mode = "rgb" if length >= 3 else "brightness"
    if isinstance(capabilities, Mapping):
        mode = str(capabilities.get("mode", default_mode)).lower()
        if mode in {"rgb", "brightness", "custom"}:
            return mode
    return default_mode


def _coerce_order(capabilities: Any, mode: str) -> Tuple[str, ...]:
    def _normalize_entry(entry: str) -> Optional[str]:
        value = entry.strip().lower()
        if value in {"r", "g", "b", "dimmer"}:
            return value
        return None

    default_orders: Dict[str, Tuple[str, ...]] = {
        "rgb": ("r", "g", "b"),
        "brightness": ("dimmer",),
    }
    if isinstance(capabilities, Mapping):
        order_value = capabilities.get("order") or capabilities.get("channel_order")
        if isinstance(order_value, str):
            parsed = tuple(
                entry for entry in (_normalize_entry(ch) for ch in order_value) if entry
            )
            if parsed:
                return parsed
        if isinstance(order_value, Iterable) and not isinstance(order_value, (str, bytes)):
            parsed_list = []
            for item in order_value:
                if not isinstance(item, str):
                    continue
                normalized = _normalize_entry(item)
                if normalized:
                    parsed_list.append(normalized)
            if parsed_list:
                return tuple(parsed_list)
    return default_orders.get(mode, default_orders["brightness"])


def _coerce_float(capabilities: Any, key: str, default: float) -> float:
    if isinstance(capabilities, Mapping) and key in capabilities:
        try:
            return float(capabilities[key])
        except (TypeError, ValueError):
            return default
    return default


def _build_spec(record: MappingRecord) -> DeviceMappingSpec:
    if record.mapping_type == "discrete":
        order = tuple(record.fields) if record.fields else ((record.field,) if record.field else ())
        mode = "discrete"
    else:
        mode = _coerce_mode(record.capabilities, record.length)
        order = tuple(record.fields) if record.fields else _coerce_order(record.capabilities, mode)
    gamma = _coerce_float(record.capabilities, "gamma", 1.0)
    dimmer = _coerce_float(record.capabilities, "dimmer", 1.0)
    dimmer = max(0.0, min(dimmer, 1.0))
    return DeviceMappingSpec(mode=mode, order=order, gamma=max(0.1, gamma), dimmer=dimmer)


def _apply_gamma_dimmer(value: int, gamma: float, dimmer: float) -> int:
    normalized = max(0.0, min(1.0, value / 255.0))
    corrected = math.pow(normalized, gamma)
    scaled = corrected * 255.0 * dimmer
    return int(round(max(0.0, min(255.0, scaled))))


def _payload_from_slice(mapping: DeviceMapping, slice_data: bytes) -> Optional[Mapping[str, Any]]:
    if mapping.record.mapping_type == "discrete":
        return _payload_from_discrete_slice(mapping, slice_data)
    spec = mapping.spec
    if len(slice_data) < spec.required_channels:
        return None

    values: Dict[str, int] = {}
    for idx, channel_name in enumerate(spec.order):
        raw_value = slice_data[idx]
        values[channel_name] = _apply_gamma_dimmer(raw_value, spec.gamma, spec.dimmer)

    if spec.mode == "brightness" or spec.order == ("dimmer",):
        brightness_value = values.get("dimmer", 0)
        # Brightness of 0 sends power off, non-zero sends power on + brightness
        if brightness_value == 0:
            return {"turn": "off"}
        else:
            return {
                "turn": "on",
                "brightness": brightness_value
            }

    color: Dict[str, int] = {}
    for key in ("r", "g", "b"):
        if key in values:
            color[key] = values[key]

    payload: Dict[str, Any] = {}
    if color:
        payload["color"] = color
    if "dimmer" in values:
        payload["brightness"] = values["dimmer"]
    return payload if payload else None


def _payload_from_discrete_slice(
    mapping: DeviceMapping, slice_data: bytes
) -> Optional[Mapping[str, Any]]:
    if not slice_data or not mapping.record.field:
        return None
    raw_value = slice_data[0]
    field = mapping.record.field

    # Handle power as a special case - convert DMX value to on/off
    if field == "power":
        # DMX values >= 128 (50%) turn the device on, < 128 turn it off
        power_state = raw_value >= 128
        return {"turn": "on" if power_state else "off"}

    # Apply gamma/dimmer for other fields
    value = _apply_gamma_dimmer(raw_value, mapping.spec.gamma, mapping.spec.dimmer)
    if field == "dimmer":
        # Dimmer of 0 sends power off, non-zero sends power on + brightness
        if value == 0:
            return {"turn": "off"}
        else:
            # Return both turn and brightness - wrap_govee_command will create the _multiple structure
            return {
                "turn": "on",
                "brightness": value
            }

    # Handle color temperature - scale DMX 0-255 to kelvin range
    if field == "ct":
        # If ArtNet value is 0, don't send color temp command to allow RGB to work
        if raw_value == 0:
            return None
        # Get color temp range from capabilities, default to common range
        color_temp_range = None
        if mapping.record.capabilities:
            # Handle both dict and NormalizedCapabilities object
            if hasattr(mapping.record.capabilities, 'color_temp_range'):
                color_temp_range = mapping.record.capabilities.color_temp_range
            elif isinstance(mapping.record.capabilities, dict):
                color_temp_range = mapping.record.capabilities.get('color_temp_range')
        low, high = color_temp_range or (2000, 9000)
        # Scale 0-255 DMX value to kelvin range
        kelvin = int(round(low + (high - low) * (value / 255.0)))
        return {"color_temp": kelvin}

    return {"color": {field: value}}


def _merge_payloads(target: MutableMapping[str, Any], incoming: Mapping[str, Any]) -> None:
    for key, value in incoming.items():
        if key == "color":
            color = target.setdefault("color", {})
            if isinstance(value, Mapping):
                color.update(value)
            if not color:
                target.pop("color", None)
            continue
        target[key] = value


class UniverseMapping:
    """Container for all mappings within a universe."""

    def __init__(
        self, universe: int, mappings: Sequence[DeviceMapping], log_sample_rate: float = 1.0
    ) -> None:
        self.universe = universe
        self._mappings = list(mappings)
        self.logger = get_logger("artnet.artnet.mapping")
        self._log_sample_rate = max(0.0, min(1.0, log_sample_rate))

    def apply(self, data: bytes, context_id: Optional[str] = None) -> List[DeviceStateUpdate]:
        aggregated: Dict[str, Dict[str, Any]] = {}
        device_order: List[str] = []
        for mapping in self._mappings:
            slice_data = mapping.slice_for(data)
            if slice_data is None:
                if random.random() <= self._log_sample_rate:
                    self.logger.debug(
                        "ArtNet payload too short for mapping",
                        extra={
                            "device_id": mapping.record.device_id,
                            "universe": mapping.record.universe,
                            "channel": mapping.record.channel,
                            "length": mapping.record.length,
                            "payload_length": len(data),
                        },
                    )
                continue
            payload = _payload_from_slice(mapping, slice_data)
            if payload is None:
                continue
            if random.random() <= self._log_sample_rate:
                self.logger.debug(
                    "Mapped ArtNet data to device payload",
                    extra={
                        "device_id": mapping.record.device_id,
                        "universe": mapping.record.universe,
                        "channel": mapping.record.channel,
                        "length": mapping.record.length,
                        "artnet_values": list(slice_data),
                        "payload": payload,
                        "mapping_mode": mapping.spec.mode,
                        "context_id": context_id,
                    },
                )
            device_id = mapping.record.device_id
            if device_id not in aggregated:
                aggregated[device_id] = {}
                device_order.append(device_id)
            _merge_payloads(aggregated[device_id], payload)
        return [
            DeviceStateUpdate(device_id=device_id, payload=aggregated[device_id], context_id=context_id)
            for device_id in device_order
        ]


class ArtNetProtocol(asyncio.DatagramProtocol):
    """Asyncio protocol for receiving ArtNet packets."""

    def __init__(self, handler: "ArtNetService") -> None:
        self.handler = handler
        self.logger = get_logger("artnet.artnet.protocol")

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.logger.info(
            "ArtNet listener started",
            extra={"local": transport.get_extra_info("sockname")},
        )

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        packet = _parse_artnet_packet(data)
        if packet is None:
            return
        self.handler.handle_packet(packet, addr)

    def error_received(self, exc: Exception) -> None:
        self.logger.error("ArtNet listener error", exc_info=(type(exc), exc, exc.__traceback__))
        self.handler.notify_error(exc)


class ArtNetService:
    """ArtNet input protocol listener.

    Receives ArtNet packets, converts them to unified DMX frames,
    and forwards to the DmxMappingService for device mapping.

    This service is now protocol-specific (ArtNet only) while mapping
    logic is handled by the protocol-agnostic DmxMappingService.
    """

    def __init__(
        self,
        config: Config,
        dmx_mapper: Optional[Any] = None,  # DmxMappingService (imported late to avoid circular)
    ) -> None:
        self.config = config
        self.dmx_mapper = dmx_mapper
        self.logger = get_logger("artnet.input")
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional[ArtNetProtocol] = None
        self._error_event: asyncio.Event = asyncio.Event()
        self._log_sample_rate = max(0.0, min(1.0, config.noisy_log_sample_rate))
        self._source_id = f"artnet-{id(self)}"  # Unique source identifier

    async def start(self) -> None:
        """Start ArtNet listener on configured port."""
        self._error_event.clear()

        if self.config.dry_run:
            self.logger.info("ArtNet input running in dry-run mode; listener not started.")
            return

        if not self.dmx_mapper:
            raise RuntimeError("ArtNet service requires a DmxMappingService instance")

        loop = asyncio.get_running_loop()
        sock = _create_artnet_socket(self.config.artnet_port)
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: ArtNetProtocol(self),
            sock=sock,
        )
        self._transport = transport  # type: ignore[assignment]
        self._protocol = protocol  # type: ignore[assignment]
        self.logger.info(
            "ArtNet input protocol started",
            extra={
                "port": self.config.artnet_port,
                "priority": self.config.artnet_priority,
            },
        )

    async def stop(self) -> None:
        """Stop ArtNet listener."""
        if self._transport:
            self._transport.close()
        self._transport = None
        self._protocol = None
        self.logger.info("ArtNet input protocol stopped")
        self._error_event.set()

    def handle_packet(self, packet: ArtNetPacket, addr: Tuple[str, int]) -> None:
        """Handle incoming ArtNet packet by converting to DMX frame.

        Converts ArtNet-specific packet format to protocol-agnostic DmxFrame
        and forwards to DmxMappingService for processing.
        """
        if random.random() <= self._log_sample_rate:
            self.logger.debug(
                "Received ArtNet packet",
                extra={
                    "universe": packet.universe,
                    "sequence": packet.sequence,
                    "data_length": packet.length,
                    "from": addr,
                },
            )

        # Ensure packet has exactly 512 DMX channels
        # ArtNet may send shorter packets, pad with zeros
        dmx_data = packet.data
        if len(dmx_data) < 512:
            dmx_data = dmx_data + b"\x00" * (512 - len(dmx_data))
        elif len(dmx_data) > 512:
            dmx_data = dmx_data[:512]

        # Convert ArtNet packet to unified DMX frame
        from .dmx import DmxFrame

        frame = DmxFrame(
            universe=packet.universe,
            data=dmx_data,
            sequence=packet.sequence,
            source_protocol="artnet",
            priority=self.config.artnet_priority,  # Configurable priority for ArtNet
            timestamp=time.perf_counter(),
            source_id=self._source_id,
        )

        # Forward to DMX mapping service (async call from sync context)
        if self.dmx_mapper:
            asyncio.create_task(self.dmx_mapper.process_dmx_frame(frame))

    @property
    def error_event(self) -> asyncio.Event:
        """Event set when ArtNet listener encounters an error."""
        return self._error_event

    def notify_error(self, exc: Exception) -> None:
        """Called by protocol when error occurs."""
        self.logger.warning("ArtNet listener reported error", extra={"error": str(exc)})
        self._error_event.set()
