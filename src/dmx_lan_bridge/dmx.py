"""Unified DMX frame abstraction and mapping service for multi-protocol support.

This module provides protocol-agnostic DMX data structures and mapping logic
that works with any input protocol (ArtNet, sACN, etc.).
"""

from __future__ import annotations

import asyncio
import copy
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional
from uuid import uuid4

from .config import Config
from .devices import DeviceStateUpdate, DeviceStore, MappingRecord
from .events import EVENT_MAPPING_CREATED, EVENT_MAPPING_DELETED, EVENT_MAPPING_UPDATED, SystemEvent
from .logging import get_logger
from .metrics import observe_artnet_ingest, record_artnet_packet, record_artnet_update


# Import mapping helpers from artnet module
# These will remain in artnet.py initially for compatibility
# TODO: Move to dmx.py in future refactor
from .artnet import (
    DeviceMapping,
    UniverseMapping,
    _build_spec,
    DEFAULT_DEBOUNCE_SECONDS,
)


@dataclass(frozen=True)
class DmxFrame:
    """Protocol-agnostic DMX frame from any input source.

    This unified structure allows ArtNet, sACN, and future protocols
    to feed the same mapping layer.
    """

    universe: int           # DMX universe number (0-63999)
    data: bytes            # Exactly 512 DMX channel values (0-255 each)
    sequence: int          # Sequence number (0-255, wraps around)
    source_protocol: str   # Input protocol: "artnet", "sacn", etc.
    priority: int          # Merge priority (0-200, higher wins)
    timestamp: float       # perf_counter() when frame was received
    source_id: str         # Unique identifier for this source

    def __post_init__(self):
        """Validate DMX frame constraints."""
        if len(self.data) != 512:
            raise ValueError(f"DMX frame must have exactly 512 bytes, got {len(self.data)}")
        if not 0 <= self.priority <= 200:
            raise ValueError(f"DMX priority must be 0-200, got {self.priority}")
        if not 0 <= self.sequence <= 255:
            raise ValueError(f"DMX sequence must be 0-255, got {self.sequence}")


class PriorityMerger:
    """Manages priority-based DMX source merging per universe.

    When multiple sources send to the same universe, the highest priority wins.
    Follows sACN priority model (0-200, higher wins).

    Priority Mapping:
        - sACN sources: Use native priority (0-200, default 100)
        - ArtNet sources: Fixed priority 50 (below sACN default)
        - Future protocols: Configurable
    """

    # Priority constants
    ARTNET_FIXED_PRIORITY = 50      # ArtNet runs below sACN default
    SACN_DEFAULT_PRIORITY = 100     # sACN standard default
    TIMEOUT_SECONDS = 2.5           # Per E1.31 specification

    def __init__(self):
        self.logger = get_logger("dmx.merger")
        self._active_sources: Dict[int, Dict[str, DmxFrame]] = {}
        # Structure: {universe: {source_id: DmxFrame}}
        self._last_winner: Dict[int, str] = {}
        # Track last winning source per universe for logging

    def merge(self, frame: DmxFrame) -> Optional[DmxFrame]:
        """Merge incoming frame with active sources for its universe.

        Args:
            frame: Incoming DMX frame from any protocol

        Returns:
            The winning frame (highest priority) if this frame wins,
            or None if a higher-priority source is active.
        """
        universe = frame.universe

        # Clean up timed-out sources for this universe
        self._remove_stale_sources(universe, frame.timestamp)

        # Update active sources with this frame
        if universe not in self._active_sources:
            self._active_sources[universe] = {}

        self._active_sources[universe][frame.source_id] = frame

        # Find highest priority source
        active = self._active_sources[universe]
        if not active:
            return frame  # Only source, wins by default

        winner = max(active.values(), key=lambda f: f.priority)

        # Log priority changes
        previous_winner = self._last_winner.get(universe)
        if winner.source_id != previous_winner:
            self._last_winner[universe] = winner.source_id
            self.logger.info(
                "DMX source priority change",
                extra={
                    "universe": universe,
                    "winner": winner.source_protocol,
                    "winner_priority": winner.priority,
                    "source_count": len(active),
                    "sources": {
                        src_id: {"protocol": f.source_protocol, "priority": f.priority}
                        for src_id, f in active.items()
                    }
                }
            )

        # Return winner if this frame won, else None
        if winner.source_id == frame.source_id:
            return winner
        else:
            # This frame lost to higher priority
            self.logger.debug(
                "DMX frame rejected (lower priority)",
                extra={
                    "universe": universe,
                    "this_protocol": frame.source_protocol,
                    "this_priority": frame.priority,
                    "winner_protocol": winner.source_protocol,
                    "winner_priority": winner.priority,
                }
            )
            return None

    def _remove_stale_sources(self, universe: int, current_time: float) -> None:
        """Remove sources that haven't sent data within timeout period."""
        if universe not in self._active_sources:
            return

        stale_sources = [
            source_id
            for source_id, frame in self._active_sources[universe].items()
            if (current_time - frame.timestamp) > self.TIMEOUT_SECONDS
        ]

        for source_id in stale_sources:
            frame = self._active_sources[universe].pop(source_id)
            self.logger.info(
                "DMX source timed out",
                extra={
                    "universe": universe,
                    "source_protocol": frame.source_protocol,
                    "source_id": source_id,
                }
            )

        # Clean up empty universe entries
        if universe in self._active_sources and not self._active_sources[universe]:
            del self._active_sources[universe]
            if universe in self._last_winner:
                del self._last_winner[universe]

    def get_active_source_count(self, universe: int) -> int:
        """Get number of active sources for a universe."""
        return len(self._active_sources.get(universe, {}))

    def get_active_universes(self) -> List[int]:
        """Get list of universes with active sources."""
        return list(self._active_sources.keys())


