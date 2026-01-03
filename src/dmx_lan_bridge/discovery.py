"""Multi-protocol discovery service for smart lighting devices."""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any, Coroutine, Dict, Mapping, Optional, Tuple

from .config import Config
from .devices import DeviceStore, DiscoveryResult
from .logging import get_logger
from .metrics import observe_discovery_cycle, record_discovery_error, record_discovery_response
from .protocol import get_protocol_handler
from .udp_protocol import GoveeProtocol


def _parse_payload(
    payload: Any, addr: Tuple[str, int]
) -> Optional[DiscoveryResult]:
    if not isinstance(payload, Mapping):
        return None

    # Check for "msg" wrapper (standard Govee response format)
    data: Mapping[str, Any]
    if "msg" in payload and isinstance(payload["msg"], Mapping):
        msg = payload["msg"]
        # Verify it's a scan response
        if msg.get("cmd") != "scan":
            return None
        # Extract data from msg.data
        if "data" not in msg or not isinstance(msg["data"], Mapping):
            return None
        data = msg["data"]  # type: ignore[assignment]
    elif "data" in payload and isinstance(payload["data"], Mapping):
        # Fallback: check for top-level "data" field
        data = payload["data"]  # type: ignore[assignment]
    else:
        # Last resort: treat entire payload as data
        data = payload

    device_id = (
        data.get("device")
        or data.get("id")
        or data.get("device_id")
        or data.get("deviceId")
    )
    if not device_id:
        return None

    ip = data.get("ip") or addr[0]
    model_number = data.get("model") or data.get("sku") or data.get("type")
    device_type = data.get("device_type") or data.get("deviceType")
    length_meters = data.get("length_meters") or data.get("lengthMeters")
    led_count = data.get("led_count") or data.get("ledCount")
    led_density_per_meter = data.get("led_density_per_meter") or data.get("ledDensityPerMeter")
    has_zones = data.get("has_zones") or data.get("hasZones")
    zone_count = data.get("zone_count") or data.get("zoneCount")
    description = data.get("description") or data.get("name")
    capabilities = data.get("capabilities") or data.get("capability") or data.get("features")
    color_temp_hints: Dict[str, Any] = {}
    for key in (
        "ct",
        "color_temp",
        "colorTemperature",
        "color_temp_range",
        "ct_range",
        "colorTempRange",
        "colorTemperatureRange",
    ):
        if key in data:
            color_temp_hints[key] = data[key]
    if color_temp_hints:
        if isinstance(capabilities, Mapping):
            merged = dict(capabilities)
            for key, value in color_temp_hints.items():
                merged.setdefault(key, value)
            capabilities = merged
        else:
            capabilities = dict(color_temp_hints)
    return DiscoveryResult(
        id=str(device_id),
        ip=str(ip),
        protocol="govee",  # Govee discovery - explicitly mark as govee protocol
        model_number=str(model_number) if model_number is not None else None,
        device_type=str(device_type) if device_type is not None else None,
        length_meters=length_meters,
        led_count=led_count,
        led_density_per_meter=led_density_per_meter,
        has_zones=has_zones,
        zone_count=zone_count,
        description=str(description) if description is not None else None,
        capabilities=capabilities,
        manual=False,
    )


