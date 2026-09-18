import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from app.features.preview.dialogs.export_settings import ExportSettingsDialog
from app.features.preview.export_plan import ExportSettings
from app.features.preview.export_controller import _preferred_skin_name


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_dialog_returns_png_export_settings_from_controls(qapp):
    dialog = ExportSettingsDialog(
        r"E:\\assets\\hero.skel", r"E:\\assets\\hero.atlas", "PNG"
    )
    dialog.custom_radio.click()

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
    assert dialog.anim_combo.isEnabled() is True
    assert not dialog.duration_spin.isVisible()
    assert not dialog.fps_spin.isVisible()


def test_dialog_can_explicitly_select_animation_mode(qapp):
    dialog = ExportSettingsDialog(r"E:\\hero.skel", r"E:\\hero.atlas", "PNG")
    dialog.custom_radio.click()

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
    dialog.custom_radio.click()

    settings = dialog.get_settings()

    assert settings["format"] == "gif"
    assert [dialog.format_combo.itemText(index) for index in range(dialog.format_combo.count())] == ["MP4", "GIF"]
    assert settings["duration"] == 2
    assert settings["fps"] == 15
    assert settings["scale"] == 2
    assert settings["pma"] is True
    assert settings["auto_open"] is True


def test_dialog_exposes_internal_spine_skins_and_animation_duration(qapp):
    dialog = ExportSettingsDialog(
        r"E:\\hero.skel",
        r"E:\\hero.atlas",
        "MP4",
        animation_names=("idle", "walk"),
        animation_durations={"idle": 1.25, "walk": 0.8},
        skin_names=("default", "motion_angry"),
    )
    dialog.custom_radio.click()

    dialog.anim_combo.setCurrentText("walk")
    assert dialog.duration_spin.value() == 0.8
    dialog.skin_list.item(1).setCheckState(Qt.Checked)

    assert dialog.selected_skins() == ("default", "motion_angry")
    assert dialog.settings().format == "Mp4"


def test_cardspine_preset_is_static_four_x_and_auto_bounded(qapp):
    dialog = ExportSettingsDialog(
        r"E:\\cardspine_10080_4.skel",
        r"E:\\cardspine_10080_4.atlas",
        "PNG",
        animation_names=("idle", "walk"),
        animation_durations={"idle": 8.333334},
        skin_names=("default",),
        resource_family="cardspine",
    )
    dialog.custom_radio.click()

    settings = dialog.settings()

    assert settings.static is True
    assert settings.animation == "idle"
    assert settings.scale == 4
    assert settings.max_resolution == 16000
    assert settings.margin == 10
    assert settings.transparent is True
    assert settings.pma is True
    assert settings.background_color == "#00000000"
    assert settings.auto_border is True
    assert dialog.mode_combo.isEnabled()
    assert dialog.advanced_toggle.isChecked() is False


def test_cardspine_video_preset_uses_gray_background_and_builtin_timing(qapp):
    dialog = ExportSettingsDialog(
        r"E:\\cardspine_10080_4.skel",
        r"E:\\cardspine_10080_4.atlas",
        "PNG",
        animation_names=("idle",),
        animation_durations={"idle": 8.333334},
        skin_names=("default",),
        resource_family="cardspine",
    )
    dialog.mode_combo.setCurrentText("动画")

    settings = dialog.settings()

    assert settings.static is False
    assert settings.format == "Mp4"
    assert settings.scale == 1
    assert settings.duration == pytest.approx(8.333, rel=1e-5)
    assert settings.fps == 30
    assert settings.transparent is False
    assert settings.background_color == "#7f7f7f"


def test_battlespine_preset_is_png_only_and_selects_stander_skin(qapp):
    dialog = ExportSettingsDialog(
        r"E:\\battlespine_10080_2.skel",
        r"E:\\battlespine_10080_2.atlas",
        "MP4",
        animation_names=("idle", "run"),
        skin_names=("default", "motion_stander", "motion_angry"),
        resource_family="battlespine",
    )

    settings = dialog.settings()

    assert settings.static is True
    assert settings.format == "Png"
    assert settings.scale == 1
    assert settings.skins == ("motion_stander",)
    assert dialog.mode_combo.count() == 1
    assert dialog.format_combo.currentText() == "PNG"


def test_legacy_battlespine_export_uses_selected_stander_skin_when_filename_has_none():
    assert _preferred_skin_name(
        {"skins": ("motion_stander",)},
        "battlespine",
        None,
    ) == "motion_stander"


def test_export_settings_advanced_content_is_scrollable(qapp):
    dialog = ExportSettingsDialog(r"E:\\hero.skel", r"E:\\hero.atlas", "PNG")
    dialog.custom_radio.click()

    scroll = dialog.findChild(QScrollArea, "exportSettingsScroll")

    assert scroll is not None
    assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    assert dialog.advanced_toggle.parentWidget() is scroll.widget()

    dialog.resize(420, 360)
    dialog.show()
    dialog.advanced_toggle.setChecked(True)
    qapp.processEvents()
    primary_button = dialog.findChild(QPushButton, "dialogPrimaryButton")
    assert primary_button.parentWidget() is dialog
    assert scroll.verticalScrollBar().maximum() > 0
    dialog.close()


def test_dialog_defaults_to_locked_builtin_mode_with_family_summary(qapp):
    dialog = ExportSettingsDialog(
        r"E:\\cardspine_10080_4.skel",
        r"E:\\cardspine_10080_4.atlas",
        "PNG",
        resource_family="cardspine",
        selected_group_count=2,
        family_summary=("cardspine", "battlespine"),
    )

    assert dialog.export_mode() == "builtin"
    assert dialog.builtin_radio.isChecked()
    assert not dialog.custom_radio.isEnabled()
    assert not dialog.scale_spin.isEnabled()
    assert "PNG" in dialog.mode_summary.text()
    assert "MP4" in dialog.mode_summary.text()
    dialog.close()


def test_dialog_custom_mode_unlocks_manual_settings_for_one_skin(qapp):
    dialog = ExportSettingsDialog(
        r"E:\\cardspine_10080_4.skel",
        r"E:\\cardspine_10080_4.atlas",
        "PNG",
        resource_family="cardspine",
        selected_group_count=1,
    )

    dialog.custom_radio.click()

    assert dialog.export_mode() == "custom"
    assert dialog.scale_spin.isEnabled()
    assert dialog.format_combo.isEnabled() is False
    dialog.close()


def test_battlespine_custom_mode_does_not_offer_video(qapp):
    dialog = ExportSettingsDialog(
        r"E:\\battlespine_10080_2.skel",
        r"E:\\battlespine_10080_2.atlas",
        "PNG",
        resource_family="battlespine",
        selected_group_count=1,
    )

    dialog.custom_radio.click()

    assert [dialog.mode_combo.itemText(i) for i in range(dialog.mode_combo.count())] == ["静态图"]
    assert not dialog.mode_combo.isEnabled()
    dialog.close()