class DmxMappingService:
    """Protocol-agnostic DMX to device mapping service.

    Receives DMX frames from any input protocol (ArtNet, sACN, etc.),
    applies priority-based merging, and generates device state updates.

    This service replaces the mapping logic previously embedded in ArtNetService.
    """

    def __init__(
        self,
        config: Config,
        store: DeviceStore,
        initial_last_payloads: Optional[Mapping[str, Mapping[str, Any]]] = None,
        event_bus: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.store = store
        self.event_bus = event_bus
        self.logger = get_logger("dmx.mapping")

        # Priority-based source merging
        self._merger = PriorityMerger()

        # Universe mappings (DMX → Device)
        self._universe_mappings: Dict[int, UniverseMapping] = {}

        # Device update tracking (for debouncing and change detection)
        self._last_payloads: MutableMapping[str, Mapping[str, Any]] = (
            copy.deepcopy(initial_last_payloads) if initial_last_payloads else {}
        )
        self._pending_updates: MutableMapping[str, DeviceStateUpdate] = {}
        self._debounce_tasks: MutableMapping[str, asyncio.Task[None]] = {}
        self._debounce_seconds = DEFAULT_DEBOUNCE_SECONDS

        # Configuration
        self._trace_context_ids = config.trace_context_ids
        self._trace_context_sample_rate = max(0.0, min(1.0, config.trace_context_sample_rate))
        self._log_sample_rate = max(0.0, min(1.0, config.noisy_log_sample_rate))

        # Event subscriptions
        self._reload_lock = asyncio.Lock()
        self._unsubscribe_handlers: list[Callable[[], None]] = []

    async def start(self) -> None:
        """Initialize mapping service and load mappings from database."""
        await self._reload_mappings()

        if not self._universe_mappings:
            self.logger.warning("No DMX mappings configured")

        # Subscribe to mapping events for automatic reload
        if self.event_bus:
            for event_type in [EVENT_MAPPING_CREATED, EVENT_MAPPING_UPDATED, EVENT_MAPPING_DELETED]:
                unsubscribe = await self.event_bus.subscribe(event_type, self._handle_mapping_event)
                self._unsubscribe_handlers.append(unsubscribe)
            self.logger.info("Subscribed to mapping events for automatic reload")

        self.logger.info(
            "DMX mapping service started",
            extra={
                "universes": sorted(self._universe_mappings.keys()),
                "mapping_count": sum(len(m._mappings) for m in self._universe_mappings.values()),
            },
        )

    async def stop(self) -> None:
        """Stop mapping service and clean up resources."""
        # Unsubscribe from mapping events
        for unsubscribe in self._unsubscribe_handlers:
            unsubscribe()
        self._unsubscribe_handlers.clear()

        # Flush pending device updates
        await self._flush_pending()

        self.logger.info("DMX mapping service stopped")

    async def process_dmx_frame(self, frame: DmxFrame) -> None:
        """Process a DMX frame from any input protocol.

        This is the main entry point for all DMX input protocols.

        Args:
            frame: DMX frame from ArtNet, sACN, or other protocol
        """
        started = time.perf_counter()
        status = "ok"

        try:
            # Priority-based merging
            winning_frame = self._merger.merge(frame)

            if winning_frame is None:
                # This frame lost to higher priority source
                status = "lower_priority"
                return

            # Record metrics (using artnet metrics for now, TODO: rename to dmx metrics)
            record_artnet_packet(frame.universe)

            # Get mapping for this universe
            mapping = self._universe_mappings.get(frame.universe)
            if mapping is None:
                if random.random() <= self._log_sample_rate:
                    self.logger.debug(
                        "No mapping for DMX universe",
                        extra={
                            "universe": frame.universe,
                            "protocol": frame.source_protocol,
                            "sequence": frame.sequence,
                        },
                    )
                status = "unmapped"
                return

            # Generate context ID for tracing (if enabled)
            context_id: Optional[str] = None
            if self._trace_context_ids and random.random() <= self._trace_context_sample_rate:
                context_id = f"dmx-{frame.source_protocol}-{frame.universe}-{frame.sequence}-{uuid4().hex}"

            # Apply mappings to generate device updates
            updates = mapping.apply(frame.data, context_id=context_id)

            if not updates:
                status = "no_updates"
                if random.random() <= self._log_sample_rate:
                    self.logger.debug(
                        "DMX frame generated no device updates",
                        extra={
                            "universe": frame.universe,
                            "protocol": frame.source_protocol,
                            "sequence": frame.sequence,
                            "context_id": context_id,
                        },
                    )
            else:
                if random.random() <= self._log_sample_rate:
                    self.logger.debug(
                        "DMX frame processed",
                        extra={
                            "universe": frame.universe,
                            "protocol": frame.source_protocol,
                            "priority": frame.priority,
                            "sequence": frame.sequence,
                            "updates_count": len(updates),
                            "context_id": context_id,
                        },
                    )

                # Schedule device updates (with debouncing)
                for update in updates:
                    self._schedule_update(update)

        except Exception:
            status = "error"
            raise
        finally:
            # Record processing time
            observe_artnet_ingest(frame.universe, status, time.perf_counter() - started)

    async def _reload_mappings(self) -> None:
        """Reload DMX mappings from database."""
        async with self._reload_lock:
            records = await self.store.mappings()
            universes: Dict[int, List[DeviceMapping]] = {}

            for record in records:
                if record.channel <= 0 or record.length <= 0:
                    self.logger.warning(
                        "Skipping mapping; invalid channel or length",
                        extra={
                            "device_id": record.device_id,
                            "universe": record.universe,
                            "channel": record.channel,
                            "length": record.length,
                        },
                    )
                    continue

                spec = _build_spec(record)

                if record.mapping_type == "discrete" and not record.field:
                    self.logger.warning(
                        "Skipping mapping; discrete mapping missing field",
                        extra={
                            "device_id": record.device_id,
                            "universe": record.universe,
                            "channel": record.channel,
                        },
                    )
                    continue

                if record.length < spec.required_channels:
                    self.logger.warning(
                        "Skipping mapping; insufficient length for required channels",
                        extra={
                            "device_id": record.device_id,
                            "universe": record.universe,
                            "channel": record.channel,
                            "length": record.length,
                            "required_channels": spec.required_channels,
                        },
                    )
                    continue

                universes.setdefault(record.universe, []).append(DeviceMapping(record=record, spec=spec))

            self._universe_mappings = {
                universe: UniverseMapping(universe, mappings, self._log_sample_rate)
                for universe, mappings in universes.items()
            }

            self.logger.info(
                "Reloaded DMX mappings",
                extra={
                    "universes": sorted(self._universe_mappings.keys()),
                    "mapping_count": len(records),
                },
            )

    async def _handle_mapping_event(self, event: SystemEvent) -> None:
        """Handle mapping change events by reloading mappings."""
        self.logger.info(
            "Mapping changed, reloading",
            extra={"event_type": event.event_type, "data": event.data},
        )
        await self._reload_mappings()

    def _schedule_update(self, update: DeviceStateUpdate) -> None:
        """Schedule a device update with debouncing and change detection."""
        previous = self._last_payloads.get(update.device_id)
        if previous is not None and previous == update.payload:
            if random.random() <= self._log_sample_rate:
                self.logger.debug(
                    "Skipping duplicate device update",
                    extra={
                        "device_id": update.device_id,
                        "payload": update.payload,
                        "context_id": update.context_id,
                    },
                )
            return

        if random.random() <= self._log_sample_rate:
            self.logger.debug(
                "Scheduling device update",
                extra={
                    "device_id": update.device_id,
                    "payload": update.payload,
                    "previous_payload": previous,
                    "context_id": update.context_id,
                },
            )

        self._last_payloads[update.device_id] = update.payload
        self._pending_updates[update.device_id] = update
        record_artnet_update(update.device_id)

        if update.device_id not in self._debounce_tasks:
            self._debounce_tasks[update.device_id] = asyncio.create_task(
                self._flush_after(update.device_id)
            )

    async def _flush_after(self, device_id: str) -> None:
        """Flush device update after debounce period."""
        try:
            await asyncio.sleep(self._debounce_seconds)
            update = self._pending_updates.pop(device_id, None)
            if update:
                await self.store.enqueue_state(update)
                self.logger.debug(
                    "Enqueued device update",
                    extra={
                        "device_id": device_id,
                        "payload": update.payload,
                        "context_id": update.context_id,
                    },
                )
        finally:
            self._debounce_tasks.pop(device_id, None)

    async def _flush_pending(self) -> None:
        """Flush all pending device updates immediately."""
        import contextlib

        pending = list(self._debounce_tasks.values())
        for task in pending:
            task.cancel()

        if pending:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*pending)

        for device_id, update in list(self._pending_updates.items()):
            await self.store.enqueue_state(update)
            self._pending_updates.pop(device_id, None)

    def snapshot_last_payloads(self) -> Dict[str, Mapping[str, Any]]:
        """Return a copy of last delivered payloads for state persistence."""
        return copy.deepcopy(self._last_payloads)

    def get_active_universes(self) -> List[int]:
        """Return the universes that currently have configured mappings."""

        return sorted(self._universe_mappings.keys())

    def get_merger_stats(self) -> Dict[str, Any]:
        """Get statistics about priority merging."""
        return {
            "active_universes": self._merger.get_active_universes(),
            "source_counts": {
                universe: self._merger.get_active_source_count(universe)
                for universe in self._merger.get_active_universes()
            },
        }
