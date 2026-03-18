# coding=utf-8
"""Event type definitions."""

from enum import Enum


class EventType(Enum):
    """Enumeration of all event types in the system."""

    # File events
    UPLOAD = "Upload"
    FILE_ADDED = "FileAdded"
    FILE_REMOVED = "FileRemoved"
    FILE_UPDATED = "FileUpdated"
    FILE_SELECTED = "FileSelected"
    FILE_UPLOADED = "file_uploaded"
    FILE_QUEUED = "file_queued"
    FILE_PROCESSING_STARTED = "file_processing_started"
    FILE_PROCESSING_FINISHED = "file_processing_finished"
    FOLDER_ADDED = "FolderAdded"
    FOLDER_REMOVED = "FolderRemoved"
    TRANSFER_DONE = "TransferDone"
    SETTINGS_UPDATED = "SettingsUpdated"

    # Print lifecycle events
    PRINT_STARTED = "PrintStarted"
    PRINT_RESUMED = "PrintResumed"
    PRINT_CANCELLED = "PrintCancelled"
    PRINT_PAUSED = "PrintPaused"
    PRINT_DONE = "PrintDone"

    # Scan events
    SCAN_STARTED = "scan_started"
    SCAN_COMPLETED = "scan_completed"
    SCAN_FAILED = "scan_failed"

    # Thumbnail events
    THUMBNAIL_GENERATED = "thumbnail_generated"
    THUMBNAIL_FAILED = "thumbnail_failed"

    @classmethod
    def from_octoprint(cls, octoprint_event: str):
        """Map OctoPrint event names to EventType.

        Args:
            octoprint_event: OctoPrint event name

        Returns:
            EventType or None if no mapping exists
        """
        for event_type in cls:
            if event_type.value == octoprint_event:
                return event_type
        return None


class Event:
    """Base event class."""

    def __init__(self, event_type: EventType, payload=None):
        """Initialize event.

        Args:
            event_type: The EventType of this event
            payload: Optional data associated with the event
        """
        self.type = event_type
        self.payload = payload or {}

    def __repr__(self):
        return f"Event(type={self.type.value}, payload={self.payload})"
