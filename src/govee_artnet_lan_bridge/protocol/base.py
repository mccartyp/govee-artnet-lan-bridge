"""Base protocol handler interface for device communication protocols."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class ProtocolHandler(ABC):
    """Abstract base class for device protocol handlers.

    Each protocol handler is responsible for:
    - Converting abstract device state payloads to protocol-specific format
    - Providing protocol-specific default configuration (port, transport)
    - Handling protocol-specific message encoding
    """

    @abstractmethod
    def wrap_command(self, payload: Mapping[str, Any]) -> Any:
        """Convert abstract device state to protocol-specific command format.

        Args:
            payload: Abstract device state (e.g., {"color": {"r": 255, "g": 0, "b": 0}})

        Returns:
            Protocol-specific command format. May be:
            - String (JSON for text protocols like Govee)
            - bytes (binary for protocols like LIFX)
            - dict (for protocols that handle serialization elsewhere)
        """
        pass

    @abstractmethod
    def get_default_port(self) -> int:
        """Get default control port for this protocol.

        Returns:
            Port number for sending control commands to devices.
        """
        pass

    @abstractmethod
    def get_default_transport(self) -> str:
        """Get default transport type for this protocol.

        Returns:
            Transport type: 'udp' or 'tcp'
        """
        pass

    @property
    @abstractmethod
    def protocol_name(self) -> str:
        """Get the protocol identifier.

        Returns:
            Protocol name (e.g., 'govee', 'lifx')
        """
        pass
