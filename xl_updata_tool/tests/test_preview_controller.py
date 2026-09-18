import os
import threading
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem, QDialog

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


def test_builtin_selection_expands_cardspine_and_battlespine_jobs(tmp_path, qapp, monkeypatch):
    controller, page = make_controller(tmp_path, qapp)
    card = replace(
        ready_record(name="default"),
        source_skel="assets/cardspine_10080_4.skel",
        atlas_path="assets/cardspine_10080_4.atlas",
        resource_family="cardspine",
    )
    background = replace(
        card,
        source_skel="assets/cardspine_10080_4_bg.skel",
        atlas_path="assets/cardspine_10080_4_bg.atlas",
        attachment_fingerprint="background",
    )
    battle = replace(
        ready_record(name="default", role="10080"),
        source_skel="assets/battlespine_10080_2.skel",
        atlas_path="assets/battlespine_10080_2.atlas",
        resource_family="battlespine",
    )

    class BuiltinDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def export_mode(self):
            return "builtin"

    monkeypatch.setattr(
        "app.features.preview.controller.ExportSettingsDialog", BuiltinDialog
    )
    runner = RecordingRunner()

    assert controller.start_selected_export(
        ((card, background), (battle,)), runner=runner
    ) is True
    assert controller._selected_export_worker.wait(5000)
    qapp.processEvents()

    assert [(job.settings.format, job.settings.static) for job in runner.calls] == [
        ("Png", True),
        ("Mp4", False),
        ("Png", True),
    ]
    assert runner.calls[0].records == (card, background)
    assert runner.calls[1].records == (card, background)
    assert runner.calls[2].settings.skins == ("motion_stander",)
    page.close()


def test_custom_selection_rejects_multiple_visible_skins(tmp_path, qapp, monkeypatch):
    controller, page = make_controller(tmp_path, qapp)
    first = replace(
        ready_record(name="one"),
        source_skel="assets/cardspine_10080_1.skel",
        atlas_path="assets/cardspine_10080_1.atlas",
        resource_family="cardspine",
    )
    second = replace(
        ready_record(name="two"),
        source_skel="assets/cardspine_10080_2.skel",
        atlas_path="assets/cardspine_10080_2.atlas",
        resource_family="cardspine",
    )

    class CustomDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def export_mode(self):
            return "custom"

        def settings(self):
            raise AssertionError("multiple custom selection must be rejected first")

    monkeypatch.setattr(
        "app.features.preview.controller.ExportSettingsDialog", CustomDialog
    )

    assert controller.start_selected_export(
        ((first,), (second,)), runner=RecordingRunner()
    ) is False
    assert controller._selected_export_worker is None
    page.close()


def test_custom_selection_keeps_legacy_single_format_export(tmp_path, qapp, monkeypatch):
    controller, page = make_controller(tmp_path, qapp)
    record = replace(
        ready_record(name="custom"),
        source_skel="assets/cardspine_10080_4.skel",
        atlas_path="assets/cardspine_10080_4.atlas",
        resource_family="cardspine",
    )

    class CustomDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def export_mode(self):
            return "custom"

        def settings(self):
            return ExportSettings(format="Png", static=True, skins=("default",))

    monkeypatch.setattr(
        "app.features.preview.controller.ExportSettingsDialog", CustomDialog
    )
    runner = RecordingRunner()

    assert controller.start_selected_export(((record,),), runner=runner) is True
    assert controller._selected_export_worker.wait(5000)
    qapp.processEvents()

    assert len(runner.calls) == 1
    assert runner.calls[0].settings.format == "Png"
    page.close()


def test_export_record_grouping_combines_internal_skins_and_background(qapp, tmp_path):
    controller, page = make_controller(tmp_path, qapp)
    character = replace(
        ready_record(),
        source_skel="assets/cardspine_10080_4.skel",
        atlas_path="assets/cardspine_10080_4.atlas",
        resource_family="cardspine",
        skin_name="default",
    )
    motion = replace(character, skin_name="motion_angry")
    background = replace(
        character,
        source_skel="assets/cardspine_10080_4_bg.skel",
        atlas_path="assets/cardspine_10080_4_bg.atlas",
        skin_name="default",
    )

    groups = controller._group_export_records((character, motion, background))

    assert groups == ((character, motion, background),)
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


def test_preview_context_menu_has_no_export_actions(tmp_path, qapp, monkeypatch):
    controller, page = make_controller(tmp_path, qapp)
    png_path = tmp_path / "preview.png"
    png_path.write_bytes(b"png")
    item = QListWidgetItem("preview")
    item.setData(
        Qt.UserRole,
        {"png": str(png_path), "skel": str(tmp_path / "hero.skel"), "atlas": str(tmp_path / "hero.atlas")},
    )
    page.image_list.addItem(item)
    item.setSelected(True)

    class RecordingMenu:
        labels = []

        def __init__(self, *_args, **_kwargs):
            self.actions = []

        def setObjectName(self, _name):
            pass

        def addAction(self, label):
            self.actions.append(label)
            self.labels.append(label)
            return label

        def addSeparator(self):
            pass

        def exec(self, _position):
            return None

    monkeypatch.setattr("app.features.preview.controller.QMenu", RecordingMenu)

    controller.show_context_menu(QPoint(-1, -1))

    assert all("导出" not in label for label in RecordingMenu.labels)
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
