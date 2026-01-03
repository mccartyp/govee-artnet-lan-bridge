"""LIFX LAN protocol implementation.

LIFX uses a binary UDP protocol on port 56700 with HSBK color model.
Protocol documentation: https://lan.developer.lifx.com/
"""

from __future__ import annotations

import colorsys
import struct
from typing import Any, Mapping, Optional

from .base import ProtocolHandler
from ..capabilities import CapabilityProvider, DeviceReportedCapabilityProvider


class LifxProtocolHandler(ProtocolHandler):
    """Protocol handler for LIFX LAN devices.

    LIFX Protocol Details:
    - Port: 56700 (UDP)
    - Binary packet format (little-endian)
    - 36-byte header + variable payload
    - Color model: HSBK (Hue, Saturation, Brightness, Kelvin)
    - Discovery: UDP broadcast GetService message
    """

    # LIFX Message Types
    MSG_GET_SERVICE = 2
    MSG_STATE_SERVICE = 3
    MSG_GET_VERSION = 32
    MSG_STATE_VERSION = 33
    MSG_GET_POWER = 20
    MSG_SET_POWER = 21
    MSG_STATE_POWER = 22
    MSG_GET_LABEL = 23
    MSG_STATE_LABEL = 25
    MSG_GET = 101           # Light::Get
    MSG_SET_COLOR = 102     # Light::SetColor
    MSG_STATE = 107         # Light::State
    MSG_GET_LIGHT_POWER = 116
    MSG_SET_LIGHT_POWER = 117
    MSG_STATE_LIGHT_POWER = 118

    # Protocol constants
    PROTOCOL_VERSION = 1024
    DEFAULT_SOURCE = 0x4C494658  # "LIFX" in hex

    def __init__(self):
        self._sequence = 0
        self._source = self.DEFAULT_SOURCE

    @property
    def protocol_name(self) -> str:
        """Return the protocol name identifier."""
        return "lifx"

    def get_default_port(self) -> int:
        """LIFX devices listen on UDP port 56700."""
        return 56700

    def get_default_transport(self) -> str:
        """LIFX uses UDP protocol."""
        return "udp"

    def wrap_command(self, payload: Mapping[str, Any]) -> bytes:
        """Convert abstract device command to LIFX binary packet.

        Args:
            payload: Device command dict (e.g., {"color": {"r": 255, "g": 0, "b": 0}})
                    Note: target_mac should be provided for unicast commands

        Returns:
            Complete LIFX packet as bytes

        Raises:
            ValueError: If command cannot be encoded
        """
        # Extract target MAC (6 bytes)
        # In production, this comes from device registry via devices.py
        target_mac = payload.get("_target_mac")  # Internal field from sender

        # Determine command type
        has_color = "color" in payload
        has_brightness = "brightness" in payload
        has_power = "turn" in payload
        has_kelvin = "colorTemperature" in payload or "color_temp" in payload
        duration_ms = payload.get("duration", 0)

        # Handle power commands
        if has_power and not has_color and not has_brightness:
            power_on = payload["turn"] == "on"
            if duration_ms > 0:
                return self._build_set_light_power(target_mac, power_on, duration_ms)
            else:
                return self._build_set_power(target_mac, power_on)

        # Handle color/brightness commands
        if has_color or has_brightness or has_kelvin:
            return self._build_set_color_from_payload(target_mac, payload)

        raise ValueError(f"Unsupported LIFX command payload: {payload}")

    def supports_polling(self) -> bool:
        """LIFX supports polling via Light::Get message."""
        return True

    def build_poll_request(self) -> bytes:
        """Build LIFX Light::Get poll request.

        Returns:
            Binary packet requesting light state.
            Note: Caller must set target MAC in header.
        """
        # Build header for Light::Get (type 101)
        # Use tagged=False for unicast polling (caller sets target MAC)
        header = self._build_header(
            msg_type=self.MSG_GET,
            target_mac=None,  # Will be set by poller
            tagged=False,
            res_required=False,  # State response is implicit
            ack_required=False,
            sequence=self._next_sequence()
        )
        # Light::Get has no payload
        return header

    def parse_poll_response(self, data: bytes) -> Optional[Mapping[str, Any]]:
        """Parse LIFX Light::State response.

        Args:
            data: Raw UDP packet bytes

        Returns:
            Dict with device state or None if not a valid State message
        """
        if len(data) < 36:
            return None

        # Decode header
        header = self._decode_header(data)

        # Check for Light::State response (type 107)
        if header["type"] != self.MSG_STATE:
            return None

        # Parse Light::State payload
        return self._parse_light_state(header["payload"])

    # ===== Internal Helper Methods =====

    def _next_sequence(self) -> int:
        """Get next sequence number and increment."""
        seq = self._sequence
        self._sequence = (self._sequence + 1) % 256
        return seq

    def _build_header(
        self,
        msg_type: int,
        target_mac: Optional[bytes] = None,
        tagged: bool = False,
        res_required: bool = False,
        ack_required: bool = False,
        sequence: Optional[int] = None
    ) -> bytes:
        """Build LIFX 36-byte packet header.

        Args:
            msg_type: Message type ID (uint16)
            target_mac: 6-byte MAC address or None for broadcast
            tagged: True for broadcast, False for unicast
            res_required: Request state response
            ack_required: Request acknowledgment
            sequence: Sequence number (0-255), auto-increments if None

        Returns:
            36-byte header as bytes
        """
        if sequence is None:
            sequence = self._next_sequence()

        # Calculate payload size based on message type
        payload_size = self._get_payload_size(msg_type)
        size = 36 + payload_size

        # Frame: protocol (12 bits) | addressable (1) | tagged (1) | origin (2)
        protocol_flags = self.PROTOCOL_VERSION  # 1024
        protocol_flags |= (1 << 12)  # addressable = 1
        if tagged:
            protocol_flags |= (1 << 13)  # tagged bit
        # origin = 0 (bits 14-15)

        # Target: 8 bytes (6-byte MAC + 2 zero bytes, or all zeros for broadcast)
        if target_mac is None or tagged:
            target = b"\x00" * 8
        else:
            if len(target_mac) != 6:
                raise ValueError(f"MAC address must be 6 bytes, got {len(target_mac)}")
            target = target_mac + b"\x00\x00"

        # Reserved fields
        reserved_6 = b"\x00" * 6
        reserved_8 = b"\x00" * 8
        reserved_2 = b"\x00" * 2

        # Flags byte: res_required (bit 0) | ack_required (bit 1)
        flags = 0
        if res_required:
            flags |= 0x01
        if ack_required:
            flags |= 0x02

        # Pack header (little-endian)
        header = struct.pack(
            "<HHI8s6sBB8sH2s",
            size,              # uint16 - total size
            protocol_flags,    # uint16 - protocol + flags
            self._source,      # uint32 - source identifier
            target,            # 8 bytes - target MAC
            reserved_6,        # 6 bytes - reserved
            flags,             # uint8 - response flags
            sequence,          # uint8 - sequence number
            reserved_8,        # 8 bytes - reserved
            msg_type,          # uint16 - message type
            reserved_2         # 2 bytes - reserved
        )

        return header

    def _decode_header(self, data: bytes) -> dict[str, Any]:
        """Decode LIFX packet header from bytes.

        Args:
            data: Raw packet bytes (minimum 36 bytes)

        Returns:
            Dict with header fields and payload
        """
        if len(data) < 36:
            raise ValueError(f"Packet too short: {len(data)} bytes")

        # Unpack header
        (size, protocol_flags, source, target, reserved_6,
         flags, sequence, reserved_8, msg_type, reserved_2) = struct.unpack(
            "<HHI8s6sBB8sH2s", data[:36]
        )

        # Extract bitfields
        protocol = protocol_flags & 0xFFF
        addressable = (protocol_flags >> 12) & 0x1
        tagged = (protocol_flags >> 13) & 0x1
        origin = (protocol_flags >> 14) & 0x3

        res_required = (flags >> 0) & 0x1
        ack_required = (flags >> 1) & 0x1

        # Extract MAC address (first 6 bytes of target)
        mac = target[:6]

        return {
            "size": size,
            "protocol": protocol,
            "addressable": addressable,
            "tagged": tagged,
            "origin": origin,
            "source": source,
            "target": mac,
            "res_required": res_required,
            "ack_required": ack_required,
            "sequence": sequence,
            "type": msg_type,
            "payload": data[36:size] if size <= len(data) else data[36:]
        }

    def decode_header(self, data: bytes) -> dict[str, Any]:
        """Public helper to decode a LIFX packet header."""
        return self._decode_header(data)

    def _get_payload_size(self, msg_type: int) -> int:
        """Get expected payload size for message type."""
        payload_sizes = {
            self.MSG_GET_SERVICE: 0,
            self.MSG_STATE_SERVICE: 5,
            self.MSG_GET_VERSION: 0,
            self.MSG_STATE_VERSION: 12,
            self.MSG_GET_POWER: 0,
            self.MSG_SET_POWER: 2,
            self.MSG_STATE_POWER: 2,
            self.MSG_GET_LABEL: 0,
            self.MSG_STATE_LABEL: 32,
            self.MSG_GET: 0,
            self.MSG_SET_COLOR: 13,
            self.MSG_STATE: 52,
            self.MSG_GET_LIGHT_POWER: 0,
            self.MSG_SET_LIGHT_POWER: 6,
            self.MSG_STATE_LIGHT_POWER: 2,
        }
        return payload_sizes.get(msg_type, 0)

    # ===== Message Builders =====

    def _build_set_power(self, target_mac: Optional[bytes], power_on: bool) -> bytes:
        """Build SetPower message (instant, no duration).

        Args:
            target_mac: 6-byte device MAC or None for broadcast
            power_on: True for on, False for off

        Returns:
            Complete LIFX packet
        """
        header = self._build_header(
            msg_type=self.MSG_SET_POWER,
            target_mac=target_mac,
            tagged=target_mac is None,
            ack_required=True
        )

        level = 65535 if power_on else 0
        payload = struct.pack("<H", level)

        return header + payload

    def _build_set_light_power(
        self,
        target_mac: Optional[bytes],
        power_on: bool,
        duration_ms: int
    ) -> bytes:
        """Build SetLightPower message with duration.

        Args:
            target_mac: 6-byte device MAC or None for broadcast
            power_on: True for on, False for off
            duration_ms: Transition duration in milliseconds

        Returns:
            Complete LIFX packet
        """
        header = self._build_header(
            msg_type=self.MSG_SET_LIGHT_POWER,
            target_mac=target_mac,
            tagged=target_mac is None,
            ack_required=True
        )

        level = 65535 if power_on else 0
        payload = struct.pack("<HI", level, duration_ms)

        return header + payload

    def _build_set_color(
        self,
        target_mac: Optional[bytes],
        hue: int,
        sat: int,
        bri: int,
        kelvin: int,
        duration_ms: int = 0
    ) -> bytes:
        """Build SetColor message with HSBK values.

        Args:
            target_mac: 6-byte device MAC or None for broadcast
            hue: Hue (0-65535)
            sat: Saturation (0-65535)
            bri: Brightness (0-65535)
            kelvin: Color temperature (2500-9000)
            duration_ms: Transition duration in milliseconds

        Returns:
            Complete LIFX packet
        """
        header = self._build_header(
            msg_type=self.MSG_SET_COLOR,
            target_mac=target_mac,
            tagged=target_mac is None,
            ack_required=True
        )

        # SetColor payload: 13 bytes
        # reserved (1) + hue (2) + sat (2) + bri (2) + kelvin (2) + duration (4)
        payload = struct.pack(
            "<BHHHHI",      # B=1, H=2, H=2, H=2, H=2, I=4 = 13 bytes
            0,              # reserved byte
            hue,
            sat,
            bri,
            kelvin,
            duration_ms
        )

        return header + payload

    def _build_set_color_from_payload(
        self,
        target_mac: Optional[bytes],
        payload: Mapping[str, Any]
    ) -> bytes:
        """Build SetColor packet from abstract payload.

        Args:
            target_mac: 6-byte device MAC or None for broadcast
            payload: Command dict with color/brightness/kelvin fields

        Returns:
            Complete LIFX packet
        """
        # Extract RGB if present
        if "color" in payload:
            color = payload["color"]
            r = color.get("r", 0)
            g = color.get("g", 0)
            b = color.get("b", 0)
        else:
            # Default to white if no color specified
            r = g = b = 255

        # Extract or default kelvin
        kelvin = payload.get("colorTemperature", payload.get("color_temp", 3500))
        # Clamp kelvin to LIFX range
        kelvin = max(2500, min(9000, kelvin))

        # Convert RGB to HSBK
        hue, sat, bri, _ = self._rgb_to_hsbk(r, g, b, kelvin)

        # Override brightness if explicitly specified
        if "brightness" in payload:
            # Brightness in payload is 0-255, convert to 0-65535
            bri = int((payload["brightness"] / 255.0) * 65535)

        # Get transition duration
        duration_ms = payload.get("duration", 0)

        return self._build_set_color(target_mac, hue, sat, bri, kelvin, duration_ms)

    # ===== Message Parsers =====

    def build_get_version_request(self, target_mac: bytes) -> bytes:
        """Build Device::GetVersion request for a specific device."""
        if len(target_mac) != 6:
            raise ValueError(f"MAC address must be 6 bytes, got {len(target_mac)}")
        return self._build_header(
            msg_type=self.MSG_GET_VERSION,
            target_mac=target_mac,
            tagged=False,
            res_required=True
        )

    def build_get_label_request(self, target_mac: bytes) -> bytes:
        """Build Device::GetLabel request for a specific device."""
        if len(target_mac) != 6:
            raise ValueError(f"MAC address must be 6 bytes, got {len(target_mac)}")
        return self._build_header(
            msg_type=self.MSG_GET_LABEL,
            target_mac=target_mac,
            tagged=False,
            res_required=True,
        )

    def parse_state_service(self, header: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """Parse a decoded StateService header into a discovery payload."""
        if header.get("type") != self.MSG_STATE_SERVICE:
            return None

        payload = header.get("payload", b"")
        if len(payload) < 5:
            return None

        service, port = struct.unpack("<BI", payload[:5])
        mac: bytes = header.get("target", b"")
        mac_str = ":".join(f"{b:02X}" for b in mac)

        return {
            "mac": mac,
            "mac_str": mac_str,
            "service": service,
            "port": port,
            "protocol": "lifx",
        }

    def _parse_light_state(self, payload: bytes) -> dict[str, Any]:
        """Parse Light::State response payload.

        Args:
            payload: 52-byte State payload

        Returns:
            Dict with device state
        """
        if len(payload) < 52:
            raise ValueError(f"Invalid State payload size: {len(payload)}")

        # Unpack State payload (52 bytes)
        (hue, sat, bri, kelvin, reserved1, power,
         label_bytes, reserved2) = struct.unpack("<HHHHHH32sQ", payload[:52])

        # Decode label (null-terminated UTF-8)
        try:
            label = label_bytes.split(b"\x00", 1)[0].decode("utf-8")
        except UnicodeDecodeError:
            label = ""

        # Convert HSBK to RGB for compatibility
        r, g, b = self._hsbk_to_rgb(hue, sat, bri)

        return {
            "hue": hue,
            "saturation": sat,
            "brightness": bri,
            "kelvin": kelvin,
            "power": power == 65535,
            "label": label,
            # Include RGB conversion for convenience
            "color": {"r": r, "g": g, "b": b},
            # Normalize brightness to 0-255 range
            "brightness_normalized": int((bri / 65535.0) * 255)
        }

    # ===== Color Conversion =====

    def _rgb_to_hsbk(self, r: int, g: int, b: int, kelvin: int = 3500) -> tuple[int, int, int, int]:
        """Convert RGB (0-255) to LIFX HSBK format.

        Args:
            r, g, b: RGB values (0-255)
            kelvin: Color temperature (2500-9000), default 3500

        Returns:
            Tuple of (hue, saturation, brightness, kelvin) as uint16 values
        """
        # Normalize RGB to 0.0-1.0
        r_norm = r / 255.0
        g_norm = g / 255.0
        b_norm = b / 255.0

        # Convert to HSV (Python's colorsys uses HSV, equivalent to HSB)
        h, s, v = colorsys.rgb_to_hsv(r_norm, g_norm, b_norm)

        # Convert to LIFX encoding (0-65535)
        hue_encoded = int(h * 65535)
        sat_encoded = int(s * 65535)
        bri_encoded = int(v * 65535)

        # Clamp kelvin to valid range
        kelvin = max(2500, min(9000, kelvin))

        return (hue_encoded, sat_encoded, bri_encoded, kelvin)

    def _hsbk_to_rgb(self, hue: int, sat: int, bri: int) -> tuple[int, int, int]:
        """Convert LIFX HSBK to RGB (0-255).

        Note: This ignores kelvin temperature for simplicity.

        Args:
            hue: Hue (0-65535)
            sat: Saturation (0-65535)
            bri: Brightness (0-65535)

        Returns:
            Tuple of (r, g, b) values (0-255)
        """
        # Normalize from uint16 to 0.0-1.0
        h = hue / 65535.0
        s = sat / 65535.0
        v = bri / 65535.0

        # Convert HSV to RGB
        r, g, b = colorsys.hsv_to_rgb(h, s, v)

        # Scale to 0-255
        return (int(r * 255), int(g * 255), int(b * 255))

    # ===== Discovery Support =====

    def build_discovery_request(self) -> bytes:
        """Build LIFX GetService broadcast discovery message.

        Returns:
            Binary packet for broadcast discovery
        """
        # GetService is a broadcast message (tagged=True)
        header = self._build_header(
            msg_type=self.MSG_GET_SERVICE,
            target_mac=None,  # All zeros for broadcast
            tagged=True,
            res_required=False,  # Devices respond automatically
            ack_required=False,
            sequence=0
        )
        # GetService has no payload
        return header

    def parse_discovery_response(self, data: bytes) -> Optional[dict[str, Any]]:
        """Parse LIFX StateService discovery response.

        Args:
            data: Raw UDP packet bytes

        Returns:
            Dict with service info or None if not a StateService message
        """
        if len(data) < 36:
            return None

        # Decode header
        header = self._decode_header(data)

        # Check for StateService response (type 3)
        if header["type"] != self.MSG_STATE_SERVICE:
            return None

        # Parse StateService payload (5 bytes)
        payload = header["payload"]
        if len(payload) < 5:
            return None

        return self.parse_state_service(header)

    def parse_state_version(self, payload: bytes) -> dict[str, Any]:
        """Parse Device::StateVersion payload into normalized structure."""
        if len(payload) < 12:
            raise ValueError(f"Invalid StateVersion payload size: {len(payload)}")

        vendor_id, product_id, version_build = struct.unpack("<III", payload[:12])
        model_number = f"{vendor_id}:{product_id}"
        capabilities = {
            "vendor_id": vendor_id,
            "product_id": product_id,
            "firmware_build": version_build,
        }

        return {
            "vendor_id": vendor_id,
            "product_id": product_id,
            "version_build": version_build,
            "model_number": model_number,
            "capabilities": capabilities,
        }

    def parse_state_label(self, payload: bytes) -> dict[str, Any]:
        """Parse Device::StateLabel payload into normalized structure."""
        if len(payload) < 32:
            raise ValueError(f"Invalid StateLabel payload size: {len(payload)}")

        label_bytes = payload[:32]
        try:
            label = label_bytes.split(b"\x00", 1)[0].decode("utf-8")
        except UnicodeDecodeError:
            label = ""

        return {"label": label}

    def get_capability_provider(self) -> CapabilityProvider:
        """Get device-reported capability provider for LIFX devices.

        LIFX devices report their full state, so no catalog is needed.
        Default capabilities assume all LIFX devices support HSBK color model,
        brightness control, and color temperature (2500-9000K).
        """
        defaults = {
            "color_modes": ["color", "ct"],
            "brightness": True,
            "color": True,
            "color_temperature": True,
            "color_temp_range": [2500, 9000],
            "white": True,
            "color_model": "hsbk"
        }
        return DeviceReportedCapabilityProvider(defaults)
