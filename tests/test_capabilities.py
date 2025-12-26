from govee_artnet_lan_bridge.capabilities import CapabilityCache, validate_command_payload


def test_normalize_capabilities_handles_modes_and_effects() -> None:
    cache = CapabilityCache()
    normalized = cache.normalize(
        "H7000",
        {
            "color_modes": ["COLOR", "color_temp", "effect"],
            "supports_brightness": False,
            "color_temp_range": [1800, 6500],
            "effects": ["Sunset", "Party"],
            "firmware": "1.2.3",
        },
    )

    assert normalized.color_modes == ("color", "ct", "effect")
    assert normalized.supports_brightness is False
    assert normalized.color_temp_range == (1800, 6500)
    assert "party" in normalized.effects
    assert normalized.cache_key == ("H7000", "1.2.3")


def test_validate_command_payload_drops_unsupported_fields() -> None:
    cache = CapabilityCache()
    normalized = cache.normalize(
        "H6100",
        {
            "color_modes": [],
            "supports_brightness": True,
            "effects": [],
        },
    )
    sanitized, warnings = validate_command_payload(
        {"color": {"r": 1}, "brightness": 120, "effect": "Twinkle"}, normalized
    )

    assert "brightness" in sanitized
    assert "color" not in sanitized
    assert warnings  # color/effect dropped warnings are reported


def test_validate_command_payload_clamps_color_temperature() -> None:
    cache = CapabilityCache()
    normalized = cache.normalize(
        "H6101",
        {"color_modes": ["ct"], "color_temp_range": [2000, 4000]},
    )
    sanitized, warnings = validate_command_payload({"color_temp": 5000}, normalized)

    assert sanitized["color_temp"] == 4000
    assert warnings


def test_normalize_capabilities_detects_color_temp_hints() -> None:
    cache = CapabilityCache()
    normalized = cache.normalize(
        "H7001",
        {"ct": (2200, 6000)},
    )

    assert normalized.supports_color_temperature is True
    assert normalized.color_temp_range == (2200, 6000)
    assert "ct" in normalized.color_modes
