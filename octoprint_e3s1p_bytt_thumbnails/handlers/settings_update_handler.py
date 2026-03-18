# coding=utf-8
"""Settings update event handler."""

from .base import EventHandler
from ..events.types import Event, EventType


class SettingsUpdateHandler(EventHandler):
    """React to plugin settings updates."""

    event_types = [EventType.SETTINGS_UPDATED]

    def __init__(self, plugin):
        self._plugin = plugin
        super().__init__()

    def handle(self, event: Event):
        self._plugin._logger.debug("Handling SettingsUpdated event")
