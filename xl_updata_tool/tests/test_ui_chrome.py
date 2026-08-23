import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTableWidget, QTreeWidget

from app.ui.main_window import MainWindow
from app.ui.views.audio_view import create_audio_view
from app.ui.views.character_view import create_character_view
from app.ui.views.preview_view import create_preview_view
from app.ui.views.version_view import create_version_header, create_version_table
from app.ui.features.audio_controller import populate_audio_tree, refresh_audio_tree_unread
from app.core.audio_library import format_size
from app.core.audio_repository import sync_audio_snapshot
from app.ui.theme import TEXT_MUTED


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
    audio_table = audio.findChild(QObject, "audioTree")
    assert audio_table.columnCount() == 6
    assert audio_table.selectionMode().name == "NoSelection"
    assert not any(button.text() == "开始解密" for button in audio.findChildren(QPushButton))
    assert any(button.text() == "全部标为已读" for button in audio.findChildren(QPushButton))

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


def test_audio_unread_only_marks_audio_tab(qapp):
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
        return_value={"unread": {}},
    ), patch(
        "app.ui.main_window.audio_unread_files",
        return_value={"album/第五专辑/event.wav"},
    ):
        window._refresh_unread_badges()

    assert window._unread_badges["audio"].isVisible()
    assert not window._unread_badges["character"].isVisible()
    assert not window._unread_badges["home"].isVisible()
    assert not window._unread_badges["preview"].isVisible()


def test_audio_rows_are_single_choice_when_clicked_anywhere(qapp):
    window = MainWindow.__new__(MainWindow)
    window.audio_table = QTreeWidget()
    window.audio_status = QLabel()
    window._audio_files = [
        {"name": "album\\第五专辑\\a.wav", "dir": "album\\第五专辑", "ext": "WAV", "size": 1, "path": "a.wav"},
        {"name": "album\\第五专辑\\b.wav", "dir": "album\\第五专辑", "ext": "WAV", "size": 1, "path": "b.wav"},
    ]
    window._audio_file_items = populate_audio_tree(
        window.audio_table, window._audio_files, format_size
    )

    first, second = window._audio_file_items
    window._on_audio_item_pressed(first, 3)
    window._on_audio_item_clicked(first, 3)
    assert first.checkState(0).name == "Checked"
    assert second.checkState(0).name == "Unchecked"

    window._on_audio_item_pressed(second, 2)
    window._on_audio_item_clicked(second, 2)
    assert first.checkState(0).name == "Unchecked"
    assert second.checkState(0).name == "Checked"
    assert window.audio_table.selectedItems() == []


def test_audio_unread_marker_propagates_to_outer_folders(qapp):
    table = QTreeWidget()
    files = [
        {
            "name": "voice\\064\\cn\\064_in_01.wav",
            "dir": "voice\\064\\cn",
            "ext": "WAV",
            "size": 1,
            "path": "064_in_01.wav",
            "unread": True,
        },
        {
            "name": "album\\第五专辑\\event.wav",
            "dir": "album\\第五专辑",
            "ext": "WAV",
            "size": 1,
            "path": "event.wav",
            "unread": False,
        },
    ]
    populate_audio_tree(table, files, format_size)

    voice_root = table.topLevelItem(0)
    voice_character = voice_root.child(0)
    voice_language = voice_character.child(0)
    voice_leaf = voice_language.child(0)

    assert voice_root.text(5) == "新"
    assert voice_character.text(5) == "新"
    assert voice_language.text(5) == "新"
    assert voice_leaf.text(5) == "新"

    info = dict(voice_leaf.data(0, Qt.UserRole))
    info["unread"] = False
    voice_leaf.setData(0, Qt.UserRole, info)
    refresh_audio_tree_unread(table)
    assert all(item.text(5) == "" for item in [voice_root, voice_character, voice_language, voice_leaf])


def test_mark_all_audio_read_updates_leaf_data_and_all_parent_markers(qapp, tmp_path):
    audio_dir = tmp_path / "output" / "audio"
    first_path = audio_dir / "voice" / "064" / "cn" / "064_in_01.wav"
    second_path = audio_dir / "album" / "第五专辑" / "event.wav"
    first_path.parent.mkdir(parents=True)
    second_path.parent.mkdir(parents=True)
    first_path.write_bytes(b"cn")
    second_path.write_bytes(b"bgm")
    audio_files = [
        {
            "name": "voice\\064\\cn\\064_in_01.wav",
            "dir": "voice\\064\\cn",
            "ext": "WAV",
            "size": first_path.stat().st_size,
            "path": str(first_path),
        },
        {
            "name": "album\\第五专辑\\event.wav",
            "dir": "album\\第五专辑",
            "ext": "WAV",
            "size": second_path.stat().st_size,
            "path": str(second_path),
        },
    ]
    sync_audio_snapshot(str(audio_dir), audio_files)

    window = MainWindow.__new__(MainWindow)
    window.audio_table = QTreeWidget()
    window.audio_status = QLabel()
    window.status_bar = MagicMock()
    window._refresh_unread_badges = MagicMock()
    window._audio_files = audio_files
    window._audio_file_items = populate_audio_tree(
        window.audio_table, audio_files, format_size
    )

    with patch("app.ui.main_window.get_base_dir", return_value=str(tmp_path)):
        window._mark_all_audio_read()

    assert all(not item.data(0, Qt.UserRole)["unread"] for item in window._audio_file_items)
    assert all(item.text(5) == "" for item in _walk_tree(window.audio_table))
    assert all(item.foreground(5).color().name() == TEXT_MUTED for item in _walk_tree(window.audio_table))


def test_cancel_audio_worker_waits_for_thread_before_replacement(qapp):
    worker = MagicMock()
    worker.wait.return_value = True
    window = MainWindow.__new__(MainWindow)
    window._audio_worker = worker

    assert window._cancel_audio_worker() is True
    worker.cancel.assert_called_once_with()
    worker.wait.assert_called_once_with(30000)
    assert window._audio_worker is None


def _walk_tree(table):
    items = []

    def visit(item):
        items.append(item)
        for index in range(item.childCount()):
            visit(item.child(index))

    for index in range(table.topLevelItemCount()):
        visit(table.topLevelItem(index))
    return items
