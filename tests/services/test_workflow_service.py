import logging
from unittest.mock import patch

from octoprint_e3s1p_bytt_thumbnails.services.workflow_service import WorkflowService


class FakeSettings:
    def get_boolean(self, _keys):
        return False


class FakeFileManager:
    def path_on_disk(self, storage, path):
        assert storage == "local"
        return f"/uploads/{path}"


class FakePrinterSyncService:
    def __init__(self):
        self.purged = []

    def purge_uploads_helper(self, helper_basename, hint_rel_path=None):
        self.purged.append((helper_basename, hint_rel_path))


class FakePrinter:
    def __init__(self, selected_path):
        self.selected_path = selected_path
        self.selected = []
        self.command_history = []
        self.start_print_calls = []

    def select_file(self, path, sd, print_after_select):
        self.selected.append((path, sd, print_after_select))

    def commands(self, command):
        self.command_history.append(command)

    def get_current_job(self):
        return {"file": {"path": self.selected_path}}

    def is_operational(self):
        return True

    def is_printing(self):
        return False

    def start_print(self, **kwargs):
        self.start_print_calls.append(kwargs)


class DeferredThread:
    pending_targets = []

    def __init__(self, target, name=None, daemon=None):
        self._target = target

    def start(self):
        self.pending_targets.append(self._target)


def build_workflow(printer, printer_sync_service):
    return WorkflowService(
        logger=logging.getLogger(__name__),
        settings_plugin=FakeSettings(),
        file_manager=FakeFileManager(),
        printer=printer,
        plugin_data_folder_getter=lambda: "/plugin-data",
        plugin_identifier="e3s1p_bytt_thumbnails",
        helper_basename="OCTODGUS.GCO",
        regex_extension=__import__("re").compile(r"\.(?:gco(?:de)?|tft)$"),
        thumbnail_service=object(),
        helper_file_service=object(),
        printer_sync_service_getter=lambda: printer_sync_service,
    )


def test_handle_helper_transfer_done_restores_selection_and_starts_print():
    printer = FakePrinter(selected_path="prints/example.gcode")
    printer_sync_service = FakePrinterSyncService()
    workflow = build_workflow(printer, printer_sync_service)
    workflow.active_transfer_job_path = "prints/example.gcode"
    workflow.active_helper_inflight = True
    workflow.note_post_helper_action(
        {"path": "prints/example.gcode", "name": "example.gcode", "storage": "local"},
        should_print=True,
    )

    with (
        patch(
            "octoprint_e3s1p_bytt_thumbnails.services.workflow_service.threading.Thread",
            DeferredThread,
        ),
        patch(
            "octoprint_e3s1p_bytt_thumbnails.services.workflow_service.time.sleep",
            lambda _x: None,
        ),
    ):
        workflow.handle_helper_transfer_done({"local": "OCTODGUS.GCO"})
        while DeferredThread.pending_targets:
            DeferredThread.pending_targets.pop(0)()

    assert printer_sync_service.purged == [("OCTODGUS.GCO", "OCTODGUS.GCO")]
    assert printer.selected == [("/uploads/prints/example.gcode", False, False)]
    assert printer.command_history == [
        "M19 S1 ; Update LCD",
        "M117 example ; Update LCD",
    ]
    assert len(printer.start_print_calls) == 1
    assert workflow.active_helper_inflight is False
    assert workflow.active_transfer_job_path is None


def test_handle_helper_transfer_done_restores_selection_without_starting_print():
    printer = FakePrinter(selected_path="prints/example.gcode")
    printer_sync_service = FakePrinterSyncService()
    workflow = build_workflow(printer, printer_sync_service)
    workflow.active_transfer_job_path = "prints/example.gcode"
    workflow.active_helper_inflight = True
    workflow.note_post_helper_action(
        {"path": "prints/example.gcode", "name": "example.gcode", "storage": "local"},
        should_print=False,
    )

    with (
        patch(
            "octoprint_e3s1p_bytt_thumbnails.services.workflow_service.threading.Thread",
            DeferredThread,
        ),
        patch(
            "octoprint_e3s1p_bytt_thumbnails.services.workflow_service.time.sleep",
            lambda _x: None,
        ),
    ):
        workflow.handle_helper_transfer_done({"local": "OCTODGUS.GCO"})
        while DeferredThread.pending_targets:
            DeferredThread.pending_targets.pop(0)()

    assert printer.selected == [("/uploads/prints/example.gcode", False, False)]
    assert printer.command_history == [
        "M19 S1 ; Update LCD",
        "M117 example ; Update LCD",
    ]
    assert printer.start_print_calls == []
