# coding=utf-8
"""File metadata event handler."""

from .base import EventHandler
from ..events.types import Event, EventType


class FileMetadataEventHandler(EventHandler):
    """Generate and refresh thumbnail metadata for file events."""

    event_types = [EventType.FILE_ADDED, EventType.FILE_REMOVED]

    def __init__(self, plugin):
        self._plugin = plugin
        super().__init__()

    def handle(self, event: Event):
        payload = event.payload
        if payload.get("storage") != "local" or not payload.get("name"):
            self._plugin._logger.debug(
                f"Ignoring {event.type.value} due to payload: storage={payload.get('storage')!r} "
                f"name={payload.get('name')!r} path={payload.get('path')!r}"
            )
            return
        if payload.get("name", "").upper() == self._plugin._helper_basename:
            self._plugin._logger.debug(
                f"Ignoring helper event {event.type.value} for {payload.get('name')}"
            )
            return
        self._plugin._logger.debug(
            f"Dispatching {event.type.value} to workflow for {payload.get('name')} "
            f"(path={payload.get('path')})"
        )
        self._plugin._workflow.handle_file_added_or_removed(event.type.value, payload)
