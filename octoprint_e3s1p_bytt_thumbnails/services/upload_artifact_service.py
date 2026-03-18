# coding=utf-8
"""Helpers for priming thumbnails and transfer sidecars during uploads."""


class UploadArtifactService:
    """Coordinates eager thumbnail/helper generation for newly saved uploads."""

    def __init__(self, logger, workflow, thumbnail_service, helper_file_service):
        self._logger = logger
        self._workflow = workflow
        self._thumbnail_service = thumbnail_service
        self._helper_file_service = helper_file_service

    def prime_uploaded_artifacts(
        self, payload, gcode_disk_path, activate_helper=False, should_print=False
    ):
        """Generate thumbnail artifacts immediately after OctoPrint saves a file."""
        if self._workflow is None:
            return

        context = self._workflow._build_file_context(payload)
        self._logger.debug(
            f"Priming thumbnail artifacts immediately after save for {context['path']} from {gcode_disk_path}"
        )

        thumbnail_extracted = self._thumbnail_service.extract_thumbnail(
            gcode_disk_path,
            context["thumbnail_disk_path"],
        )
        if thumbnail_extracted:
            try:
                self._workflow._set_thumbnail_metadata(
                    context["path"],
                    context["thumbnail_relative_path"],
                )
            except Exception as exc:
                self._logger.debug(
                    f"Thumbnail metadata was not ready yet for {context['path']}: {exc}"
                )
        else:
            self._workflow._cleanup_file_if_exists(
                context["thumbnail_disk_path"],
                "stale thumbnail preview",
            )

        helper_extracted = self._helper_file_service.extract_transfer_file(
            gcode_disk_path,
            context["thumbnail_sidecar_path"],
        )
        if not helper_extracted:
            self._workflow._cleanup_file_if_exists(
                context["thumbnail_sidecar_path"],
                "stale thumbnail sidecar",
            )

        self._workflow._mark_file_processed(context["path"])

        if activate_helper:
            self._workflow.note_post_helper_action(payload, should_print=should_print)
            self._logger.info(
                f"Upload requested immediate select/print; activating helper during save for {context['path']}"
            )
            self._workflow.activate_thumbnail_for_print(
                payload,
                trigger="file_preprocessor",
            )
