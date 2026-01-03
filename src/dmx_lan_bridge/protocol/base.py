"""Base protocol handler interface for device communication protocols."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Mapping, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..capabilities import CapabilityProvider


class ProtocolHandler(ABC):
    """Abstract base class for device protocol handlers.

    Each protocol handler is responsible for:
    - Converting abstract device state payloads to protocol-specific format
    - Providing protocol-specific default configuration (port, transport)
    - Handling protocol-specific message encoding
    - Building and parsing device poll requests/responses (if supported)
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

    def enrich_capabilities(
        self,
        existing_caps: Mapping[str, Any],
        incoming_caps: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Enrich device capabilities with protocol-specific catalog data.

        This method allows protocols to augment device capabilities from
        protocol-specific catalogs, firmware data, or other sources.

        Args:
            existing_caps: Existing capabilities stored in database
            incoming_caps: New/updated capabilities from discovery or updates

        Returns:
            Enriched capabilities dictionary. Default implementation just
            merges incoming over existing. Protocols can override to add
            catalog lookups, feature detection, etc.
        """
        # Default: simple merge (incoming overwrites existing)
        result = dict(existing_caps) if existing_caps else {}
        if incoming_caps:
            result.update(incoming_caps)
        return result

    def supports_polling(self) -> bool:
        """Check if this protocol supports device polling.

        Returns:
            True if polling is supported, False otherwise.
            Defaults to False. Override in subclasses that support polling.
        """
        return False

    def build_poll_request(self) -> bytes:
        """Build a poll request message for querying device state.

        Returns:
            Protocol-specific poll request as bytes to send to device.

        Raises:
            NotImplementedError: If polling is not supported by this protocol.
        """
        raise NotImplementedError(
            f"{self.protocol_name} protocol does not support polling"
        )

    def parse_poll_response(self, data: bytes) -> Optional[Mapping[str, Any]]:
        """Parse a poll response and extract normalized device state.

        Args:
            data: Raw response bytes from device

        Returns:
            Normalized state dict with keys like:
            - "device": device ID
            - "model": model number
            - "power": bool (on/off)
            - "brightness": int (0-255)
            - "color": {"r": int, "g": int, "b": int}
            - "color_temperature": int (kelvin)
            Returns None if response cannot be parsed.

        Raises:
            NotImplementedError: If polling is not supported by this protocol.
        """
        raise NotImplementedError(
            f"{self.protocol_name} protocol does not support polling"
        )

    @abstractmethod
    def get_capability_provider(self) -> "CapabilityProvider":
        """Get the capability provider for this protocol.

        Returns:
            CapabilityProvider instance that provides device capabilities.
            May be catalog-based (for Govee, WiZ) or device-reported (for LIFX).
        """
        pass

    def register_udp_handlers(
        self,
        protocol: Any,
        logger: Any,
        poller: Optional[Any] = None,
        poll_notifier: Optional[Callable[[str, bytes, tuple[str, int], Optional[str]], None]] = None,
    ) -> None:
        """Register protocol-specific UDP message handlers with the protocol's UDP listener.

        This method is called during poller startup to allow protocols to register handlers
        for incoming UDP messages on their protocol-specific port. For example, Govee
        registers handlers for 'devStatus' responses on its port 4002 UDP listener.

        Each protocol has its own UDP listener on its own port:
        - Govee: port 4002
        - LIFX: port 56700
        - etc.

        Args:
            protocol: The protocol-specific UDP protocol instance (e.g., GoveeProtocol)
            logger: Logger instance for the handler to use
            poller: Optional poller interface (e.g., DevicePollerService) for routing shared
                responses back to awaiting polls and recording success.
            poll_notifier: Optional callback to notify the poller when a poll response is received
                on a shared protocol socket. Signature: (device_id, payload_bytes, addr, protocol)

        Note:
            This is optional - protocols that don't need to handle incoming UDP messages
            can use the default implementation which does nothing.
        """
        pass  # Default implementation does nothing
