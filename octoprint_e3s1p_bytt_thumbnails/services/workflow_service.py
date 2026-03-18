# coding=utf-8
"""Workflow coordinator for plugin event handling."""

import datetime
import os
import threading
import time

try:
    from urllib import quote
except ImportError:
    from urllib.parse import quote


class WorkflowService:
    """Coordinates scanning, metadata generation, helper sync, and print events."""

    PROCESSED_HASH_METADATA_KEY = "e3s1p_bytt_processed_hash"
    HELPER_UPLOAD_DEDUPE_SECONDS = 5.0

    def __init__(
        self,
        logger,
        settings_plugin,
        file_manager,
        printer,
        plugin_data_folder_getter,
        plugin_identifier,
        helper_basename,
        regex_extension,
        thumbnail_service,
        helper_file_service,
        printer_sync_service_getter,
    ):
        self._logger = logger
        self._settings = settings_plugin
        self._file_manager = file_manager
        self._printer = printer
        self._get_plugin_data_folder = plugin_data_folder_getter
        self._plugin_identifier = plugin_identifier
        self._helper_basename = helper_basename
        self._regex_extension = regex_extension
        self._thumbnail_service = thumbnail_service
        self._helper_file_service = helper_file_service
        self._get_printer_sync_service = printer_sync_service_getter

        self.active_print_path = None
        self.active_print_name = None
        self.active_transfer_job_path = None
        self.active_transfer_sidecar_path = None
        self.active_helper_inflight = False
        self.suppress_next_file_selected_path = None
        self._last_helper_activation_path = None
        self._last_helper_activation_at = 0.0
        self._post_helper_action_path = None
        self._post_helper_should_print = False

    def scan_files(self):
        self._logger.debug("Scanning files")
        try:
            file_list = self._file_manager.list_files(recursive=True)
            local_files = file_list.get("local", {})
            results = {"no_thumbnail": [], "no_thumbnail_src": []}
            for file_key, file_data in local_files.items():
                if file_data.get("name", "").upper() == self._helper_basename:
                    self._logger.debug(
                        f"Skipping helper entry during scan: {file_data.get('name')}"
                    )
                    continue
                results = self.process_gcode(local_files[file_key], results)
            self._logger.debug(f"Scan complete with results: {results}")
            return results
        except Exception as exc:
            self._logger.error(f"Scan failed: {exc}", exc_info=True)
            return {"no_thumbnail": [], "no_thumbnail_src": [], "error": str(exc)}

    def process_gcode(self, gcode_file, results=None):
        if results is None:
            results = {"no_thumbnail": [], "no_thumbnail_src": []}

        if gcode_file.get("name", "").upper() == self._helper_basename:
            self._logger.debug(
                f"Skipping helper file during process_gcode: {gcode_file.get('name')}"
            )
            return results

        if gcode_file.get("type") == "machinecode":
            path = gcode_file.get("path")
            if not path or not self._regex_extension.search(path):
                self._logger.debug(
                    f"Skipping non-target machinecode entry: name={gcode_file.get('name')!r} "
                    f"path={path!r} type={gcode_file.get('type')!r}"
                )
                return results

            thumbnail_filename = self.thumbnail_output_path(path)
            if gcode_file.get("thumbnail") is None or not os.path.exists(
                thumbnail_filename
            ):
                self._logger.debug(
                    f"Thumbnail missing for {path}; triggering FileAdded processing"
                )
                results["no_thumbnail"].append(path)
                self.handle_file_added_or_removed(
                    "FileAdded",
                    {
                        "path": path,
                        "storage": "local",
                        "name": gcode_file.get("name", ""),
                    },
                )
            elif "e3s1p_bytt_thumbnails" in gcode_file.get(
                "thumbnail", ""
            ) and not gcode_file.get("thumbnail_src"):
                self._logger.debug(
                    f"thumbnail_src missing for {path}; restoring metadata"
                )
                results["no_thumbnail_src"].append(path)
                self._file_manager.set_additional_metadata(
                    "local",
                    path,
                    "thumbnail_src",
                    self._plugin_identifier,
                    overwrite=True,
                )
        elif (
            gcode_file.get("type") == "folder"
            and gcode_file.get("children") is not None
        ):
            self._logger.debug(
                f"Descending into folder during process_gcode: {gcode_file.get('path')}"
            )
            for key in gcode_file["children"]:
                self.process_gcode(gcode_file["children"][key], results)
        else:
            self._logger.debug(
                f"Skipping unsupported gcode entry type={gcode_file.get('type')!r} "
                f"name={gcode_file.get('name')!r} path={gcode_file.get('path')!r}"
            )

        return results

    def handle_folder_added(self, payload):
        self._logger.debug(f"Handling FolderAdded for path={payload.get('path')}")
        try:
            file_list = self._file_manager.list_files(
                path=payload["path"], recursive=True
            )
            local_files = file_list.get("local", {})
            results = {"no_thumbnail": [], "no_thumbnail_src": []}
            for file_key, file_data in local_files.items():
                results = self.process_gcode(local_files[file_key], results)
            self._logger.debug(f"FolderAdded scan results: {results}")
        except Exception as exc:
            self._logger.error(
                f"FolderAdded processing failed for {payload.get('path')}: {exc}",
                exc_info=True,
            )

    def handle_folder_removed(self, payload):
        import shutil

        target_path = os.path.join(
            self._get_plugin_data_folder(), payload.get("path", "")
        )
        self._logger.debug(f"Handling FolderRemoved for path={target_path}")
        try:
            shutil.rmtree(target_path, ignore_errors=True)
        except Exception as exc:
            self._logger.error(
                f"FolderRemoved cleanup failed for {target_path}: {exc}", exc_info=True
            )

    def handle_file_added_or_removed(self, event, payload):
        self._logger.debug(
            f"Workflow handling {event} for name={payload.get('name')!r} path={payload.get('path')!r}"
        )
        file_extension = os.path.splitext(payload["name"])[1].lower()
        if file_extension not in [".gcode", ".gco", ".tft"]:
            self._logger.debug(
                f"Ignoring {event} for unsupported extension {file_extension} on {payload.get('name')}"
            )
            return

        if event == "FileRemoved":
            self._cleanup_file_artifacts(payload["path"])
            return

        self.prepare_file_for_storage(payload, trigger=event, skip_if_processed=False)

    def handle_file_selected(self, payload):
        normalized = self.normalize_local_payload(payload, trigger="FileSelected")
        self._logger.debug(
            f"Workflow handling FileSelected for name={payload.get('name')!r} "
            f"path={payload.get('path')!r} normalized={normalized}"
        )

        if normalized is None:
            return False

        if (
            self.suppress_next_file_selected_path
            and normalized.get("path") == self.suppress_next_file_selected_path
        ):
            self._logger.debug(
                f"Suppressing self-triggered FileSelected for {payload.get('path')}"
            )
            self.suppress_next_file_selected_path = None
            return False

        if self.active_helper_inflight:
            self._logger.debug(
                f"Helper upload inflight; ignoring FileSelected for {normalized.get('name')}"
            )
            return False

        context = self._build_file_context(normalized)
        if self._has_prepared_helper_sidecar(context):
            self._logger.debug(
                f"Using pre-generated helper sidecar for {context['path']} during FileSelected"
            )
        else:
            if not self.prepare_file_for_storage(
                normalized, trigger="FileSelected", skip_if_processed=True
            ):
                return False
        return self.activate_thumbnail_for_print(normalized, trigger="FileSelected")

    def prepare_file_for_storage(self, payload, trigger, skip_if_processed=True):
        normalized = self.normalize_local_payload(payload, trigger=trigger)
        if normalized is None:
            self._logger.debug(
                f"Skipping prepare_file_for_storage for {trigger}; payload={payload}"
            )
            return False

        context = self._build_file_context(normalized)
        path = context["path"]
        if skip_if_processed and self.is_file_already_processed(path):
            self._logger.debug(f"Skipping {trigger} for already processed file {path}")
            return True

        self._logger.debug(
            f"Preparing stored artifacts for {path} via {trigger}: "
            f"gcode={context['gcode_disk_path']} thumbnail={context['thumbnail_disk_path']} "
            f"sidecar={context['thumbnail_sidecar_path']}"
        )

        if not self._refresh_thumbnail_preview(context, trigger):
            return False

        self._refresh_thumbnail_sidecar(context, trigger)
        self._mark_file_processed(path)
        return True

    def activate_thumbnail_for_print(self, payload, trigger):
        normalized = self.normalize_local_payload(payload, trigger=trigger)
        if normalized is None:
            self._logger.debug(
                f"Skipping activate_thumbnail_for_print for {trigger}; payload={payload}"
            )
            return False

        context = self._build_file_context(normalized)
        self._remember_active_print(context)

        if (
            self.active_helper_inflight
            and self.active_transfer_job_path != context["path"]
        ):
            self._logger.warning(
                f"Helper upload already inflight for {self.active_transfer_job_path}; "
                f"skipping activation for {context['path']}"
            )
            return False

        if self._should_skip_duplicate_helper_activation(context["path"], trigger):
            return False

        if not os.path.exists(context["thumbnail_sidecar_path"]):
            self._logger.debug(
                f"Thumbnail sidecar {context['thumbnail_sidecar_path']} missing for {context['path']}; "
                f"preparing inline during {trigger}"
            )
            if not self.prepare_file_for_storage(
                context, trigger=trigger, skip_if_processed=False
            ):
                return False
            if not os.path.exists(context["thumbnail_sidecar_path"]):
                self._logger.debug(
                    f"No thumbnail sidecar available for {context['path']} during {trigger}"
                )
                return True

        printer_sync_service = self._get_printer_sync_service()
        if printer_sync_service is None:
            self._logger.warning(
                f"Printer sync service is not ready; skipping helper upload for {context['path']}"
            )
            return False

        printer_sync_service.purge_uploads_helper(self._helper_basename)
        self._helper_file_service.filter_helper_file(context["thumbnail_sidecar_path"])

        self.active_transfer_job_path = context["path"]
        self.active_transfer_sidecar_path = context["thumbnail_sidecar_path"]
        self.active_helper_inflight = True
        self._mark_helper_activation_attempt(context["path"])
        self._logger.debug(
            f"Prepared helper upload for {context['job_key']} "
            f"from sidecar {context['thumbnail_sidecar_path']} via {trigger}"
        )

        if not printer_sync_service.send_helper_to_sd(
            context["thumbnail_sidecar_path"],
            self._helper_basename,
        ):
            self.active_helper_inflight = False
            self._logger.debug(
                f"Helper upload failed for {context['name']} during {trigger}"
            )
            return False

        self._logger.debug(
            f"Helper upload requested for {context['name']} during {trigger}"
        )
        return True

    def note_post_helper_action(self, payload, should_print=False):
        normalized = self.normalize_local_payload(payload, trigger="post_helper_action")
        if normalized is None:
            return

        self._post_helper_action_path = normalized["path"]
        self._post_helper_should_print = bool(should_print)
        self._logger.debug(
            f"Recorded post-helper action for {normalized['path']}: should_print={self._post_helper_should_print}"
        )

    def handle_helper_transfer_done(self, payload):
        self._logger.debug(f"Handling helper TransferDone with payload={payload}")
        printer_sync_service = self._get_printer_sync_service()
        if printer_sync_service is None:
            self._logger.warning(
                "TransferDone received but printer sync service is unavailable"
            )
            self.active_helper_inflight = False
            return

        printer_sync_service.purge_uploads_helper(
            self._helper_basename,
            payload.get("local"),
        )

        if self.active_transfer_job_path:
            path_select_file = self._file_manager.path_on_disk(
                "local", self.active_transfer_job_path
            )
            self.suppress_next_file_selected_path = self.active_transfer_job_path
            should_print_after_select = self._should_print_after_helper_transfer(
                self.active_transfer_job_path
            )
            self._logger.debug(
                f"Re-selecting original file after helper transfer: {path_select_file} "
                f"(print_after_select={should_print_after_select})"
            )
            try:
                self._printer.select_file(path_select_file, False, False)
                self._printer.commands("M19 S1 ; Update LCD")
                display_name = os.path.splitext(
                    os.path.basename(self.active_transfer_job_path)
                )[0]
                self._printer.commands("M117 {} ; Update LCD".format(display_name))
                if should_print_after_select:
                    self._start_print_after_helper_transfer(
                        self.active_transfer_job_path
                    )
            except Exception as exc:
                self._logger.error(
                    f"Failed to re-select original file {path_select_file} after helper transfer: {exc}",
                    exc_info=True,
                )
        else:
            self._logger.debug(
                "No active transfer job path stored at TransferDone time"
            )

        self.active_transfer_job_path = None
        self.active_transfer_sidecar_path = None
        self.active_helper_inflight = False
        self._clear_post_helper_action()
        self._logger.debug("Helper transfer handling complete")

    def handle_print_event(self, event, payload=None):
        if event == "PrintStarted":
            normalized = self.normalize_local_payload(payload, trigger=event)
            if normalized is not None:
                context = self._build_file_context(normalized)
                self._remember_active_print(context)
                self.activate_thumbnail_for_print(context, trigger=event)
        if not self.active_print_name:
            self._logger.debug(
                f"Ignoring print event {event} because no selected print filename is set"
            )
            return

        event_commands = {
            "PrintStarted": "M19 S3 ; Update LCD",
            "PrintResumed": "M19 S5 ; Update LCD",
            "PrintCancelled": "M19 S2 ; Update LCD",
            "PrintPaused": "M19 S4 ; Update LCD",
            "PrintDone": "M19 S6 ; Update LCD",
        }
        command = event_commands.get(event)
        if command:
            self._logger.debug(
                f"Sending print lifecycle command for {event}: {command}"
            )
            try:
                self._printer.commands(command)
            except Exception as exc:
                self._logger.error(
                    f"Failed sending printer command {command} for {event}: {exc}",
                    exc_info=True,
                )

        if event == "PrintStarted":
            display_name = os.path.splitext(os.path.basename(self.active_print_name))[0]
            display_command = "M117 {} ; Update LCD".format(display_name)
            self._logger.debug(
                f"Sending print start display command: {display_command}"
            )
            try:
                self._printer.commands(display_command)
            except Exception as exc:
                self._logger.error(
                    f"Failed sending display command for {self.active_print_name}: {exc}",
                    exc_info=True,
                )

        if event in ["PrintCancelled", "PrintDone"]:
            self.active_print_path = None
            self.active_print_name = None
            self._clear_helper_activation_cache()
            self._clear_post_helper_action()
            self._logger.debug(f"Cleared selected print state after {event}")

    def thumbnail_output_path(self, relative_gcode_path):
        relative_thumbnail_path = self._regex_extension.sub(".jpg", relative_gcode_path)
        if self._settings.get_boolean(["use_uploads_folder"]):
            return self._file_manager.path_on_disk("local", relative_thumbnail_path)
        return os.path.join(self._get_plugin_data_folder(), relative_thumbnail_path)

    def thumbnail_sidecar_path(self, relative_gcode_path):
        return self._file_manager.path_on_disk("local", relative_gcode_path) + ".thumb"

    def normalize_local_payload(self, payload, trigger="event"):
        payload = payload or {}
        raw_path = payload.get("path") or payload.get("filename") or payload.get("file")
        raw_name = payload.get("name")
        nested_file_payload = raw_path if isinstance(raw_path, dict) else None

        if isinstance(raw_path, dict):
            raw_name = raw_name or raw_path.get("name")
            raw_path = raw_path.get("path") or raw_path.get("name")
        if isinstance(raw_name, dict):
            raw_name = raw_name.get("name") or raw_name.get("path")

        if not raw_path and raw_name:
            raw_path = raw_name
        if not raw_name and raw_path:
            raw_name = os.path.basename(raw_path)

        storage = (
            payload.get("storage") or payload.get("origin") or payload.get("target")
        )
        if storage is None and nested_file_payload is not None:
            storage = (
                nested_file_payload.get("storage")
                or nested_file_payload.get("origin")
                or nested_file_payload.get("target")
            )
        if storage not in (None, "local"):
            self._logger.debug(
                f"Ignoring {trigger} because storage={storage!r} path={raw_path!r} name={raw_name!r}"
            )
            return None
        if not raw_path or not raw_name:
            self._logger.debug(
                f"Ignoring {trigger} because payload is missing path or name: {payload}"
            )
            return None
        if raw_name.upper() == self._helper_basename:
            self._logger.debug(f"Ignoring helper file for {trigger}: {raw_name}")
            return None

        file_extension = os.path.splitext(raw_name)[1].lower()
        if file_extension not in [".gcode", ".gco", ".tft"]:
            self._logger.debug(
                f"Ignoring {trigger} for unsupported extension {file_extension} on {raw_name}"
            )
            return None

        normalized = dict(payload)
        normalized["path"] = raw_path
        normalized["name"] = raw_name
        normalized["storage"] = storage or "local"
        return normalized

    def is_file_already_processed(self, path):
        current_hash = self._current_file_hash(path)
        if not current_hash:
            self._logger.debug(
                f"No current hash available to determine processing state for {path}"
            )
            return False

        try:
            metadata = self._file_manager.get_metadata("local", path) or {}
        except Exception as exc:
            self._logger.error(
                f"Failed reading metadata for processed check on {path}: {exc}",
                exc_info=True,
            )
            return False

        processed_hash = metadata.get(self.PROCESSED_HASH_METADATA_KEY)
        is_processed = processed_hash == current_hash
        self._logger.debug(
            f"Processed check for {path}: current_hash={current_hash!r} "
            f"processed_hash={processed_hash!r} is_processed={is_processed}"
        )
        return is_processed

    def _set_thumbnail_metadata(self, file_path, thumbnail_path):
        thumbnail_url = self.build_thumbnail_url(thumbnail_path)
        self._logger.debug(
            f"Setting thumbnail metadata for {file_path}: thumbnail={thumbnail_url} "
            f"thumbnail_src={self._plugin_identifier}"
        )
        self._file_manager.set_additional_metadata(
            "local",
            file_path,
            "thumbnail",
            thumbnail_url,
            overwrite=True,
        )
        self._file_manager.set_additional_metadata(
            "local",
            file_path,
            "thumbnail_src",
            self._plugin_identifier,
            overwrite=True,
        )
        self._log_thumbnail_metadata(file_path)

    def _log_thumbnail_metadata(self, file_path):
        try:
            metadata = self._file_manager.get_metadata("local", file_path)
            self._logger.debug(f"Metadata read-back for {file_path}: {metadata!r}")
        except Exception as exc:
            self._logger.error(
                f"Failed reading metadata back for {file_path}: {exc}", exc_info=True
            )

    def _remember_active_print(self, payload):
        self.active_print_path = payload["path"]
        self.active_print_name = payload["name"]
        self._logger.debug(
            f"Remembered active print file {self.active_print_name} "
            f"(path={self.active_print_path})"
        )

    def _build_file_context(self, payload):
        normalized = self.normalize_local_payload(payload, trigger="context") or payload
        path = normalized["path"]
        name = normalized["name"]
        return {
            "storage": normalized.get("storage", "local"),
            "path": path,
            "name": name,
            "job_key": f"{normalized.get('storage', 'local')}/{path}",
            "gcode_disk_path": self._file_manager.path_on_disk("local", path),
            "thumbnail_relative_path": self._regex_extension.sub(".jpg", path),
            "thumbnail_disk_path": self.thumbnail_output_path(path),
            "thumbnail_sidecar_path": self.thumbnail_sidecar_path(path),
        }

    def _refresh_thumbnail_preview(self, context, trigger):
        if os.path.exists(context["thumbnail_disk_path"]):
            try:
                os.remove(context["thumbnail_disk_path"])
                self._logger.debug(
                    f"Removed existing thumbnail {context['thumbnail_disk_path']} before {trigger}"
                )
            except OSError as exc:
                self._logger.error(
                    f"Failed removing thumbnail {context['thumbnail_disk_path']}: {exc}",
                    exc_info=True,
                )
                return False

        if not self._thumbnail_service.extract_thumbnail(
            context["gcode_disk_path"],
            context["thumbnail_disk_path"],
        ):
            self._logger.debug(
                f"No embedded thumbnail extracted for {context['path']} during {trigger}"
            )
            return True

        try:
            self._set_thumbnail_metadata(
                context["path"], context["thumbnail_relative_path"]
            )
            self._logger.debug(
                f"Updated thumbnail metadata for {context['path']} during {trigger}"
            )
            return True
        except Exception as exc:
            self._logger.error(
                f"Failed setting thumbnail metadata during {trigger} for {context['path']}: {exc}",
                exc_info=True,
            )
            return False

    def _refresh_thumbnail_sidecar(self, context, trigger):
        if os.path.exists(context["thumbnail_sidecar_path"]):
            try:
                os.remove(context["thumbnail_sidecar_path"])
                self._logger.debug(
                    f"Removed existing thumbnail sidecar {context['thumbnail_sidecar_path']} before {trigger}"
                )
            except OSError as exc:
                self._logger.error(
                    f"Failed removing thumbnail sidecar {context['thumbnail_sidecar_path']}: {exc}",
                    exc_info=True,
                )
                return

        if self._helper_file_service.extract_transfer_file(
            context["gcode_disk_path"],
            context["thumbnail_sidecar_path"],
        ):
            self._logger.debug(
                f"Updated thumbnail sidecar for {context['job_key']} at {context['thumbnail_sidecar_path']}"
            )
        else:
            self._logger.debug(
                f"No thumbnail sidecar extracted for {context['job_key']} during {trigger}"
            )
            self._cleanup_file_if_exists(
                context["thumbnail_sidecar_path"], "stale thumbnail sidecar"
            )

    def _cleanup_file_artifacts(self, relative_gcode_path):
        context = self._build_file_context(
            {
                "storage": "local",
                "path": relative_gcode_path,
                "name": os.path.basename(relative_gcode_path),
            }
        )
        self._cleanup_file_if_exists(
            context["thumbnail_disk_path"], "thumbnail preview"
        )
        self._cleanup_file_if_exists(
            context["thumbnail_sidecar_path"], "thumbnail sidecar"
        )
        if self.active_print_path == relative_gcode_path:
            self.active_print_path = None
            self.active_print_name = None
            self._clear_helper_activation_cache()
        if self.active_transfer_job_path == relative_gcode_path:
            self.active_transfer_job_path = None
            self.active_transfer_sidecar_path = None
            self.active_helper_inflight = False
        if self._post_helper_action_path == relative_gcode_path:
            self._clear_post_helper_action()

    def _cleanup_file_if_exists(self, path, label):
        if not os.path.exists(path):
            return
        try:
            os.remove(path)
            self._logger.debug(f"Removed {label} at {path}")
        except OSError as exc:
            self._logger.error(
                f"Failed removing {label} at {path}: {exc}", exc_info=True
            )

    def _mark_file_processed(self, path):
        current_hash = self._current_file_hash(path)
        if not current_hash:
            self._logger.debug(
                f"Skipping processed marker for {path} because no file hash is available"
            )
            return
        try:
            self._file_manager.set_additional_metadata(
                "local",
                path,
                self.PROCESSED_HASH_METADATA_KEY,
                current_hash,
                overwrite=True,
            )
            self._logger.debug(f"Marked {path} as processed with hash {current_hash}")
        except Exception as exc:
            self._logger.error(
                f"Failed setting processed marker for {path}: {exc}", exc_info=True
            )

    def _current_file_hash(self, path):
        try:
            metadata = self._file_manager.get_metadata("local", path) or {}
        except Exception as exc:
            self._logger.error(
                f"Failed reading metadata for {path}: {exc}", exc_info=True
            )
            return None
        current_hash = metadata.get("hash")
        self._logger.debug(f"Current file hash for {path} is {current_hash!r}")
        return current_hash

    def _should_skip_duplicate_helper_activation(self, path, trigger):
        now = time.monotonic()
        if (
            self._last_helper_activation_path == path
            and now - self._last_helper_activation_at
            < self.HELPER_UPLOAD_DEDUPE_SECONDS
        ):
            self._logger.debug(
                f"Skipping duplicate helper activation for {path} via {trigger}; "
                f"last attempt was {now - self._last_helper_activation_at:.2f}s ago"
            )
            return True
        return False

    def _mark_helper_activation_attempt(self, path):
        self._last_helper_activation_path = path
        self._last_helper_activation_at = time.monotonic()

    def _clear_helper_activation_cache(self):
        self._last_helper_activation_path = None
        self._last_helper_activation_at = 0.0

    def _should_print_after_helper_transfer(self, path):
        return self._post_helper_action_path == path and self._post_helper_should_print

    def _clear_post_helper_action(self):
        self._post_helper_action_path = None
        self._post_helper_should_print = False

    def _start_print_after_helper_transfer(self, path):
        def _runner():
            for attempt in range(10):
                time.sleep(0.2)
                if self.active_helper_inflight:
                    continue
                if (
                    self.active_print_path
                    and self.active_print_path != path
                    and self._printer.is_printing()
                ):
                    self._logger.debug(
                        f"Skipping delayed print start for {path}; another print is already active"
                    )
                    return
                try:
                    current_job = self._printer.get_current_job() or {}
                except Exception as exc:
                    self._logger.debug(
                        f"Failed reading current job before delayed print start: {exc}"
                    )
                    current_job = {}

                current_file = (current_job.get("file") or {}).get("path")
                if current_file != path:
                    self._logger.debug(
                        f"Delayed print start waiting for selected file {path}; current file is {current_file!r}"
                    )
                    continue
                if not self._printer.is_operational() or self._printer.is_printing():
                    self._logger.debug(
                        f"Delayed print start waiting for printer readiness on {path}: "
                        f"operational={self._printer.is_operational()} printing={self._printer.is_printing()}"
                    )
                    continue

                self._logger.info(
                    f"Starting original print after helper transfer for {path}"
                )
                try:
                    self._printer.start_print(
                        tags={"source:plugin", "plugin:e3s1p_bytt_thumbnails"}
                    )
                except Exception as exc:
                    self._logger.error(
                        f"Failed starting original print after helper transfer for {path}: {exc}"
                    )
                return

            self._logger.warning(
                f"Timed out waiting to start original print after helper transfer for {path}"
            )

        thread = threading.Thread(
            target=_runner,
            name="e3s1p-bytt-post-helper-print",
            daemon=True,
        )
        thread.start()

    @staticmethod
    def _has_prepared_helper_sidecar(context):
        return os.path.exists(context["thumbnail_sidecar_path"])

    @staticmethod
    def build_thumbnail_url(thumbnail_path):
        thumbnail_name = os.path.basename(thumbnail_path)
        quoted_thumbnail_path = thumbnail_path.replace(
            thumbnail_name, quote(thumbnail_name)
        )
        timestamp = datetime.datetime.now()
        return (
            f"plugin/e3s1p_bytt_thumbnails/thumbnail/{quoted_thumbnail_path}?{timestamp:%Y%m%d%H%M%S}"
        ).replace("//", "/")
