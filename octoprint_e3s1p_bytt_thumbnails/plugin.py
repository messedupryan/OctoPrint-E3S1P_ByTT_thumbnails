# coding=utf-8
from __future__ import absolute_import

import logging
import re
import socket

import flask
import octoprint.filemanager.util
import octoprint.plugin
from octoprint.access import ADMIN_GROUP
from octoprint.access.permissions import Permissions

from .events import Event, EventType, get_event_bus
from .handlers import (
    FileMetadataEventHandler,
    FileSelectionEventHandler,
    FolderEventHandler,
    PrintLifecycleEventHandler,
    SettingsUpdateHandler,
    UploadEventHandler,
    UploadProcessingEventHandler,
)
from .plugin_config import (
    DEFAULT_SETTINGS,
    DEFAULT_TIMEOUT,
    HELPER_BASENAME,
    PERMISSION_SCAN,
    PLUGIN_NAME,
    PLUGIN_VERSION,
)
from .services import (
    HelperFileService,
    PrinterSyncService,
    ThumbnailService,
    UploadArtifactService,
    UploadIntentService,
    UploadProcessingService,
    WorkflowService,
)

socket.setdefaulttimeout(DEFAULT_TIMEOUT)


class _PostSaveArtifactWrapper(octoprint.filemanager.util.AbstractFileWrapper):
    """Run plugin artifact generation immediately after OctoPrint saves an upload."""

    def __init__(self, wrapped, after_save, logger):
        super().__init__(wrapped.filename)
        self._wrapped = wrapped
        self._after_save = after_save
        self._logger = logger

    def save(self, path, permissions=None):
        self._wrapped.save(path, permissions=permissions)
        try:
            self._after_save(path)
        except Exception as exc:
            self._logger.error(
                f"Post-save artifact generation failed for {path}: {exc}", exc_info=True
            )

    def stream(self):
        return self._wrapped.stream()

    def __getattr__(self, item):
        return getattr(self._wrapped, item)


