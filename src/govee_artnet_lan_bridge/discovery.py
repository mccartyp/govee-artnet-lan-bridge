"""Discovery service for Govee devices."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Mapping, Optional, Tuple

from .config import Config
from .devices import DeviceStore, DiscoveryResult
from .logging import get_logger
from .metrics import observe_discovery_cycle, record_discovery_error, record_discovery_response
from .protocol import GoveeProtocol


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
    model = data.get("model") or data.get("sku") or data.get("type")
    description = data.get("description") or data.get("name")
    capabilities = data.get("capabilities") or data.get("capability") or data.get("features")
    return DiscoveryResult(
        id=str(device_id),
        ip=str(ip),
        model=str(model) if model is not None else None,
        description=str(description) if description is not None else None,
        capabilities=capabilities,
        manual=False,
    )


class DiscoveryService:
    """High-level discovery coordinator using shared protocol."""

    def __init__(self, config: Config, store: DeviceStore, protocol: Optional[GoveeProtocol] = None) -> None:
        self.config = config
        self.store = store
        self.protocol = protocol
        self.logger = get_logger("govee.discovery")
        self._seen: Dict[str, str] = {}
        self._probe_payload = self.config.discovery_probe_payload.encode("utf-8")

    async def start(self) -> None:
        """Register handler with shared protocol."""
        if self.config.dry_run:
            self.logger.info("Discovery service running in dry-run mode.")
            return

        if not self.protocol:
            raise RuntimeError("Discovery service requires a GoveeProtocol instance")

        # Register handler for "scan" command responses
        self.protocol.register_handler("scan", self._handle_scan_response)
        self.logger.info(
            "Discovery service registered with protocol",
            extra={
                "multicast": self.config.discovery_multicast_address,
                "probe_port": self.config.discovery_multicast_port,
                "reply_port": self.config.discovery_reply_port,
            },
        )

    async def stop(self) -> None:
        """Service cleanup (handler remains registered with protocol)."""
        self.logger.info("Discovery service stopped")

    def _handle_scan_response(self, payload: Mapping[str, Any], addr: Tuple[str, int]) -> None:
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

        self.logger.info("Discovered device", extra={"device_id": parsed.id, "ip": parsed.ip, "model": parsed.model})
        record_discovery_response("multicast")
        asyncio.create_task(self.store.record_discovery(parsed))

    def reset_cycle(self) -> None:
        """Clear seen devices for a new discovery cycle."""
        self._seen.clear()

    async def run_cycle(self) -> None:
        """Run a discovery cycle by sending probes."""
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

            target = (
                self.config.discovery_multicast_address,
                self.config.discovery_multicast_port,
            )
            self.protocol.send_to(self._probe_payload, target)

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
