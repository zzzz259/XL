import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.features.preview.controller import PreviewController
from app.features.preview.export_plan import ExportSettings
from app.features.preview.page import PreviewPage
from app.features.preview.resource_model import SpineSkinRecord
from app.features.preview.resource_catalog import discover_preview_resources
from app.features.preview.service import PreviewService


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def ready_record(name="holiday", role="10080"):
    return SpineSkinRecord(
        character_id=role,
        source_skel=f"assets/{role}.skel",
        atlas_path=f"assets/{role}.atlas",
        skin_name=name,
        attachment_fingerprint=f"fp-{name}",
        display_name=f"显示 {name}",
        status="ready",
    )


class RecordingRunner:
    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def __call__(self, job):
        self.calls.append(job)
        job.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.result:
            job.output_path.write_bytes(b"png")
        return self.result


def make_controller(tmp_path, qapp):
    page = PreviewPage()
    service = PreviewService(tmp_path / "material", tmp_path / "output" / "character")
    controller = PreviewController(page, service)
    return controller, page


def test_selected_export_builds_identity_jobs_and_restores_ui_on_success(tmp_path, qapp):
    controller, page = make_controller(tmp_path, qapp)
    runner = RecordingRunner()
    statuses = []
    controller.status_changed.connect(statuses.append)
    records = (ready_record(),)

    assert controller.start_selected_export(
        records, settings=ExportSettings(animation="walk"), runner=runner
    ) is True
    assert controller.start_selected_export(
        records, settings=ExportSettings(), runner=runner
    ) is False
    assert controller._selected_export_worker.wait(5000)
    qapp.processEvents()

    assert [job.record for job in runner.calls] == list(records)
    assert runner.calls[0].settings.animation == "walk"
    assert "composite" not in str(runner.calls[0].output_path)
    assert page.btn_reload.text() == "重新加载图片"
    assert page.btn_reload.isEnabled()
    assert any("1 succeeded" in status or "完成" in status for status in statuses)

    page.close()


def test_selected_export_reports_failure_and_restores_ui(tmp_path, qapp):
    controller, page = make_controller(tmp_path, qapp)
    statuses = []
    controller.status_changed.connect(statuses.append)

    assert controller.start_selected_export(
        (ready_record(),), settings=ExportSettings(), runner=RecordingRunner(False)
    ) is True
    assert controller._selected_export_worker.wait(5000)
    qapp.processEvents()

    assert page.btn_reload.text() == "重新加载图片"
    assert any("failed" in status.lower() or "失败" in status for status in statuses)
    page.close()


def test_selected_export_cancel_stops_worker_and_restores_ui(tmp_path, qapp):
    controller, page = make_controller(tmp_path, qapp)
    entered = threading.Event()
    release = threading.Event()

    def runner(job):
        entered.set()
        release.wait(5)
        job.output_path.parent.mkdir(parents=True, exist_ok=True)
        job.output_path.write_bytes(b"png")
        return True

    assert controller.start_selected_export(
        (ready_record(),), settings=ExportSettings(), runner=runner
    ) is True
    assert entered.wait(5)
    assert page.btn_reload.text() == "取消导出"
    release.set()
    controller.cancel_export()
    qapp.processEvents()

    assert controller._selected_export_worker is None
    assert page.btn_reload.text() == "重新加载图片"
    page.close()


def test_selected_export_empty_selection_does_not_start_worker(tmp_path, qapp):
    controller, page = make_controller(tmp_path, qapp)
    assert controller.start_selected_export((), settings=ExportSettings(), runner=RecordingRunner()) is False
    assert controller._selected_export_worker is None
    page.close()


def test_cancelled_settings_dialog_does_not_start_worker(tmp_path, qapp, monkeypatch):
    controller, page = make_controller(tmp_path, qapp)

    class CancelledDialog:
        def __init__(self, *_args, **_kwargs):
            self.started = False

        def exec(self):
            return 0

        def settings(self):
            self.started = True
            raise AssertionError("cancelled dialog must not read settings")

    monkeypatch.setattr(
        "app.features.preview.controller.ExportSettingsDialog", CancelledDialog
    )

    assert controller.start_selected_export((ready_record(),), runner=RecordingRunner()) is False
    assert controller._selected_export_worker is None
    page.close()


def test_discover_resources_refreshes_spine_and_game_material_views(tmp_path, qapp, monkeypatch):
    controller, page = make_controller(tmp_path, qapp)
    spine_catalog = discover_preview_resources(tmp_path / "missing-material")
    material_catalog = controller.service.discover_game_materials()
    exported = []

    monkeypatch.setattr(controller.service, "discover_preview_resources", lambda: spine_catalog)
    monkeypatch.setattr(controller.service, "discover_game_materials", lambda: material_catalog)
    monkeypatch.setattr(
        controller.service,
        "export_game_materials",
        lambda catalog=None: exported.append(catalog) or type("Summary", (), {"exported": 1, "failed": 0, "diagnostics": ()})(),
    )

    result = controller.discover_resources()

    assert result == spine_catalog
    assert exported == [material_catalog]
    assert page.spine_tree.topLevelItemCount() == 0
    assert page.material_tree.topLevelItemCount() == 0
    page.close()
