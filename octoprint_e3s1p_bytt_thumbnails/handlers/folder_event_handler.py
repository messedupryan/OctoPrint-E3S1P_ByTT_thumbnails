# coding=utf-8
"""Folder event handler."""

from .base import EventHandler
from ..events.types import Event, EventType


class FolderEventHandler(EventHandler):
    """Handle folder add/remove events."""

    event_types = [EventType.FOLDER_ADDED, EventType.FOLDER_REMOVED]

    def __init__(self, plugin):
        self._plugin = plugin
        super().__init__()

    def handle(self, event: Event):
        payload = event.payload
        if payload.get("storage") != "local":
            self._plugin._logger.debug(
                f"Ignoring {event.type.value} for non-local storage {payload.get('storage')!r}"
            )
            return

        if event.type == EventType.FOLDER_REMOVED:
            self._plugin._logger.debug(
                f"Dispatching FolderRemoved for path={payload.get('path')}"
            )
            self._plugin._workflow.handle_folder_removed(payload)
            return

        self._plugin._logger.debug(
            f"Dispatching FolderAdded for path={payload.get('path')}"
        )
        self._plugin._workflow.handle_folder_added(payload)
