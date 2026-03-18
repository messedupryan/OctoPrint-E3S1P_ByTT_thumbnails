import logging

from octoprint_e3s1p_bytt_thumbnails.services.upload_artifact_service import (
    UploadArtifactService,
)


class DummyWorkflow:
    def __init__(self):
        self.context = {
            "path": "prints/example.gcode",
            "thumbnail_disk_path": "/tmp/example.jpg",
            "thumbnail_relative_path": "prints/example.jpg",
            "thumbnail_sidecar_path": "/tmp/example.gcode.thumb",
        }
        self.metadata = []
        self.cleaned = []
        self.marked = []
        self.post_actions = []
        self.activations = []

    def _build_file_context(self, payload):
        return self.context

    def _set_thumbnail_metadata(self, path, thumbnail_relative_path):
        self.metadata.append((path, thumbnail_relative_path))

    def _cleanup_file_if_exists(self, path, label):
        self.cleaned.append((path, label))

    def _mark_file_processed(self, path):
        self.marked.append(path)

    def note_post_helper_action(self, payload, should_print=False):
        self.post_actions.append((payload, should_print))

    def activate_thumbnail_for_print(self, payload, trigger):
        self.activations.append((payload, trigger))


class DummyThumbnailService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def extract_thumbnail(self, gcode_disk_path, thumbnail_disk_path):
        self.calls.append((gcode_disk_path, thumbnail_disk_path))
        return self.result


class DummyHelperFileService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def extract_transfer_file(self, gcode_disk_path, thumbnail_sidecar_path):
        self.calls.append((gcode_disk_path, thumbnail_sidecar_path))
        return self.result


def test_prime_uploaded_artifacts_generates_files_and_triggers_immediate_activation():
    workflow = DummyWorkflow()
    thumbnail_service = DummyThumbnailService(result=True)
    helper_service = DummyHelperFileService(result=True)
    service = UploadArtifactService(
        logging.getLogger(__name__),
        workflow,
        thumbnail_service,
        helper_service,
    )
    payload = {
        "path": "prints/example.gcode",
        "name": "example.gcode",
        "storage": "local",
    }

    service.prime_uploaded_artifacts(
        payload,
        "/uploads/prints/example.gcode",
        activate_helper=True,
        should_print=True,
    )

    assert thumbnail_service.calls == [
        ("/uploads/prints/example.gcode", "/tmp/example.jpg")
    ]
    assert helper_service.calls == [
        ("/uploads/prints/example.gcode", "/tmp/example.gcode.thumb")
    ]
    assert workflow.metadata == [("prints/example.gcode", "prints/example.jpg")]
    assert workflow.marked == ["prints/example.gcode"]
    assert workflow.post_actions == [(payload, True)]
    assert workflow.activations == [(payload, "file_preprocessor")]


def test_prime_uploaded_artifacts_cleans_up_missing_outputs():
    workflow = DummyWorkflow()
    thumbnail_service = DummyThumbnailService(result=False)
    helper_service = DummyHelperFileService(result=False)
    service = UploadArtifactService(
        logging.getLogger(__name__),
        workflow,
        thumbnail_service,
        helper_service,
    )

    service.prime_uploaded_artifacts(
        {"path": "prints/example.gcode", "name": "example.gcode", "storage": "local"},
        "/uploads/prints/example.gcode",
    )

    assert workflow.cleaned == [
        ("/tmp/example.jpg", "stale thumbnail preview"),
        ("/tmp/example.gcode.thumb", "stale thumbnail sidecar"),
    ]
    assert workflow.marked == ["prints/example.gcode"]
    assert workflow.post_actions == []
    assert workflow.activations == []
