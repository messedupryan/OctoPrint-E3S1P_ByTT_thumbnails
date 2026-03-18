# coding=utf-8
"""Base handler class for event handling."""

import logging
from abc import ABC, abstractmethod
from ..events.types import Event
from ..events.bus import get_event_bus

_logger = logging.getLogger(__name__)


class EventHandler(ABC):
    """Base class for event handlers."""

    # Subclasses should define the event types they handle
    event_types: list = []

    def __init__(self):
        """Initialize the handler and register with event bus."""
        self._register_handlers()

    def _register_handlers(self):
        """Register this handler with the event bus."""
        event_bus = get_event_bus()
        for event_type in self.event_types:
            event_bus.subscribe(event_type, self.handle)
            _logger.debug(
                f"{self.__class__.__name__} registered for {event_type.value}"
            )

    def unregister(self):
        """Unregister this handler from the event bus."""
        event_bus = get_event_bus()
        for event_type in self.event_types:
            event_bus.unsubscribe(event_type, self.handle)
            _logger.debug(
                f"{self.__class__.__name__} unregistered from {event_type.value}"
            )

    @abstractmethod
    def handle(self, event: Event):
        """Handle the event.

        Args:
            event: The Event to handle
        """
        pass