class E3s1p_bytt_thumbnailsPlugin(
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.EventHandlerPlugin,
    octoprint.printer.PrinterCallback,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.ShutdownPlugin,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.TemplatePlugin,
):
    def __init__(self):
        super().__init__()
        self._logger = logging.getLogger("octoprint.plugins.e3s1p_bytt_thumbnails")

        self.file_scanner = None
        self.regex_extension = re.compile(r"\.(?:gco(?:de)?|tft)$")
        self._thumbnail_service = ThumbnailService(self._logger)
        self._helper_file_service = HelperFileService(self._logger)
        self._printer_sync_service = None
        self._workflow = None
        self._upload_artifact_service = None
        self._upload_processing_service = None
        self._event_handlers = []
        self._helper_basename = HELPER_BASENAME

    ## OctoPrint Mixin hooks ##
    """-- This section is for any mixin implementation related to OctoPrint's plugin system. --"""

    # Octoprint Backup Hook
    def additional_backup_excludes(self, excludes, *args, **kwargs):
        """Return backup excludes."""
        if "uploads" in excludes:
            return ["."]
        return []

    # OctoPrint Access Permissions Hook
    def get_additional_permissions(self, *args, **kwargs):
        from flask_babel import gettext

        """Return additional permissions."""
        return [
            {
                "key": PERMISSION_SCAN,
                "name": "Scan Files",
                "description": gettext("Allows access to scan files."),
                "roles": ["admin"],
                "dangerous": True,
                "default_groups": [ADMIN_GROUP],
            }
        ]

    # OctoPrint SimpleApiPlugin mixin
    def on_api_command(self, command, data):
        """Handle API commands."""
        if not Permissions.PLUGIN_E3S1P_BYTT_THUMBNAILS_SCAN.can():
            return flask.make_response("Insufficient rights", 403)

        if command == "crawl_files":
            return flask.jsonify(self.scan_files())

        return flask.make_response("Unknown command", 400)

    def get_api_commands(self):
        """Return API commands."""
        return dict(crawl_files=[])

    # OctoPrint AssetPlugin mixin
    def get_assets(self):
        return {
            "js": ["js/e3s1p_bytt_thumbnails.js"],
            "css": ["css/e3s1p_bytt_thumbnails.css"],
        }

    # Octoprint ExtentionTreePlugin mixin
    def get_extension_tree(self, *args, **kwargs):
        """Return extension tree."""
        return dict(machinecode=dict(gcode=["txt"]))

    # OctoPrint SettingsPlugin mixin
    def get_settings_defaults(self):
        """Return default settings."""
        return DEFAULT_SETTINGS

    def on_settings_save(self, data):
        super(E3s1p_bytt_thumbnailsPlugin, self).on_settings_save(data)

    # OctoPrint TemplatePlugin mixin
    def get_template_configs(self):
        """Return template configurations for settings UI."""
        return [
            {
                "type": "settings",
                "template": "e3s1p_bytt_thumbnals_settings.jinja2",
                "custom_bindings": False,
            },
            {
                "type": "generic",
                "template": "e3s1p_bytt_thumbnals.jinja2",
                "custom_bindings": True,
            },
        ]

    # OctoPrint Softwareupdate hook
    def get_update_information(self):
        return {
            "e3s1p_bytt_thumbnails": {
                "displayName": PLUGIN_NAME,
                "displayVersion": PLUGIN_VERSION,
                "type": "github_release",
                "user": "messedupRyan",
                "repo": "OctoPrint-E3S1P_ByTT_thumbnails",
                "current": PLUGIN_VERSION,
                "stable_branch": {
                    "name": "Stable",
                    "branch": "master",
                    "comittish": ["master"],
                },
                "prerelease_branches": [
                    {
                        "name": "Release Candidate",
                        "branch": "rc",
                        "comittish": ["rc", "master"],
                    }
                ],
                "pip": "https://github.com/messedupRyan/OctoPrint-E3S1P_ByTT_thumbnails/archive/{target_version}.zip",
            }
        }

    # Block Event (for startup hook)
    def hook_octoprint_server_api_before_request(self, *args, **kwargs):
        """Hook for API before request."""
        return [self.update_file_list]

    def hook_octoprint_filemanager_preprocessor(
        self,
        path,
        file_object,
        links=None,
        printer_profile=None,
        allow_overwrite=False,
        *args,
        **kwargs,
    ):
        """Generate helper artifacts as part of upload save to beat select/print races."""
        if self._workflow is None or file_object is None:
            return file_object

        normalized = self._workflow.normalize_local_payload(
            {"path": path, "name": file_object.filename, "storage": "local"},
            trigger="file_preprocessor",
        )
        if normalized is None:
            return file_object

        self._logger.debug(
            f"Wrapping uploaded file for post-save artifact generation: {normalized.get('path')}"
        )
        return _PostSaveArtifactWrapper(
            file_object,
            lambda disk_path: self._prime_uploaded_artifacts(normalized, disk_path),
            self._logger,
        )

    # OctoPrint Routes Hook
    def route_hook(self, server_routes, *args, **kwargs):
        """Register routes."""
        from octoprint.server.util.tornado import (
            LargeResponseHandler,
            path_validation_factory,
        )
        from octoprint.util import is_hidden_path

        thumbnail_root_path = (
            self._file_manager.path_on_disk("local", "")
            if self._settings.get_boolean(["use_uploads_folder"])
            else self.get_plugin_data_folder()
        )
        self._logger.debug(
            f"Registering thumbnail route with root path {thumbnail_root_path}"
        )
        return [
            (
                r"thumbnail/(.*)",
                LargeResponseHandler,
                {
                    "path": thumbnail_root_path,
                    "as_attachment": False,
                    "path_validation": path_validation_factory(
                        lambda path: not is_hidden_path(path), status_code=404
                    ),
                },
            )
        ]

    ## End of OctoPrint Mixin hooks ##

    # Handle Octoprint Events
    def on_event(self, event, payload):
        """Handle OctoPrint events."""
        payload = payload or {}
        self._logger.debug(f"{PLUGIN_NAME} handling event: {event}")
        event_type = EventType.from_octoprint(event)
        if event_type is None:
            return
        get_event_bus().publish(Event(event_type, payload=payload))

    def on_after_startup(self):
        self._printer_sync_service = PrinterSyncService(
            self._logger,
            self._printer,
            self._file_manager,
        )
        self._workflow = WorkflowService(
            logger=self._logger,
            settings_plugin=self._settings,
            file_manager=self._file_manager,
            printer=self._printer,
            plugin_data_folder_getter=self.get_plugin_data_folder,
            plugin_identifier=self._identifier,
            helper_basename=self._helper_basename,
            regex_extension=self.regex_extension,
            thumbnail_service=self._thumbnail_service,
            helper_file_service=self._helper_file_service,
            printer_sync_service_getter=lambda: self._printer_sync_service,
        )
        self._upload_processing_service = UploadProcessingService(
            logger=self._logger,
            workflow=self._workflow,
        )
        self._upload_artifact_service = UploadArtifactService(
            logger=self._logger,
            workflow=self._workflow,
            thumbnail_service=self._thumbnail_service,
            helper_file_service=self._helper_file_service,
        )
        self._upload_processing_service.start()
        self._register_event_handlers()

    def on_shutdown(self):
        for handler in self._event_handlers:
            handler.unregister()
        self._event_handlers = []
        if self._upload_processing_service is not None:
            self._upload_processing_service.stop()
        get_event_bus().clear()

    def update_file_list(self):
        if (
            self._settings.get_boolean(["sync_on_refresh"])
            and flask.request.path.startswith("/api/files")
            and flask.request.method == "GET"
            and not self.file_scanner
        ):
            from threading import Thread

            self.file_scanner = Thread(target=self.scan_files, daemon=True)
            self.file_scanner.start()

    def _register_event_handlers(self):
        get_event_bus().clear()
        self._event_handlers = [
            SettingsUpdateHandler(self),
            FolderEventHandler(self),
            FileMetadataEventHandler(self),
            FileSelectionEventHandler(self),
            UploadEventHandler(self),
            UploadProcessingEventHandler(self),
            PrintLifecycleEventHandler(self),
        ]

    def scan_files(self):
        results = (
            self._workflow.scan_files()
            if self._workflow is not None
            else {"no_thumbnail": [], "no_thumbnail_src": []}
        )
        self.file_scanner = None
        return results

    def _prime_uploaded_artifacts(self, payload, gcode_disk_path):
        if self._upload_artifact_service is None:
            return

        if not flask.has_request_context():
            self._upload_artifact_service.prime_uploaded_artifacts(
                payload, gcode_disk_path
            )
            return

        request_values = flask.request.values
        self._upload_artifact_service.prime_uploaded_artifacts(
            payload,
            gcode_disk_path,
            activate_helper=UploadIntentService.wants_immediate_select_or_print(
                request_values
            ),
            should_print=UploadIntentService.wants_immediate_print(request_values),
        )
