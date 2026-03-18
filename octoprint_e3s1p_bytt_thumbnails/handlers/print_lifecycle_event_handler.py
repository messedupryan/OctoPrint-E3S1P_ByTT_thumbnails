# coding=utf-8
"""Print lifecycle event handler."""

from .base import EventHandler
from ..events.types import Event, EventType


class PrintLifecycleEventHandler(EventHandler):
    """Forward print lifecycle events to the printer LCD workflow."""

    event_types = [
        EventType.PRINT_STARTED,
        EventType.PRINT_RESUMED,
        EventType.PRINT_CANCELLED,
        EventType.PRINT_PAUSED,
        EventType.PRINT_DONE,
    ]

    def __init__(self, plugin):
        self._plugin = plugin
        super().__init__()

    def handle(self, event: Event):
        self._plugin._logger.debug(
            f"Dispatching print lifecycle event {event.type.value}"
        )
        if event.type == EventType.PRINT_STARTED:
            processed = (
                self._plugin._upload_processing_service.ensure_processed_before_print(
                    event.payload,
                    trigger=event.type.value,
                )
            )
            self._plugin._logger.debug(
                f"Pre-print processing check for {event.type.value} completed "
                f"with processed={processed} payload={event.payload}"
            )
        self._plugin._workflow.handle_print_event(event.type.value, event.payload)
