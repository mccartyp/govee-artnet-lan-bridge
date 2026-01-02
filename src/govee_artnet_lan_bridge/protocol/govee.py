"""Govee LAN protocol handler."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping

from .base import ProtocolHandler


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
