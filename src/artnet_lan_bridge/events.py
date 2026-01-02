"""Event bus for pub/sub system events."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set


@dataclass
class SystemEvent:
    """System event with type, timestamp, and data."""

    event_type: str
    timestamp: str
    data: Dict[str, Any]

    @classmethod
    def create(cls, event_type: str, data: Dict[str, Any]) -> SystemEvent:
        """Create a new system event with current timestamp."""
        return cls(
            event_type=event_type,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            data=data,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "event": self.event_type,
            "timestamp": self.timestamp,
            "data": self.data,
        }


class EventBus:
    """
    Async pub/sub event bus for system events.

    Supports subscribing to specific event types and publishing events
    to all registered subscribers.
    """

    def __init__(self):
        """Initialize event bus."""
        self._subscribers: Dict[str, Set[Callable[[SystemEvent], Any]]] = defaultdict(set)
        self._wildcard_subscribers: Set[Callable[[SystemEvent], Any]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Publish event to all subscribers.

        Args:
            event_type: Type of event (e.g., 'device_discovered', 'mapping_created')
            data: Event data dictionary
        """
        event = SystemEvent.create(event_type, data)

        # Get subscribers for this event type and wildcard subscribers
        async with self._lock:
            subscribers = list(self._subscribers.get(event_type, set()))
            wildcard_subs = list(self._wildcard_subscribers)

        all_subscribers = subscribers + wildcard_subs

        # Call subscribers (don't hold lock during callbacks)
        for callback in all_subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback(event))
                else:
                    callback(event)
            except Exception:
                # Ignore subscriber errors to prevent one bad subscriber from breaking others
                pass

    async def subscribe(
        self,
        event_type: str,
        callback: Callable[[SystemEvent], Any],
    ) -> Callable[[], None]:
        """
        Subscribe to specific event type.

        Args:
            event_type: Type of event to subscribe to, or '*' for all events
            callback: Function to call when event is published (can be async)

        Returns:
            Unsubscribe function
        """
        async with self._lock:
            if event_type == "*":
                self._wildcard_subscribers.add(callback)
            else:
                self._subscribers[event_type].add(callback)

        def unsubscribe() -> None:
            asyncio.create_task(self._unsubscribe(event_type, callback))

        return unsubscribe

    async def _unsubscribe(self, event_type: str, callback: Callable[[SystemEvent], Any]) -> None:
        """Internal unsubscribe implementation."""
        async with self._lock:
            if event_type == "*":
                self._wildcard_subscribers.discard(callback)
            else:
                self._subscribers[event_type].discard(callback)

    async def subscriber_count(self, event_type: Optional[str] = None) -> int:
        """
        Get count of subscribers.

        Args:
            event_type: Event type to count, or None for total count

        Returns:
            Number of subscribers
        """
        async with self._lock:
            if event_type is None:
                total = len(self._wildcard_subscribers)
                total += sum(len(subs) for subs in self._subscribers.values())
                return total
            elif event_type == "*":
                return len(self._wildcard_subscribers)
            else:
                return len(self._subscribers.get(event_type, set()))

    async def event_types(self) -> List[str]:
        """Get list of event types with active subscribers."""
        async with self._lock:
            return list(self._subscribers.keys())


# Predefined event types
EVENT_DEVICE_DISCOVERED = "device_discovered"
EVENT_DEVICE_UPDATED = "device_updated"
EVENT_DEVICE_OFFLINE = "device_offline"
EVENT_DEVICE_ONLINE = "device_online"
EVENT_MAPPING_CREATED = "mapping_created"
EVENT_MAPPING_UPDATED = "mapping_updated"
EVENT_MAPPING_DELETED = "mapping_deleted"
EVENT_ARTNET_PACKET = "artnet_packet_received"
EVENT_QUEUE_DEPTH_CHANGED = "queue_depth_changed"
EVENT_RATE_LIMIT_TRIGGERED = "rate_limit_triggered"
EVENT_HEALTH_STATUS_CHANGED = "health_status_changed"
