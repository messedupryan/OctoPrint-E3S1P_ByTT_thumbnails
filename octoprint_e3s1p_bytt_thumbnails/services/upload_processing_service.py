# coding=utf-8
"""Queued processing support for upload-triggered print preparation."""

import queue
import threading

from ..events import Event, EventType, get_event_bus


class UploadProcessingService:
    """Processes uploaded files asynchronously and coordinates print-start safety."""

    def __init__(self, logger, workflow, print_wait_timeout=15.0):
        self._logger = logger
        self._workflow = workflow
        self._print_wait_timeout = print_wait_timeout
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._pending_events = {}
        self._lock = threading.Lock()
        self._worker = None

    def start(self):
        """Start the background worker."""
        if self._worker is not None and self._worker.is_alive():
            self._logger.debug("Upload processing worker already running")
            return

        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._run,
            name="e3s1p-bytt-upload-worker",
            daemon=True,
        )
        self._worker.start()
        self._logger.debug("Started upload processing worker")

    def stop(self):
        """Stop the background worker."""
        self._stop_event.set()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=5.0)
        self._worker = None
        with self._lock:
            for done_event in self._pending_events.values():
                done_event.set()
            self._pending_events.clear()
        self._logger.debug("Stopped upload processing worker")

    def queue_file(self, payload, trigger="Upload"):
        """Queue a file for asynchronous processing."""
        normalized = self._workflow.normalize_local_payload(payload, trigger=trigger)
        if normalized is None:
            self._logger.debug(
                f"Skipping queue request for {trigger}; payload could not be normalized: {payload}"
            )
            return False

        path = normalized["path"]
        if self._workflow.is_file_already_processed(path):
            self._logger.debug(f"Skipping queue for already processed file {path}")
            return False

        with self._lock:
            if path in self._pending_events:
                self._logger.debug(
                    f"Skipping duplicate queue request for pending file {path}"
                )
                return False
            self._pending_events[path] = threading.Event()

        self._queue.put(normalized)
        get_event_bus().publish(Event(EventType.FILE_QUEUED, payload=normalized))
        self._logger.debug(f"Queued {path} for background processing")
        return True

    def ensure_processed_before_print(self, payload, trigger="PrintStarted"):
        """Ensure a file is processed before print start continues."""
        normalized = self._workflow.normalize_local_payload(payload, trigger=trigger)
        if normalized is None:
            self._logger.debug(
                f"No printable local file resolved for {trigger} payload={payload}"
            )
            return False

        path = normalized["path"]
        if self._workflow.is_file_already_processed(path):
            self._logger.debug(f"File {path} already processed before {trigger}")
            return True

        pending_event = self._get_pending_event(path)
        if pending_event is not None:
            self._logger.debug(
                f"Waiting up to {self._print_wait_timeout:.1f}s for queued processing of {path} before {trigger}"
            )
            pending_event.wait(self._print_wait_timeout)
            if self._workflow.is_file_already_processed(path):
                self._logger.debug(f"Queued processing completed in time for {path}")
                return True
            self._logger.warning(
                f"Queued processing for {path} did not finish before {trigger} timeout; "
                "continuing with inline processing"
            )

        return self._workflow.prepare_file_for_storage(
            normalized, trigger=trigger, skip_if_processed=True
        )

    def _run(self):
        while not self._stop_event.is_set():
            try:
                payload = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            path = payload.get("path")
            get_event_bus().publish(
                Event(EventType.FILE_PROCESSING_STARTED, payload=payload)
            )
            success = False
            try:
                success = self._workflow.prepare_file_for_storage(
                    payload,
                    trigger=EventType.FILE_QUEUED.value,
                    skip_if_processed=True,
                )
            except Exception as exc:
                self._logger.error(
                    f"Unhandled queue processing failure for {path}: {exc}",
                    exc_info=True,
                )
            finally:
                self._queue.task_done()
                self._mark_done(path)
                finish_payload = dict(payload)
                finish_payload["success"] = success
                get_event_bus().publish(
                    Event(EventType.FILE_PROCESSING_FINISHED, payload=finish_payload)
                )
                self._logger.debug(
                    f"Background processing finished for {path} success={success}"
                )

    def _get_pending_event(self, path):
        with self._lock:
            return self._pending_events.get(path)

    def _mark_done(self, path):
        if not path:
            return
        with self._lock:
            done_event = self._pending_events.pop(path, None)
        if done_event is not None:
            done_event.set()
