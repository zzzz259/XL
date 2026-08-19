import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QPushButton

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
    detail_empty = views[2].findChild(QObject, "detailEmptyState")
    detail_body = views[2].findChild(QObject, "detailBody")
    highlight_button = views[2].findChild(QObject, "numberHighlightButton")
    assert detail_empty is not None and not detail_empty.isHidden()
    assert detail_body is not None and detail_body.isHidden()
    assert highlight_button is not None and highlight_button.isHidden()


def test_version_workspace_has_summary_and_stable_table_contract(qapp):
    header, summary = create_version_header()
    table = create_version_table()

    assert header.objectName() == "workspaceHeader"
    assert summary.objectName() == "workspaceSummary"
    assert table.columnCount() == 8
    assert table.objectName() == "workspaceTable"
    assert table.horizontalHeaderItem(1).text() == "版本"
