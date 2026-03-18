# coding=utf-8
"""Internal upload processing lifecycle handler."""

from ..events.types import Event, EventType
from ..services import UploadIntentService
from .base import EventHandler


class UploadProcessingEventHandler(EventHandler):
    """Drive queued upload processing from internal lifecycle events."""

    event_types = [
        EventType.FILE_UPLOADED,
        EventType.FILE_QUEUED,
        EventType.FILE_PROCESSING_STARTED,
        EventType.FILE_PROCESSING_FINISHED,
    ]

    def __init__(self, plugin):
        self._plugin = plugin
        super().__init__()

    def handle(self, event: Event):
        if event.type == EventType.FILE_UPLOADED:
            self._plugin._logger.debug(
                f"Queueing uploaded file {event.payload.get('path')} for background processing"
            )
            self._plugin._upload_processing_service.queue_file(
                event.payload, trigger=event.type.value
            )
            return

        if event.type == EventType.FILE_QUEUED:
            self._plugin._logger.debug(
                f"File queued for processing: {event.payload.get('path')}"
            )
            return

        if event.type == EventType.FILE_PROCESSING_STARTED:
            self._plugin._logger.debug(
                f"File processing started: {event.payload.get('path')}"
            )
            return

        self._plugin._logger.debug(
            f"File processing finished: {event.payload.get('path')} "
            f"success={event.payload.get('success')}"
        )

        if not event.payload.get("success"):
            return

        if not UploadIntentService.wants_immediate_select_or_print(event.payload):
            return

        self._plugin._logger.debug(
            f"Auto-activating helper upload after slicer upload for {event.payload.get('path')}"
        )
        self._plugin._workflow.activate_thumbnail_for_print(
            event.payload,
            trigger=event.type.value,
        )
