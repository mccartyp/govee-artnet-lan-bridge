"""Capability normalization and validation helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Optional, Sequence, Set, Tuple


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    return default


def _fingerprint(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _normalize_string_set(value: Any) -> Set[str]:
    results: Set[str] = set()
    if isinstance(value, str):
        results.add(value.strip().lower())
        return results
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for entry in value:
            if isinstance(entry, str):
                normalized = entry.strip().lower()
                if normalized:
                    results.add(normalized)
    return results


def _normalize_color_modes(capabilities: Any) -> Set[str]:
    modes: Set[str] = set()
    explicit = False
    color_temp_hint = False
    if isinstance(capabilities, Mapping):
        raw_modes = capabilities.get("color_modes")
        if raw_modes is None:
            raw_modes = capabilities.get("colorModes")
        if raw_modes is None:
            raw_modes = capabilities.get("modes")
        if raw_modes is not None:
            explicit = True
            modes |= _normalize_string_set(raw_modes)
        single_mode = capabilities.get("mode")
        if isinstance(single_mode, str):
            explicit = True
            modes.add(single_mode.strip().lower())
        if any(
            key in capabilities
            for key in (
                "ct",
                "color_temp",
                "colorTemperature",
                "color_temp_range",
                "ct_range",
                "colorTempRange",
                "colorTemperatureRange",
            )
        ):
            explicit = True
            color_temp_hint = True

    normalized: Set[str] = set()
    for mode in modes:
        if mode in {"color", "rgb", "rgbw", "white"}:
            normalized.add("color")
        elif mode in {"ct", "cct", "color_temp", "color temperature", "temperature"}:
            normalized.add("ct")
        elif mode in {"scene", "effects", "effect"}:
            normalized.add("effect")
        else:
            normalized.add(mode)

    if color_temp_hint:
        normalized.add("ct")
    if not normalized and modes:
        normalized |= modes
    if not normalized and not explicit:
        normalized.add("color")
    return normalized


def _normalize_color_temp_range(capabilities: Any) -> Optional[Tuple[int, int]]:
    def _coerce_two_ints(value: Any) -> Optional[Tuple[int, int]]:
        if isinstance(value, Mapping):
            low = value.get("min") or value.get("minimum")
            high = value.get("max") or value.get("maximum")
            if low is None or high is None:
                return None
            try:
                return (int(low), int(high))
            except (TypeError, ValueError):
                return None
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
            if len(value) != 2:
                return None
            try:
                return (int(value[0]), int(value[1]))
            except (TypeError, ValueError):
                return None
        return None

    if not isinstance(capabilities, Mapping):
        return None
    for key in (
        "color_temp_range",
        "ct_range",
        "colorTempRange",
        "colorTemperatureRange",
        "color_temp",
        "colorTemperature",
        "ct",
    ):
        if key in capabilities:
            coerced = _coerce_two_ints(capabilities[key])
            if coerced:
                low, high = coerced
                if low > high:
                    low, high = high, low
                return (low, high)
    return None


def _normalize_effects(capabilities: Any) -> Set[str]:
    if not isinstance(capabilities, Mapping):
        return set()
    effects = (
        capabilities.get("effects")
        or capabilities.get("scenes")
        or capabilities.get("scene_modes")
        or capabilities.get("moods")
    )
    return _normalize_string_set(effects)


def _extract_firmware(capabilities: Any) -> Optional[str]:
    if not isinstance(capabilities, Mapping):
        return None
    for key in ("firmware", "fwVersion", "fw_version", "version"):
        if key in capabilities and capabilities[key] is not None:
            return str(capabilities[key])
    return None


@dataclass(frozen=True)
class NormalizedCapabilities:
    """Normalized capability description for validation."""

    model: Optional[str]
    firmware: Optional[str]
    color_modes: Tuple[str, ...]
    supports_brightness: bool
    color_temp_range: Optional[Tuple[int, int]]
    effects: Tuple[str, ...]
    raw: MutableMapping[str, Any]
    fingerprint: str

    @property
    def supports_color(self) -> bool:
        return "color" in self.color_modes

    @property
    def supports_color_temperature(self) -> bool:
        return "ct" in self.color_modes or self.color_temp_range is not None

    @property
    def supports_effects(self) -> bool:
        return bool(self.effects)

    @property
    def cache_key(self) -> Tuple[str, str]:
        return (self.model or "", self.firmware or "")

    @property
    def supported_modes(self) -> Tuple[str, ...]:
        modes = set(self.color_modes)
        if self.supports_brightness:
            modes.add("brightness")
        return tuple(sorted(modes))

    def as_mapping(self) -> MutableMapping[str, Any]:
        data = dict(self.raw)
        data["color_modes"] = list(self.color_modes)
        data["supports_brightness"] = self.supports_brightness
        if self.color_temp_range:
            data["color_temp_range"] = list(self.color_temp_range)
        if self.effects:
            data["effects"] = list(self.effects)
        if self.firmware and "firmware" not in data:
            data["firmware"] = self.firmware
        return data

    def describe_support(self) -> str:
        modes = list(self.color_modes)
        if self.supports_brightness:
            modes.append("brightness")
        summary = ", ".join(sorted(set(modes))) if modes else "none"
        if self.supports_effects:
            summary = f"{summary}; effects ({', '.join(self.effects)})"
        if self.supports_color_temperature and self.color_temp_range:
            summary = f"{summary}; color temp {self.color_temp_range[0]}-{self.color_temp_range[1]}K"
        elif self.supports_color_temperature:
            summary = f"{summary}; color temp supported"
        return summary


def normalize_capabilities(
    model: Optional[str], capabilities: Any
) -> NormalizedCapabilities:
    base: MutableMapping[str, Any] = {}
    if isinstance(capabilities, Mapping):
        base.update(capabilities)

    color_modes = tuple(sorted(_normalize_color_modes(capabilities)))
    supports_brightness = _coerce_bool(
        base.get("supports_brightness", base.get("brightness")), default=True
    )
    color_temp_range = _normalize_color_temp_range(capabilities)
    effects = tuple(sorted(_normalize_effects(capabilities)))
    firmware = _extract_firmware(capabilities)
    fingerprint = _fingerprint(base)
    return NormalizedCapabilities(
        model=model,
        firmware=firmware,
        color_modes=color_modes,
        supports_brightness=supports_brightness,
        color_temp_range=color_temp_range,
        effects=effects,
        raw=base,
        fingerprint=fingerprint,
    )


class CapabilityCache:
    """Cache normalized capabilities keyed by model/firmware."""

    def __init__(self) -> None:
        self._cache: MutableMapping[Tuple[str, str], Tuple[str, NormalizedCapabilities]] = {}

    def normalize(self, model: Optional[str], capabilities: Any) -> NormalizedCapabilities:
        normalized = normalize_capabilities(model, capabilities)
        cached = self._cache.get(normalized.cache_key)
        if cached and cached[0] == normalized.fingerprint:
            return cached[1]
        self._cache[normalized.cache_key] = (normalized.fingerprint, normalized)
        return normalized


def validate_mapping_mode(mode: str, capabilities: NormalizedCapabilities) -> None:
    """Ensure a mapping mode is supported by the device capabilities."""

    if mode == "brightness" and not capabilities.supports_brightness:
        raise ValueError("Device does not support brightness control.")
    if mode in {"rgb", "rgbw", "custom", "color"} and not capabilities.supports_color:
        supported = ", ".join(capabilities.supported_modes) or "none"
        raise ValueError(
            f"Device does not support color mode '{mode}'. Supported modes: {supported}."
        )


def validate_command_payload(
    payload: Any, capabilities: NormalizedCapabilities
) -> tuple[Any, list[str]]:
    """Drop unsupported command fields and surface warnings."""

    if not isinstance(payload, Mapping):
        return payload, []

    sanitized: MutableMapping[str, Any] = {}
    warnings: list[str] = []

    if "brightness" in payload:
        if capabilities.supports_brightness:
            sanitized["brightness"] = payload["brightness"]
        else:
            warnings.append("Brightness is not supported by this device; value dropped.")

    if "color" in payload:
        if capabilities.supports_color:
            sanitized["color"] = payload["color"]
        else:
            warnings.append("Color payload dropped because device does not support color control.")

    ct_key = None
    for key in ("color_temp", "colorTemperature", "ct", "temperature"):
        if key in payload:
            ct_key = key
            break
    if ct_key:
        if capabilities.supports_color_temperature:
            value = payload[ct_key]
            try:
                ct_value = int(value)
            except (TypeError, ValueError):
                warnings.append(f"Color temperature '{value}' is not a number; value dropped.")
            else:
                if capabilities.color_temp_range:
                    low, high = capabilities.color_temp_range
                    clamped = max(low, min(high, ct_value))
                    if clamped != ct_value:
                        warnings.append(
                            f"Color temperature {ct_value}K clamped to supported range {low}-{high}K."
                        )
                    sanitized["color_temp"] = clamped
                else:
                    sanitized["color_temp"] = ct_value
        else:
            warnings.append("Color temperature is not supported; value dropped.")

    effect_key = None
    for key in ("effect", "scene"):
        if key in payload:
            effect_key = key
            break
    if effect_key:
        effect_value = str(payload[effect_key])
        if not capabilities.supports_effects:
            warnings.append("Effects are not supported; effect value dropped.")
        else:
            allowed = {entry.lower() for entry in capabilities.effects}
            if allowed and effect_value.lower() not in allowed:
                warnings.append(
                    f"Effect '{effect_value}' is not supported by this device; value dropped."
                )
            else:
                sanitized["effect"] = effect_value

    if not sanitized:
        supported = capabilities.describe_support()
        raise ValueError(
            f"Payload contains only unsupported fields for this device (supported: {supported})."
        )

    return sanitized, warnings
