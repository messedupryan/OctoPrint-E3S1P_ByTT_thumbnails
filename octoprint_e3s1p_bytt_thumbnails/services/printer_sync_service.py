# coding=utf-8
"""Utilities for syncing the helper file with printer SD."""

import os
import time


class PrinterSyncService:
    """Handles helper upload and cleanup using OctoPrint internals."""

    def __init__(self, logger, printer, file_manager):
        self._logger = logger
        self._printer = printer
        self._file_manager = file_manager

    def send_helper_to_sd(self, helper_path, helper_basename):
        """Upload the helper file to printer SD via OctoPrint internals."""
        if self._printer is None:
            self._logger.warning("Cannot upload helper because printer is unavailable")
            return False
        if not os.path.exists(helper_path):
            self._logger.warning(
                f"Cannot upload helper because sidecar does not exist: {helper_path}"
            )
            return False
        if (
            not self._printer.is_operational()
            or self._printer.is_printing()
            or self._printer.is_paused()
        ):
            self._logger.warning(
                "Cannot upload helper because printer is not operational or is already busy"
            )
            return False
        if not self._printer.is_sd_ready():
            self._logger.warning("Cannot upload helper because printer SD is not ready")
            return False

        self._logger.debug(
            f"Uploading helper {helper_basename} from {helper_path} via printer.add_sd_file"
        )

        try:
            self.delete_existing_sd_helper(helper_basename)
            remote_name = self._printer.add_sd_file(
                helper_basename,
                helper_path,
                tags={
                    "source:plugin",
                    "plugin:e3s1p_bytt_thumbnails",
                    "trigger:thumbnail_helper_sync",
                },
            )
            self._logger.debug(f"Helper upload started with remote name {remote_name}")
            return remote_name is not None
        except Exception as exc:
            self._logger.error(
                f"Failed to upload helper to printer SD: {exc}", exc_info=True
            )
            return False

    def purge_uploads_helper(self, helper_basename, hint_rel_path=None):
        """Remove helper copies that may have appeared in local uploads."""
        self._logger.debug(
            f"Purging uploads helper basename={helper_basename} hint_rel_path={hint_rel_path}"
        )
        try:
            if hint_rel_path:
                try:
                    if os.path.basename(hint_rel_path).upper() == helper_basename:
                        self._file_manager.remove_file("local", hint_rel_path)
                        self._logger.debug(
                            f"Removed helper via file manager: {hint_rel_path}"
                        )
                        return
                except Exception as exc:
                    self._logger.debug(
                        f"file_manager.remove_file failed for {hint_rel_path}: {exc}"
                    )

            self._delete_local_helper(helper_basename)
        except Exception as exc:
            self._logger.error(f"Purge uploads helper failed: {exc}", exc_info=True)

    def delete_existing_sd_helper(self, helper_basename):
        """Delete an existing helper file on SD if it is present."""
        if self._printer is None or not self._printer.is_sd_ready():
            return
        try:
            sd_files = self._printer.get_sd_files(refresh=True) or []
            helper_exists = any(
                (entry.get("name", "") or "").upper() == helper_basename
                for entry in sd_files
            )
            if not helper_exists:
                self._logger.debug(
                    f"No existing SD helper named {helper_basename} found"
                )
                return
            if not self._printer.can_modify_file(helper_basename, True):
                self._logger.warning(
                    f"Existing SD helper {helper_basename} cannot be modified right now"
                )
                return
            self._logger.debug(f"Deleting existing SD helper {helper_basename}")
            self._printer.delete_sd_file(
                helper_basename,
                tags={
                    "source:plugin",
                    "plugin:e3s1p_bytt_thumbnails",
                    "trigger:thumbnail_helper_cleanup",
                },
            )

            for _ in range(10):
                time.sleep(0.2)
                sd_files = self._printer.get_sd_files(refresh=True) or []
                if not any(
                    (entry.get("name", "") or "").upper() == helper_basename
                    for entry in sd_files
                ):
                    self._logger.debug(
                        f"Confirmed SD helper {helper_basename} was deleted"
                    )
                    return

            self._logger.warning(
                f"Timed out waiting for SD helper {helper_basename} to disappear before re-upload"
            )
        except Exception as exc:
            self._logger.error(
                f"Failed deleting existing SD helper {helper_basename}: {exc}",
                exc_info=True,
            )

    def _delete_local_helper(self, helper_basename):
        try:
            root = self._file_manager.path_on_disk("local", "")
            self._logger.debug(f"Scanning local uploads for helper cleanup in {root}")
            for entry in os.listdir(root):
                if entry.upper() == helper_basename:
                    try:
                        os.remove(os.path.join(root, entry))
                        self._logger.debug(f"Removed uploads/{entry}")
                    except Exception as exc:
                        self._logger.debug(f"Failed removing uploads/{entry}: {exc}")
        except Exception as exc:
            self._logger.error(f"Local helper cleanup failed: {exc}", exc_info=True)
