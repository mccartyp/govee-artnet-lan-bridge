"""Govee LAN protocol handler."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional, Tuple

from .base import ProtocolHandler
from ..capabilities import CapabilityProvider, CatalogCapabilityProvider


class GoveeProtocolHandler(ProtocolHandler):
    """Protocol handler for Govee LAN devices.

    Govee uses a JSON-based UDP protocol with specific command types:
    - "turn" for power on/off
    - "brightness" for brightness control
    - "colorwc" for color and color temperature
    """

    @property
    def protocol_name(self) -> str:
        return "govee"

    def get_default_port(self) -> int:
        """Govee devices listen on port 4003 for control commands."""
        return 4003

    def get_default_transport(self) -> str:
        """Govee uses UDP for device control."""
        return "udp"

    def wrap_command(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Wrap a device state payload in the Govee LAN API message format.

        Uses specific Govee command types based on payload contents:
        - "brightness" cmd for brightness-only changes
        - "colorwc" cmd for color/color_temp changes
        - "turn" cmd for power on/off changes
        - For combined color+brightness, returns multiple commands via "_multiple" key

        Transforms:
            {"color": {"r": 154, "g": 0, "b": 0}}
        Into:
            {"msg": {"cmd": "colorwc", "data": {"color": {"r": 154, "g": 0, "b": 0}}}}

        For combined updates:
            {"color": {"r": 154, "g": 0, "b": 0}, "brightness": 200}
        Into:
            {"_multiple": [
                {"msg": {"cmd": "colorwc", "data": {"color": {"r": 154, "g": 0, "b": 0}}}},
                {"msg": {"cmd": "brightness", "data": {"value": 200}}}
            ]}

        Args:
            payload: Abstract device state payload

        Returns:
            Govee-formatted command dict (may contain "_multiple" key for batched commands)
        """
        # If already wrapped, return as-is
        if "msg" in payload:
            return payload

        # Determine the appropriate command type based on payload contents
        has_color = "color" in payload
        has_color_temp = "color_temp" in payload or "colorTemInKelvin" in payload
        has_brightness = "brightness" in payload
        has_turn = "turn" in payload

        # Handle power/turn commands
        if has_turn:
            # Convert "on"/"off" to 1/0 as required by Govee API
            turn_value = 1 if payload["turn"] == "on" else 0
            turn_cmd = {
                "msg": {
                    "cmd": "turn",
                    "data": {"value": turn_value}
                }
            }

            # When turning off, only send the turn command
            if turn_value == 0:
                return turn_cmd

            # When turning on, send additional commands alongside turn
            additional_cmds = []

            # Add color/colorwc command if present
            if has_color or has_color_temp:
                data: Dict[str, Any] = {}
                if has_color:
                    data["color"] = payload["color"]
                if "color_temp" in payload:
                    data["colorTemInKelvin"] = payload["color_temp"]
                elif "colorTemInKelvin" in payload:
                    data["colorTemInKelvin"] = payload["colorTemInKelvin"]
                additional_cmds.append({
                    "msg": {
                        "cmd": "colorwc",
                        "data": data
                    }
                })

            # Add brightness command if present
            if has_brightness:
                additional_cmds.append({
                    "msg": {
                        "cmd": "brightness",
                        "data": {"value": payload["brightness"]}
                    }
                })

            # Return turn with additional commands if any
            if additional_cmds:
                return {"_multiple": [turn_cmd] + additional_cmds}
            return turn_cmd

        # Brightness-only command (no color or color_temp)
        if has_brightness and not has_color and not has_color_temp:
            return {
                "msg": {
                    "cmd": "brightness",
                    "data": {"value": payload["brightness"]}
                }
            }

        # Color/colorwc command
        if has_color or has_color_temp:
            data: Dict[str, Any] = {}
            if has_color:
                data["color"] = payload["color"]
            if "color_temp" in payload:
                data["colorTemInKelvin"] = payload["color_temp"]
            elif "colorTemInKelvin" in payload:
                data["colorTemInKelvin"] = payload["colorTemInKelvin"]

            colorwc_cmd = {
                "msg": {
                    "cmd": "colorwc",
                    "data": data
                }
            }

            # If brightness is also present, send it as a separate command
            if has_brightness:
                brightness_cmd = {
                    "msg": {
                        "cmd": "brightness",
                        "data": {"value": payload["brightness"]}
                    }
                }
                return {"_multiple": [colorwc_cmd, brightness_cmd]}

            return colorwc_cmd

        # Fallback for any other payload (shouldn't normally happen)
        return {
            "msg": {
                "cmd": "devControl",
                "data": dict(payload)
            }
        }

    def supports_polling(self) -> bool:
        """Govee devices support polling via devStatus command."""
        return True

    def build_poll_request(self) -> bytes:
        """Build Govee devStatus poll request.

        Returns:
            JSON-encoded devStatus command as bytes
        """
        poll_command = {"msg": {"cmd": "devStatus", "data": {}}}
        return json.dumps(poll_command, separators=(",", ":")).encode("utf-8")

    def parse_poll_response(self, data: bytes) -> Optional[Mapping[str, Any]]:
        """Parse Govee devStatus response and extract normalized state.

        Args:
            data: Raw JSON response from device

        Returns:
            Normalized state dict or None if parsing fails
        """
        try:
            payload = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

        return self._extract_state(payload)

    def _extract_state(self, payload: Any) -> Optional[Mapping[str, Any]]:
        """Extract and normalize state from Govee poll response.

        Args:
            payload: Decoded JSON response from device

        Returns:
            Normalized state dict with standard keys
        """
        if not isinstance(payload, Mapping):
            return None

        def _coerce_int(value: Any) -> Optional[int]:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        def _coerce_number(value: Any) -> Optional[float]:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def _normalize_power(value: Any) -> Optional[bool]:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(int(value))
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"on", "1", "true"}:
                    return True
                if lowered in {"off", "0", "false"}:
                    return False
            return None

        def _normalize_color(value: Any) -> Optional[Dict[str, int]]:
            if not isinstance(value, Mapping):
                return None
            channels = {}
            for channel in ("r", "g", "b", "w"):
                coerced = _coerce_int(value.get(channel))
                if coerced is not None:
                    channels[channel] = coerced
            return channels or None

        def _pop_first(keys: Tuple[str, ...], source: Dict[str, Any]) -> Any:
            for key in keys:
                if key in source:
                    return source.pop(key)
            return None

        envelope = payload["msg"] if isinstance(payload.get("msg"), Mapping) else payload
        data_block = envelope.get("data") if isinstance(envelope.get("data"), Mapping) else envelope
        if not isinstance(data_block, Mapping):
            return None

        merged: Dict[str, Any] = {}
        merged.update({k: v for k, v in data_block.items() if k not in {"state", "property", "properties"}})

        # Merge nested state/property blocks from devStatus responses
        for key in ("state", "property", "properties"):
            nested = data_block.get(key)
            if isinstance(nested, Mapping):
                merged.update(nested)
            elif isinstance(nested, list):
                for entry in nested:
                    if isinstance(entry, Mapping):
                        merged.update(entry)

        normalized: Dict[str, Any] = {}

        device_id = _pop_first(("device", "device_id", "id"), merged)
        if device_id is not None:
            normalized["device"] = str(device_id)

        model = _pop_first(("model", "model_number", "sku"), merged)
        if model is not None:
            normalized["model"] = str(model)

        firmware = _pop_first(("firmware", "fwVersion", "fw_version", "version"), merged)
        if firmware is not None:
            normalized["firmware"] = str(firmware)

        power = _normalize_power(_pop_first(("power", "powerState", "onOff", "switch"), merged))
        if power is not None:
            normalized["power"] = power

        brightness = _coerce_int(_pop_first(("brightness", "bright", "level"), merged))
        if brightness is not None:
            normalized["brightness"] = brightness

        color_temp = _coerce_int(
            _pop_first(
                (
                    "color_temperature",
                    "colorTemp",
                    "colorTem",
                    "colorTempInKelvin",
                    "colorTemInKelvin",
                    "color_temp",
                    "ct",
                ),
                merged,
            )
        )
        if color_temp is not None:
            normalized["color_temperature"] = color_temp

        temperature = _coerce_number(_pop_first(("temperature", "temp", "tem"), merged))
        if temperature is not None:
            normalized["temperature"] = temperature

        mode = _pop_first(("mode", "workMode", "scene", "sceneId", "sceneNum"), merged)
        if mode is not None:
            normalized["mode"] = mode

        effects = _pop_first(("effects", "lightingEffects", "sceneMode", "scene_modes"), merged)
        if effects is not None:
            normalized["effects"] = effects

        color = _normalize_color(_pop_first(("color", "colors", "rgb"), merged))
        if color is not None:
            normalized["color"] = color

        ext = _pop_first(("ext",), merged)
        if isinstance(ext, Mapping):
            normalized["ext"] = ext

        # Preserve any remaining fields
        for key, value in merged.items():
            normalized[key] = value

        return normalized or None

    def get_capability_provider(self) -> CapabilityProvider:
        """Get catalog-based capability provider for Govee devices."""
        return CatalogCapabilityProvider("govee")

    @staticmethod
    def create_devstatus_handler(logger: Any) -> Any:
        """Create a handler for devStatus responses received on the shared protocol port.

        Govee devices send devStatus responses back to port 4002 (the multicast discovery
        port) even when polls are sent from ephemeral ports. This handler catches those
        responses that arrive on the shared protocol dispatcher.

        Args:
            logger: Logger instance to use for logging

        Returns:
            Handler function compatible with MessageHandler signature
        """
        def _handle_devstatus_response(payload: Mapping[str, Any], addr: tuple[str, int]) -> None:
            """Handle devStatus responses received on shared protocol port 4002.

            Note: The current polling implementation uses ephemeral sockets, so most
            responses are handled by _PollProtocol. This handler catches responses
            that Govee devices send back to port 4002 instead of the ephemeral port.

            Args:
                payload: The devStatus message payload
                addr: Source address (ip, port) of the response
            """
            logger.debug(
                "Received devStatus response on shared protocol port",
                extra={
                    "from": addr,
                    "payload_keys": list(payload.keys()) if isinstance(payload, dict) else None,
                },
            )

        return _handle_devstatus_response
