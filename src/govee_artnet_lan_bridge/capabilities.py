"""Capability normalization and validation helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

from .config import _default_capability_catalog_path


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


def _normalize_model_number(model: Optional[str]) -> Optional[str]:
    if model is None:
        return None
    normalized = str(model).strip()
    return normalized.upper() if normalized else None


def _capabilities_missing(capabilities: Any) -> bool:
    if capabilities is None:
        return True
    if isinstance(capabilities, Mapping):
        if not capabilities:
            return True
        meaningful_keys = {
            "brightness",
            "color",
            "color_temperature",
            "color_modes",
            "color_temp_range",
            "ct",
            "ct_range",
            "mode",
            "modes",
            "effects",
            "scenes",
            "scene_modes",
        }
        return not any(key in capabilities for key in meaningful_keys)
    return False


@dataclass(frozen=True)
class CapabilityCatalogEntry:
    """Single catalog entry describing a device model."""

    model_number: str
    capabilities: Mapping[str, Any]
    metadata: Mapping[str, Any]


class CapabilityCatalog:
    """Capability catalog loaded from JSON data."""

    def __init__(
        self,
        entries: Mapping[str, CapabilityCatalogEntry],
        *,
        schema: Optional[int] = None,
    ) -> None:
        self._entries = dict(entries)
        self.schema = schema

    @classmethod
    def from_path(cls, path: Path) -> "CapabilityCatalog":
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_data(data)

    @classmethod
    def from_data(cls, data: Any) -> "CapabilityCatalog":
        schema = None
        entries_data: Any = data
        if isinstance(data, Mapping):
            schema = data.get("schema") or data.get("version")
            for key in ("devices", "models", "entries", "capabilities"):
                if key in data:
                    entries_data = data[key]
                    break
        if not isinstance(entries_data, Sequence) or isinstance(entries_data, (bytes, bytearray, str)):
            raise ValueError("Capability catalog must contain a list of entries.")

        entries: Dict[str, CapabilityCatalogEntry] = {}
        for raw in entries_data:
            if not isinstance(raw, Mapping):
                raise ValueError("Capability catalog entries must be objects.")
            model_number = raw.get("model_number") or raw.get("modelNumber")
            if not model_number:
                raise ValueError("Catalog entries must include a model_number.")
            capabilities = raw.get("capabilities") or {}
            if not isinstance(capabilities, Mapping):
                raise ValueError("Catalog entry capabilities must be a mapping.")
            metadata: Dict[str, Any] = {}
            if isinstance(raw.get("metadata"), Mapping):
                metadata.update(raw["metadata"])  # type: ignore[index]
            metadata.update(
                {
                    key: value
                    for key, value in raw.items()
                    if key not in {"model_number", "modelNumber", "capabilities", "aliases", "metadata"}
                }
            )
            entry = CapabilityCatalogEntry(
                model_number=str(model_number),
                capabilities=dict(capabilities),
                metadata=metadata,
            )
            normalized_model = _normalize_model_number(model_number)
            if normalized_model:
                entries[normalized_model] = entry
            aliases = raw.get("aliases")
            if isinstance(aliases, Sequence) and not isinstance(aliases, (str, bytes, bytearray)):
                for alias in aliases:
                    normalized_alias = (
                        _normalize_model_number(alias) if isinstance(alias, (str, int, float)) else None
                    )
                    if normalized_alias and normalized_alias not in entries:
                        entries[normalized_alias] = entry
        return cls(entries, schema=schema)

    @classmethod
    def from_embedded(cls) -> "CapabilityCatalog":
        return cls.from_path(_default_capability_catalog_path())

    def lookup(self, model_number: Optional[str]) -> Optional[CapabilityCatalogEntry]:
        normalized = _normalize_model_number(model_number)
        if normalized is None:
            return None
        return self._entries.get(normalized)


@lru_cache(maxsize=1)
def load_embedded_catalog() -> CapabilityCatalog:
    """Load and cache the embedded capability catalog."""

    return CapabilityCatalog.from_embedded()


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
    color_flag = None
    color_temp_flag = None
    if isinstance(capabilities, Mapping):
        if "color" in capabilities or "supports_color" in capabilities:
            explicit = True
            color_flag = _coerce_bool(capabilities.get("color", capabilities.get("supports_color")))
            if color_flag:
                modes.add("color")
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
        if "color_temperature" in capabilities or "supports_color_temperature" in capabilities:
            explicit = True
            color_temp_flag = _coerce_bool(
                capabilities.get("color_temperature", capabilities.get("supports_color_temperature"))
            )
            if color_temp_flag:
                color_temp_hint = True
                modes.add("ct")
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
    if color_flag is False and "color" in normalized:
        normalized.remove("color")
    if color_temp_flag is False and "ct" in normalized:
        normalized.remove("ct")
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

    model_number: Optional[str]
    firmware: Optional[str]
    metadata: MutableMapping[str, Any]
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
        return (self.model_number or "", self.firmware or "")

    @property
    def supported_modes(self) -> Tuple[str, ...]:
        modes = set(self.color_modes)
        if self.supports_brightness:
            modes.add("brightness")
        return tuple(sorted(modes))

    def as_mapping(self) -> MutableMapping[str, Any]:
        data = dict(self.raw)
        data["model_number"] = self.model_number
        data.pop("supports_brightness", None)
        data.pop("supports_color", None)
        data.pop("supports_color_temperature", None)
        data["color_modes"] = list(self.color_modes)
        data["brightness"] = self.supports_brightness
        data["color"] = self.supports_color
        data["color_temperature"] = self.supports_color_temperature
        if self.color_temp_range:
            data["color_temp_range"] = list(self.color_temp_range)
        if self.effects:
            data["effects"] = list(self.effects)
        if self.firmware and "firmware" not in data:
            data["firmware"] = self.firmware
        for key, value in self.metadata.items():
            data[key] = value
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
    model_number: Optional[str], capabilities: Any, *, metadata: Optional[Mapping[str, Any]] = None
) -> NormalizedCapabilities:
    base: MutableMapping[str, Any] = {}
    if isinstance(metadata, Mapping):
        base.update(metadata)
    if isinstance(capabilities, Mapping):
        base.update(capabilities)

    color_modes = tuple(sorted(_normalize_color_modes(capabilities)))
    supports_brightness = _coerce_bool(
        base.get("brightness", base.get("supports_brightness")), default=True
    )
    base["brightness"] = supports_brightness
    base.pop("supports_brightness", None)
    base.pop("supports_color", None)
    base.pop("supports_color_temperature", None)
    color_temp_range = _normalize_color_temp_range(capabilities)
    effects = tuple(sorted(_normalize_effects(capabilities)))
    supports_color = "color" in color_modes
    supports_color_temperature = "ct" in color_modes or color_temp_range is not None
    base["color"] = supports_color
    base["color_temperature"] = supports_color_temperature
    normalized_metadata = _normalize_metadata(base)
    base.update(normalized_metadata)
    firmware = _extract_firmware(capabilities)
    fingerprint = _fingerprint(base)
    return NormalizedCapabilities(
        model_number=model_number,
        firmware=firmware,
        metadata=normalized_metadata,
        color_modes=color_modes,
        supports_brightness=supports_brightness,
        color_temp_range=color_temp_range,
        effects=effects,
        raw=base,
        fingerprint=fingerprint,
    )


class CapabilityCache:
    """Cache normalized capabilities keyed by model/firmware."""

    def __init__(self, catalog: Optional[CapabilityCatalog] = None) -> None:
        self._cache: MutableMapping[Tuple[str, str], Tuple[str, NormalizedCapabilities]] = {}
        self._catalog = catalog

    def has_catalog_entry(self, model: Optional[str]) -> bool:
        if not self._catalog or model is None:
            return False
        return self._catalog.lookup(model) is not None

    def normalize(
        self, model: Optional[str], capabilities: Any, metadata: Optional[Mapping[str, Any]] = None
    ) -> NormalizedCapabilities:
        missing_caps = _capabilities_missing(capabilities)
        catalog_entry = self._catalog.lookup(model) if self._catalog and model else None
        normalized_model = model
        source_capabilities: Any = None
        metadata_source: Dict[str, Any] = {}
        if metadata:
            metadata_source.update(metadata)
        if missing_caps and catalog_entry is not None:
            source_capabilities = catalog_entry.capabilities
            normalized_model = catalog_entry.model_number
            metadata_source.update(catalog_entry.metadata)
        elif not missing_caps:
            source_capabilities = capabilities
            if catalog_entry is not None:
                metadata_source.update(catalog_entry.metadata)
        normalized = normalize_capabilities(
            normalized_model, source_capabilities, metadata=metadata_source or None
        )
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
def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return _coerce_bool(value, default=False)


def _normalize_metadata(data: Mapping[str, Any]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    if "device_type" in data and data.get("device_type") is not None:
        metadata["device_type"] = str(data["device_type"])
    if "length_meters" in data or "lengthMeters" in data:
        metadata["length_meters"] = _coerce_optional_float(
            data.get("length_meters", data.get("lengthMeters"))
        )
    if "led_count" in data or "ledCount" in data:
        metadata["led_count"] = _coerce_optional_int(data.get("led_count", data.get("ledCount")))
    if "led_density_per_meter" in data or "ledDensityPerMeter" in data:
        metadata["led_density_per_meter"] = _coerce_optional_float(
            data.get("led_density_per_meter", data.get("ledDensityPerMeter"))
        )
    if "has_segments" in data or "hasSegments" in data:
        metadata["has_segments"] = _coerce_optional_bool(
            data.get("has_segments", data.get("hasSegments"))
        )
    if "segment_count" in data or "segmentCount" in data:
        metadata["segment_count"] = _coerce_optional_int(
            data.get("segment_count", data.get("segmentCount"))
        )
    return {key: value for key, value in metadata.items() if value is not None}
