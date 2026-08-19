import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.ui.dialogs.character_select import CharacterSelectDialog
from app.ui.dialogs.export_settings import ExportSettingsDialog
from app.ui.dialogs.image_viewer import ImageViewerDialog
from app.ui.theme import apply_theme


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "new")
    yield app


def test_image_viewer_empty_state_disables_navigation(qapp):
    dialog = ImageViewerDialog([])

    assert dialog.objectName() == "imageViewerDialog"
    assert dialog.info_label.text() == "0 / 0"
    assert not dialog.btn_prev.isEnabled()
    assert not dialog.btn_next.isEnabled()
    assert dialog.btn_close.accessibleName() == "关闭图片预览"


def test_character_select_dialog_preserves_selection_contract(qapp):
    dialog = CharacterSelectDialog(["角色甲", "角色乙"])

    assert dialog.objectName() == "characterSelectDialog"
    assert dialog.selected_roles() == {"角色甲", "角色乙"}
    dialog.findChild(QPushButton, "clearAllButton").click()
    assert dialog.selected_roles() == set()
    dialog.findChild(QPushButton, "selectAllButton").click()
    assert dialog.selected_roles() == {"角色甲", "角色乙"}
    assert dialog.findChild(QPushButton, "dialogPrimaryButton").text() == "导出选中"


def test_export_settings_dialog_keeps_output_settings_contract(qapp):
    dialog = ExportSettingsDialog(r"C:\\assets\\hero.skel", r"C:\\assets\\hero.atlas", "GIF")

    assert dialog.objectName() == "exportSettingsDialog"
    assert dialog.format_combo.currentText() == "GIF"
    settings = dialog.get_settings()
    assert settings["format"] == "gif"
    assert settings["animation"] == "idle"
    assert settings["file_label"].endswith(".gif")
    assert dialog.findChild(QPushButton, "dialogPrimaryButton").text() == "导出"
