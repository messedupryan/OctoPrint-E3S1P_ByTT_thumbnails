# coding=utf-8
"""Upload event adapter for eager processing with queued fallback."""

from ..events import Event, EventType, get_event_bus
from ..services import UploadIntentService
from .base import EventHandler


class UploadEventHandler(EventHandler):
    """Prepare local uploads immediately and fall back to queued processing if needed."""

    event_types = [EventType.UPLOAD]

    def __init__(self, plugin):
        self._plugin = plugin
        super().__init__()

    def handle(self, event: Event):
        normalized = self._plugin._workflow.normalize_local_payload(
            event.payload, trigger=event.type.value
        )
        if normalized is None:
            self._plugin._logger.debug(
                f"Ignoring Upload event because no local gcode target was resolved: {event.payload}"
            )
            return

        self._plugin._logger.debug(
            f"Preparing uploaded file inline for {normalized.get('name')} "
            f"(path={normalized.get('path')})"
        )
        context = self._plugin._workflow._build_file_context(normalized)
        if self._plugin._workflow._has_prepared_helper_sidecar(context):
            self._plugin._logger.debug(
                f"Upload already has a prepared helper sidecar for {normalized.get('path')}; "
                "skipping inline regeneration"
            )
            prepared = True
        else:
            prepared = self._plugin._workflow.prepare_file_for_storage(
                normalized,
                trigger=event.type.value,
                skip_if_processed=True,
            )

        if UploadIntentService.wants_immediate_select_or_print(event.payload):
            self._plugin._logger.debug(
                f"Handling Upload inline for immediate select/print of {normalized.get('name')} "
                f"(path={normalized.get('path')})"
            )
            if prepared:
                self._plugin._workflow.note_post_helper_action(
                    normalized,
                    should_print=UploadIntentService.wants_immediate_print(
                        event.payload
                    ),
                )
                self._plugin._workflow.activate_thumbnail_for_print(
                    normalized,
                    trigger=event.type.value,
                )
                return

        if prepared:
            self._plugin._logger.debug(
                f"Upload preparation finished inline for {normalized.get('path')}; no queue fallback needed"
            )
            return

        self._plugin._logger.debug(
            f"Inline upload preparation failed or was incomplete for {normalized.get('name')} "
            f"(path={normalized.get('path')}); publishing queued fallback"
        )
        get_event_bus().publish(Event(EventType.FILE_UPLOADED, payload=normalized))