class DiscoveryProtocol(GoveeProtocol):
    """Datagram protocol with discovery handling wired in for backward compatibility."""

    def __init__(self, config: Config, store: DeviceStore, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(config, loop)
        self._discovery = DiscoveryService(config, store, protocol=self)
        self.register_handler("scan", self._discovery._handle_scan_response)
        self.register_default_handler(self._discovery._handle_scan_response)


class DiscoveryService:
    """High-level discovery coordinator using shared protocol (multi-protocol support)."""

    def __init__(self, config: Config, store: DeviceStore, protocol: Optional[GoveeProtocol] = None) -> None:
        self.config = config
        self.store = store
        self.protocol = protocol
        self.logger = get_logger("devices.discovery")
        self._seen: Dict[str, str] = {}
        self._lifx_version_requests: set[tuple[str, str]] = set()
        self._lifx_host_firmware_requests: set[tuple[str, str]] = set()
        self._lifx_label_requests: set[tuple[str, str]] = set()
        self._probe_payload = self.config.discovery_probe_payload.encode("utf-8")
        self._lifx_socket: Optional[socket.socket] = None
        self._lifx_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Register handler with shared protocol and start LIFX discovery listener."""
        if self.config.dry_run:
            self.logger.info("Discovery service running in dry-run mode.")
            return

        if not self.protocol:
            raise RuntimeError("Discovery service requires a GoveeProtocol instance")

        # Register handler for "scan" command responses (Govee)
        self.protocol.register_handler("scan", self._handle_scan_response)

        # Start LIFX discovery listener
        try:
            self._start_lifx_listener()
            self.logger.info(
                "Multi-protocol discovery service started",
                extra={
                    "govee_multicast": self.config.discovery_multicast_address,
                    "govee_port": self.config.discovery_multicast_port,
                    "lifx_port": 56700,
                },
            )
        except Exception as e:
            self.logger.warning(
                "Failed to start LIFX discovery listener",
                extra={"error": str(e)},
            )

    async def stop(self) -> None:
        """Service cleanup (handler remains registered with protocol)."""
        # Stop LIFX listener
        if self._lifx_task:
            self._lifx_task.cancel()
            try:
                await self._lifx_task
            except asyncio.CancelledError:
                pass

        if self._lifx_socket:
            self._lifx_socket.close()
            self._lifx_socket = None

        self.logger.info("Discovery service stopped")

    def _schedule(self, coro: asyncio.Future | Coroutine[Any, Any, Any]) -> None:
        """Schedule a coroutine on the protocol loop when available."""
        async def _wrapper() -> None:
            try:
                await coro  # type: ignore[misc]
            except Exception as exc:
                self.logger.exception(
                    "Scheduled coroutine failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        loop: Optional[asyncio.AbstractEventLoop] = getattr(self.protocol, "loop", None) if self.protocol else None
        try:
            if loop:
                loop.create_task(_wrapper())
            else:
                asyncio.create_task(_wrapper())
        except RuntimeError:
            asyncio.create_task(_wrapper())

    def _handle_scan_response(self, payload: Mapping[str, Any], addr: Tuple[str, int], raw: bytes) -> None:
        """Handle scan responses from the shared protocol."""
        self.logger.debug("Received scan response", extra={"from": addr, "payload": payload})

        parsed = _parse_payload(payload, addr)
        if parsed is None:
            record_discovery_error("invalid_payload")
            self.logger.warning(
                "Failed to parse discovery response",
                extra={"from": addr, "payload": payload}
            )
            return

        previous_ip = self._seen.get(parsed.id)
        self._seen[parsed.id] = parsed.ip
        if previous_ip and previous_ip == parsed.ip:
            self.logger.debug("Ignoring duplicate discovery response", extra={"device_id": parsed.id, "ip": parsed.ip})
            return

        self.logger.info(
            "Discovered device",
            extra={"device_id": parsed.id, "ip": parsed.ip, "model_number": parsed.model_number},
        )
        record_discovery_response("multicast")
        self.logger.debug(
            "Scheduling device record to database",
            extra={"device_id": parsed.id, "ip": parsed.ip}
        )
        self._schedule(self.store.record_discovery(parsed))

    def reset_cycle(self) -> None:
        """Clear seen devices for a new discovery cycle."""
        self._seen.clear()
        self._lifx_version_requests.clear()
        self._lifx_label_requests.clear()

    async def run_cycle(self) -> None:
        """Run a discovery cycle by sending probes for all protocols."""
        started = time.perf_counter()
        result = "ok"
        try:
            if self.config.dry_run:
                self.logger.debug("Skipping discovery probes in dry-run mode")
                await self.store.mark_stale(self.config.discovery_stale_after)
                result = "dry_run"
                return

            if not self.protocol:
                raise RuntimeError("Discovery service requires a GoveeProtocol instance")

            self.reset_cycle()

            # Send Govee multicast discovery
            target = (
                self.config.discovery_multicast_address,
                self.config.discovery_multicast_port,
            )
            self.logger.debug(
                "Sent Govee scan broadcast",
                extra={"target": target, "payload": self._probe_payload.decode("utf-8")},
            )
            self.protocol.send_to(self._probe_payload, target)

            # Send LIFX broadcast discovery
            self._send_lifx_discovery()

            if self.config.manual_unicast_probes:
                for device_id, ip in await self.store.manual_probe_targets():
                    self.logger.debug(
                        "Sending unicast probe",
                        extra={"device_id": device_id, "ip": ip},
                    )
                    self.protocol.send_to(self._probe_payload, (ip, self.config.discovery_multicast_port))

            try:
                await asyncio.sleep(self.config.discovery_response_timeout)
            finally:
                await self.store.mark_stale(self.config.discovery_stale_after)
        except Exception:
            result = "error"
            raise
        finally:
            observe_discovery_cycle(result, time.perf_counter() - started)

    # ===== LIFX Discovery Methods =====

    def _start_lifx_listener(self) -> None:
        """Create LIFX UDP socket and start listener task."""
        try:
            # Create UDP socket for LIFX discovery
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setblocking(False)

            # Bind to LIFX port (56700)
            sock.bind(("", 56700))

            self._lifx_socket = sock

            # Start listener task
            self._lifx_task = asyncio.create_task(self._lifx_listener())

            self.logger.debug("LIFX discovery listener started on port 56700")
        except Exception as e:
            self.logger.warning(
                "Failed to create LIFX discovery socket",
                extra={"error": str(e)},
            )
            if self._lifx_socket:
                self._lifx_socket.close()
                self._lifx_socket = None

    async def _lifx_listener(self) -> None:
        """Listen for LIFX StateService discovery responses."""
        if not self._lifx_socket:
            return

        loop = asyncio.get_event_loop()

        while True:
            try:
                # Wait for data to be available
                data, addr = await loop.sock_recvfrom(self._lifx_socket, 1024)

                # Handle response in background
                self._handle_lifx_response(data, addr)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.debug(
                    "Error in LIFX listener",
                    extra={"error": str(e)},
                )

    def _send_lifx_discovery(self) -> None:
        """Send LIFX GetService broadcast discovery packet."""
        if not self._lifx_socket:
            self.logger.debug("LIFX socket not available, skipping LIFX discovery")
            return

        try:
            # Get LIFX protocol handler
            lifx_handler = get_protocol_handler("lifx")

            # Build GetService broadcast packet
            discovery_packet = lifx_handler.build_discovery_request()

            # Send broadcast to 255.255.255.255:56700
            self._lifx_socket.sendto(discovery_packet, ("255.255.255.255", 56700))

            self.logger.debug("Sent LIFX GetService broadcast")
        except Exception as e:
            self.logger.warning(
                "Failed to send LIFX discovery broadcast",
                extra={"error": str(e)},
            )

    def _handle_lifx_response(self, data: bytes, addr: Tuple[str, int]) -> None:
        """Handle LIFX StateService discovery response."""
        try:
            # Get LIFX protocol handler
            lifx_handler = get_protocol_handler("lifx")

            header = lifx_handler.decode_header(data)

            if header["type"] == lifx_handler.MSG_STATE_SERVICE:
                parsed = lifx_handler.parse_state_service(header)
                if parsed is None:
                    return

                device_id = parsed["mac_str"]
                ip = addr[0]
                port = parsed.get("port", 56700)
                previous_ip = self._seen.get(device_id)
                self._seen[device_id] = ip

                version_key = (device_id, ip)
                if version_key not in self._lifx_version_requests and self._lifx_socket:
                    try:
                        version_request = lifx_handler.build_get_version_request(parsed["mac"])
                        self._lifx_socket.sendto(version_request, (ip, port))
                        self._lifx_version_requests.add(version_key)
                        self.logger.debug(
                            "Sent LIFX GetVersion request",
                            extra={"device_id": device_id, "ip": ip, "port": port},
                        )
                    except Exception as exc:
                        record_discovery_error("lifx_version_request_error")
                        self.logger.debug(
                            "Failed to send LIFX version request",
                            extra={"device_id": device_id, "ip": ip, "error": str(exc)},
                        )
                firmware_key = (device_id, ip)
                if firmware_key not in self._lifx_host_firmware_requests and self._lifx_socket:
                    try:
                        firmware_request = lifx_handler.build_get_host_firmware_request(parsed["mac"])
                        self._lifx_socket.sendto(firmware_request, (ip, port))
                        self._lifx_host_firmware_requests.add(firmware_key)
                        self.logger.debug(
                            "Sent LIFX GetHostFirmware request",
                            extra={"device_id": device_id, "ip": ip, "port": port},
                        )
                    except Exception as exc:
                        record_discovery_error("lifx_host_firmware_request_error")
                        self.logger.debug(
                            "Failed to send LIFX host firmware request",
                            extra={"device_id": device_id, "ip": ip, "error": str(exc)},
                        )
                label_key = (device_id, ip)
                if label_key not in self._lifx_label_requests and self._lifx_socket:
                    try:
                        label_request = lifx_handler.build_get_label_request(parsed["mac"])
                        self._lifx_socket.sendto(label_request, (ip, port))
                        self._lifx_label_requests.add(label_key)
                        self.logger.debug(
                            "Sent LIFX GetLabel request",
                            extra={"device_id": device_id, "ip": ip, "port": port},
                        )
                    except Exception as exc:
                        record_discovery_error("lifx_label_request_error")
                        self.logger.debug(
                            "Failed to send LIFX label request",
                            extra={"device_id": device_id, "ip": ip, "error": str(exc)},
                        )

                if previous_ip and previous_ip == ip:
                    self.logger.debug(
                        "Ignoring duplicate LIFX discovery response",
                        extra={"device_id": device_id, "ip": ip},
                    )
                    return

                discovery_result = DiscoveryResult(
                    id=device_id,
                    ip=ip,
                    protocol="lifx",
                    model_number=None,
                    device_type="light",
                    length_meters=None,
                    led_count=None,
                    led_density_per_meter=None,
                    has_zones=None,
                    zone_count=None,
                    description=None,
                    capabilities={"port": port, "service": parsed.get("service", 1)},
                    manual=False,
                )

                self.logger.info(
                    "Discovered LIFX device",
                    extra={"device_id": device_id, "ip": ip, "port": port},
                )
                record_discovery_response("lifx_broadcast")
                self.logger.debug(
                    "Scheduling LIFX device record to database",
                    extra={"device_id": device_id, "ip": ip}
                )
                self._schedule(self.store.record_discovery(discovery_result))
                return

            if header["type"] == lifx_handler.MSG_STATE_VERSION:
                try:
                    version_details = lifx_handler.parse_state_version(header["payload"])
                except Exception as exc:
                    record_discovery_error("lifx_version_parse_error")
                    self.logger.debug(
                        "Failed to parse LIFX version response",
                        extra={"from": addr, "error": str(exc)},
                    )
                    return

                device_mac = header.get("target", b"")
                device_id = ":".join(f"{b:02X}" for b in device_mac)
                ip = addr[0]
                discovery_result = DiscoveryResult(
                    id=device_id,
                    ip=ip,
                    protocol="lifx",
                    model_number=version_details.get("model_number"),
                    device_type="light",
                    length_meters=None,
                    led_count=None,
                    led_density_per_meter=None,
                    has_zones=None,
                    zone_count=None,
                    description=None,
                    capabilities=version_details.get("capabilities"),
                    manual=False,
                )
                self.logger.info(
                    "Received LIFX version details",
                    extra={
                        "device_id": device_id,
                        "ip": ip,
                        "model_number": discovery_result.model_number,
                    },
                )
                record_discovery_response("lifx_version")
                self.logger.debug(
                    "Scheduling LIFX version record to database",
                    extra={"device_id": device_id, "ip": ip}
                )
                self._schedule(self.store.record_discovery(discovery_result))
                return

            if header["type"] == lifx_handler.MSG_STATE_HOST_FIRMWARE:
                try:
                    firmware_details = lifx_handler.parse_state_host_firmware(header["payload"])
                except Exception as exc:
                    record_discovery_error("lifx_host_firmware_parse_error")
                    self.logger.debug(
                        "Failed to parse LIFX host firmware response",
                        extra={"from": addr, "error": str(exc)},
                    )
                    return

                device_mac = header.get("target", b"")
                device_id = ":".join(f"{b:02X}" for b in device_mac)
                ip = addr[0]
                discovery_result = DiscoveryResult(
                    id=device_id,
                    ip=ip,
                    protocol="lifx",
                    model_number=None,
                    device_type="light",
                    length_meters=None,
                    led_count=None,
                    led_density_per_meter=None,
                    has_zones=None,
                    zone_count=None,
                    description=None,
                    capabilities=firmware_details.get("capabilities"),
                    manual=False,
                )
                self.logger.info(
                    "Received LIFX host firmware details",
                    extra={"device_id": device_id, "ip": ip},
                )
                record_discovery_response("lifx_host_firmware")
                self.logger.debug(
                    "Scheduling LIFX host firmware record to database",
                    extra={"device_id": device_id, "ip": ip}
                )
                self._schedule(self.store.record_discovery(discovery_result))
                return

            if header["type"] == lifx_handler.MSG_STATE_LABEL:
                try:
                    label_details = lifx_handler.parse_state_label(header["payload"])
                except Exception as exc:
                    record_discovery_error("lifx_label_parse_error")
                    self.logger.debug(
                        "Failed to parse LIFX label response",
                        extra={"from": addr, "error": str(exc)},
                    )
                    return

                device_mac = header.get("target", b"")
                device_id = ":".join(f"{b:02X}" for b in device_mac)
                ip = addr[0]
                label = label_details.get("label") or None
                discovery_result = DiscoveryResult(
                    id=device_id,
                    ip=ip,
                    protocol="lifx",
                    name=label,
                    model_number=None,
                    device_type="light",
                    length_meters=None,
                    led_count=None,
                    led_density_per_meter=None,
                    has_zones=None,
                    zone_count=None,
                    description=None,
                    capabilities=None,
                    manual=False,
                )
                self.logger.info(
                    "Received LIFX label",
                    extra={"device_id": device_id, "ip": ip, "label": label},
                )
                record_discovery_response("lifx_label")
                self.logger.debug(
                    "Scheduling LIFX label record to database",
                    extra={"device_id": device_id, "ip": ip}
                )
                self._schedule(self.store.record_discovery(discovery_result))

        except Exception as e:
            record_discovery_error("lifx_parse_error")
            self.logger.debug(
                "Failed to parse LIFX discovery response",
                extra={"from": addr, "error": str(e)},
            )
