"""Protocol handler registry for multi-protocol device support."""

from __future__ import annotations

from typing import Dict

from .base import ProtocolHandler
from .govee import GoveeProtocolHandler
from .lifx import LifxProtocolHandler

# Default protocol for new devices
DEFAULT_PROTOCOL = "govee"

# Registry of available protocol handlers
_PROTOCOL_HANDLERS: Dict[str, ProtocolHandler] = {
    "govee": GoveeProtocolHandler(),
    "lifx": LifxProtocolHandler(),
}


def get_protocol_handler(protocol: str) -> ProtocolHandler:
    """Get the protocol handler for a given protocol name.

    Args:
        protocol: Protocol identifier (e.g., 'govee', 'lifx')

    Returns:
        Protocol handler instance

    Raises:
        ValueError: If protocol is not recognized
    """
    if protocol not in _PROTOCOL_HANDLERS:
        raise ValueError(
            f"Unknown protocol: {protocol}. "
            f"Supported protocols: {', '.join(_PROTOCOL_HANDLERS.keys())}"
        )
    return _PROTOCOL_HANDLERS[protocol]


def get_supported_protocols() -> list[str]:
    """Get list of supported protocol names.

    Returns:
        List of protocol identifiers
    """
    return list(_PROTOCOL_HANDLERS.keys())


__all__ = [
    "ProtocolHandler",
    "GoveeProtocolHandler",
    "LifxProtocolHandler",
    "get_protocol_handler",
    "get_supported_protocols",
    "DEFAULT_PROTOCOL",
]
