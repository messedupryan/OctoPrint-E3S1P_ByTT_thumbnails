# coding=utf-8
"""Event bus and dispatcher."""

import logging
from typing import Dict, List, Callable
from .types import Event, EventType

_logger = logging.getLogger(__name__)


class EventBus:
    """Central event bus for publishing and subscribing to events."""

    def __init__(self):
        """Initialize the event bus."""
        self._handlers: Dict[EventType, List[Callable]] = {}

    def subscribe(self, event_type: EventType, handler: Callable):
        """Subscribe a handler to an event type.

        Args:
            event_type: The EventType to subscribe to
            handler: Callable that handles the event
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []

        self._handlers[event_type].append(handler)
        _logger.debug(f"Subscribed {handler.__name__} to {event_type.value}")

    def unsubscribe(self, event_type: EventType, handler: Callable):
        """Unsubscribe a handler from an event type.

        Args:
            event_type: The EventType to unsubscribe from
            handler: The handler to remove
        """
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
                _logger.debug(
                    f"Unsubscribed {handler.__name__} from {event_type.value}"
                )
            except ValueError:
                _logger.warning(
                    f"{handler.__name__} not found in {event_type.value} handlers"
                )

    def publish(self, event: Event):
        """Publish an event to all subscribed handlers.

        Args:
            event: The Event to publish
        """
        _logger.debug(f"Publishing event: {event}")

        if event.type not in self._handlers:
            _logger.debug(f"No handlers registered for {event.type.value}")
            return

        for handler in self._handlers[event.type]:
            try:
                handler(event)
            except Exception as e:
                _logger.error(
                    f"Error in handler {handler.__name__}: {e}", exc_info=True
                )

    def clear(self):
        """Clear all registered handlers."""
        self._handlers.clear()
        _logger.debug("Event bus cleared")


# Global event bus instance
_event_bus = EventBus()


def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    return _event_bus
