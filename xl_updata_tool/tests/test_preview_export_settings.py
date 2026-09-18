import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.features.preview.dialogs.export_settings import ExportSettingsDialog
from app.features.preview.export_plan import ExportSettings


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_dialog_returns_png_export_settings_from_controls(qapp):
    dialog = ExportSettingsDialog(
        r"E:\\assets\\hero.skel", r"E:\\assets\\hero.atlas", "PNG"
    )

    dialog.anim_combo.setCurrentText("walk")
    dialog.scale_spin.setValue(3)
    dialog.max_resolution_spin.setValue(4096)
    dialog.margin_spin.setValue(12)
    dialog.fps_spin.setValue(2)
    dialog.transparent_checkbox.setChecked(False)
    dialog.pma_checkbox.setChecked(False)

    settings = dialog.settings()

    assert isinstance(settings, ExportSettings)
    assert settings == ExportSettings(
        animation="walk",
        static=True,
        scale=3,
        max_resolution=4096,
        margin=12,
        transparent=False,
        pma=False,
        format="Png",
        fps=2,
    )
    assert dialog.format_combo.isEnabled() is False
    assert dialog.format_combo.currentText() == "PNG"
    assert dialog.mode_combo.currentText() == "静态图"
    assert dialog.anim_combo.isEnabled() is False


def test_dialog_can_explicitly_select_animation_mode(qapp):
    dialog = ExportSettingsDialog(r"E:\\hero.skel", r"E:\\hero.atlas", "PNG")

    dialog.mode_combo.setCurrentText("动画")
    dialog.anim_combo.setCurrentText("walk")
    dialog.fps_spin.setValue(12)

    settings = dialog.settings()

    assert settings.static is False
    assert settings.animation == "walk"
    assert settings.fps == 12
    assert dialog.anim_combo.isEnabled() is True


def test_dialog_cancel_does_not_produce_an_export_settings(qapp):
    dialog = ExportSettingsDialog(r"E:\\hero.skel", r"E:\\hero.atlas", "PNG")
    dialog.findChild(QPushButton, "dialogCancelButton").click()

    assert dialog.result() == dialog.Rejected


def test_dialog_keeps_legacy_video_settings_fields(qapp):
    dialog = ExportSettingsDialog(r"E:\\hero.skel", r"E:\\hero.atlas", "GIF")

    settings = dialog.get_settings()

    assert settings["format"] == "gif"
    assert [dialog.format_combo.itemText(index) for index in range(dialog.format_combo.count())] == ["MP4", "GIF"]
    assert settings["duration"] == 2
    assert settings["fps"] == 15
    assert settings["scale"] == 2
    assert settings["pma"] is True
    assert settings["auto_open"] is True
