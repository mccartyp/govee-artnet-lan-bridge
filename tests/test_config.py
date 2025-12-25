import pytest

from govee_artnet_lan_bridge.config import (
    CONFIG_VERSION,
    MIN_SUPPORTED_CONFIG_VERSION,
    Config,
)


def test_default_config_passes_validation() -> None:
    config = Config()
    assert config.config_version == CONFIG_VERSION


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("discovery_interval", 0.0, "discovery_interval"),
        ("device_max_queue_depth", 0, "device_max_queue_depth"),
        ("rate_limit_per_second", -1.0, "rate_limit_per_second"),
    ],
)
def test_bounds_enforced(field: str, value: object, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        Config(**{field: value})


def test_future_config_version_rejected() -> None:
    with pytest.raises(ValueError, match="newer than supported"):
        Config(config_version=CONFIG_VERSION + 1)


def test_ancient_config_version_rejected() -> None:
    with pytest.raises(ValueError, match="too old"):
        Config(config_version=MIN_SUPPORTED_CONFIG_VERSION - 1)


def test_logging_dict_masks_secrets() -> None:
    config = Config(api_key="secret-key", api_bearer_token="token")
    logged = config.logging_dict()
    assert logged["api_key"] == "***REDACTED***"
    assert logged["api_bearer_token"] == "***REDACTED***"
