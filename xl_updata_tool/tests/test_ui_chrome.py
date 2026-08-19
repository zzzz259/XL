import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTableWidget

from app.ui.main_window import MainWindow
from app.ui.views.audio_view import create_audio_view
from app.ui.views.character_view import create_character_view
from app.ui.views.preview_view import create_preview_view
from app.ui.views.version_view import create_version_header, create_version_table


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.quit()


def test_feature_views_use_shared_page_chrome(qapp):
    views = [create_preview_view()[0], create_audio_view()[0], create_character_view()[0]]

    for view in views:
        assert view.objectName() == "viewContainer"
        assert view.findChild(QObject, "pageHeader") is not None
        assert view.findChild(QObject, "pageCommandBar") is not None

    preview = views[0]
    assert preview.findChild(QObject, "pageStatus") is not None
    assert preview.findChild(QObject, "previewProgress") is not None
    assert preview.findChild(QObject, "previewImageList") is not None
    empty_state = preview.findChild(QObject, "emptyState")
    assert empty_state is not None
    assert empty_state.parent().objectName() == "viewContent"
    assert all("🖼" not in button.text() for button in preview.findChildren(QPushButton))

    audio = views[1]
    audio_empty = audio.findChild(QObject, "audioEmptyState")
    assert audio_empty is not None
    assert audio_empty.parent().objectName() == "viewContent"
    assert audio.findChild(QObject, "audioTree") is not None
    assert audio.findChild(QObject, "audioPlayButton").text() == "播放"

    character_empty = views[2].findChild(QObject, "emptyState")
    assert character_empty is not None
    assert character_empty.parent().objectName() == "viewContent"
    character_table = views[2].findChild(QTableWidget)
    assert character_table is not None
    assert character_table.columnCount() == 3
    assert character_table.horizontalHeaderItem(2).text() == "状态"
    detail_empty = views[2].findChild(QObject, "detailEmptyState")
    detail_body = views[2].findChild(QObject, "detailBody")
    highlight_button = views[2].findChild(QObject, "numberHighlightButton")
    assert detail_empty is not None and not detail_empty.isHidden()
    assert detail_body is not None and detail_body.isHidden()
    assert highlight_button is not None and highlight_button.isHidden()
    assert any(button.text() == "全部标为已读" for button in views[2].findChildren(QPushButton))


def test_version_workspace_has_summary_and_stable_table_contract(qapp):
    header, summary = create_version_header()
    table = create_version_table()

    assert header.objectName() == "workspaceHeader"
    assert summary.objectName() == "workspaceSummary"
    assert table.columnCount() == 8
    assert table.objectName() == "workspaceTable"
    assert table.horizontalHeaderItem(1).text() == "版本"


def test_main_view_toolbar_builds_qicons_for_navigation(qapp):
    window = MainWindow.__new__(MainWindow)

    toolbar = window._view_toolbar()

    assert toolbar is not None
    assert window.btn_home.icon().isNull() is False or window._icon("list") is None
    assert window.btn_image_preview.icon().isNull() is False or window._icon("image") is None
    assert window.btn_audio.icon().isNull() is False or window._icon("music") is None
    assert window.btn_lua.icon().isNull() is False or window._icon("users") is None


def test_character_unread_only_marks_character_tab(qapp):
    window = MainWindow.__new__(MainWindow)
    window.character_unread = {}
    window._unread_badges = {
        "home": QLabel(),
        "preview": QLabel(),
        "audio": QLabel(),
        "character": QLabel(),
    }

    with patch(
        "app.ui.main_window.load_character_repository",
        return_value={"unread": {"80100001": "new"}},
    ):
        window._refresh_unread_badges()

    assert window._unread_badges["character"].isVisible()
    assert not window._unread_badges["home"].isVisible()
    assert not window._unread_badges["preview"].isVisible()
    assert not window._unread_badges["audio"].isVisible()
