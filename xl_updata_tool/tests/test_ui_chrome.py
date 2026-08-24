import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTableWidget, QTreeWidget

from app.ui.main_window import MainWindow
from app.features.audio.controller import AudioController
from app.features.audio.page import AudioPage
from app.features.audio.service import AudioService
from app.ui.views.audio_view import create_audio_view
from app.ui.views.character_view import create_character_view
from app.ui.views.preview_view import create_preview_view
from app.ui.views.version_view import create_version_header, create_version_table
from app.features.versions.page import VersionPage
from app.ui.features.audio_controller import populate_audio_tree, refresh_audio_tree_unread
from app.features.audio.audio_library import format_size
from app.ui.theme import DANGER, TEXT_MUTED


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


def test_auto_update_routes_through_version_controller(monkeypatch):
    window = MainWindow.__new__(MainWindow)
    window.version_service = SimpleNamespace(current=lambda: None)
    window.version_controller = SimpleNamespace(check_update=MagicMock())
    window.status_bar = SimpleNamespace(showMessage=MagicMock())
    callbacks = []
    monkeypatch.setattr(
        "app.ui.main_window.QTimer.singleShot",
        lambda _delay, callback: callbacks.append(callback),
    )

    window._check_auto()

    assert len(callbacks) == 1
    callbacks[0]()
    window.version_controller.check_update.assert_called_once_with()


def test_version_page_visibility_controls_whole_page(qapp):
    page = VersionPage()
    page.show()
    qapp.processEvents()

    page.set_visible(False)
    assert page.isHidden()

    page.set_visible(True)
    qapp.processEvents()
    assert page.isVisible()

    page.close()


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
    window.runtime = SimpleNamespace(
        features=[
            SimpleNamespace(descriptor=SimpleNamespace(key=key, title=title, icon=icon))
            for key, title, icon in (
                ("versions", "版本列表", "list"),
                ("preview", "图片预览", "image"),
                ("audio", "音频", "music"),
                ("character", "角色", "users"),
                ("importer", "导入AS", "file-import"),
            )
        ]
    )

    toolbar = window._view_toolbar()

    assert toolbar is not None
    assert window.btn_home.icon().isNull() is False or window._icon("list") is None
    assert window.btn_image_preview.icon().isNull() is False or window._icon("image") is None
    assert window.btn_audio.icon().isNull() is False or window._icon("music") is None
    assert window.btn_lua.icon().isNull() is False or window._icon("users") is None


def test_character_unread_only_marks_character_tab(qapp):
    window = MainWindow.__new__(MainWindow)
    window.character_controller = SimpleNamespace(has_unread=True)
    window._unread_badges = {
        "home": QLabel(),
        "preview": QLabel(),
        "audio": QLabel(),
        "character": QLabel(),
    }

    window._refresh_unread_badges()

    assert window._unread_badges["character"].isVisible()
    assert not window._unread_badges["home"].isVisible()
    assert not window._unread_badges["preview"].isVisible()
    assert not window._unread_badges["audio"].isVisible()


def test_audio_unread_only_marks_audio_tab(qapp):
    window = MainWindow.__new__(MainWindow)
    window.character_controller = SimpleNamespace(has_unread=False)
    window.audio_controller = SimpleNamespace(has_unread=True)
    window._unread_badges = {
        "home": QLabel(),
        "preview": QLabel(),
        "audio": QLabel(),
        "character": QLabel(),
    }

    window._refresh_unread_badges()

    assert window._unread_badges["audio"].isVisible()
    assert not window._unread_badges["character"].isVisible()
    assert not window._unread_badges["home"].isVisible()
    assert not window._unread_badges["preview"].isVisible()


def test_audio_rows_are_single_choice_when_clicked_anywhere(qapp):
    controller = _build_audio_controller(qapp)
    controller._audio_files = [
        {"name": "album\\第五专辑\\a.wav", "dir": "album\\第五专辑", "ext": "WAV", "size": 1, "path": "a.wav"},
        {"name": "album\\第五专辑\\b.wav", "dir": "album\\第五专辑", "ext": "WAV", "size": 1, "path": "b.wav"},
    ]
    controller._audio_file_items = populate_audio_tree(
        controller.page.audio_table, controller._audio_files, format_size
    )

    first, second = controller._audio_file_items
    controller.on_item_pressed(first, 3)
    controller.on_item_clicked(first, 3)
    qapp.processEvents()
    assert first.checkState(0).name == "Checked"
    assert second.checkState(0).name == "Unchecked"

    controller.on_item_pressed(second, 2)
    controller.on_item_clicked(second, 2)
    qapp.processEvents()
    assert first.checkState(0).name == "Unchecked"
    assert second.checkState(0).name == "Checked"
    assert controller.page.audio_table.selectedItems() == []


def test_audio_checkbox_indicator_toggles_reliably(qapp):
    controller = _build_audio_controller(qapp)
    controller._audio_files = [
        {"name": "album\\专辑\\a.wav", "dir": "album\\专辑", "ext": "WAV", "size": 1, "path": "a.wav"},
    ]
    controller._audio_file_items = populate_audio_tree(
        controller.page.audio_table, controller._audio_files, format_size
    )
    controller.page.resize(800, 500)
    controller.page.show()
    controller.page.audio_table.expandAll()
    qapp.processEvents()

    item = controller._audio_file_items[0]
    rect = controller.page.audio_table.visualItemRect(item)
    checkbox = QPoint(rect.left() + 3, rect.center().y())
    QTest.mouseClick(controller.page.audio_table.viewport(), Qt.LeftButton, Qt.NoModifier, checkbox)
    qapp.processEvents()
    assert item.checkState(0) == Qt.Checked

    QTest.mouseClick(controller.page.audio_table.viewport(), Qt.LeftButton, Qt.NoModifier, checkbox)
    qapp.processEvents()
    assert item.checkState(0) == Qt.Unchecked
    controller.page.close()


def test_audio_directory_selection_checks_descendants_and_ctrl_adds(qapp):
    controller = _build_audio_controller(qapp)
    controller._audio_files = [
        {"name": "album\\a\\one.wav", "dir": "album\\a", "ext": "WAV", "size": 1, "path": "one.wav"},
        {"name": "album\\a\\two.wav", "dir": "album\\a", "ext": "WAV", "size": 1, "path": "two.wav"},
        {"name": "album\\b\\three.wav", "dir": "album\\b", "ext": "WAV", "size": 1, "path": "three.wav"},
    ]
    controller._audio_file_items = populate_audio_tree(
        controller.page.audio_table, controller._audio_files, format_size
    )

    root = controller.page.audio_table.topLevelItem(0)
    first_album = root.child(0)
    controller.on_item_pressed(first_album, 0)
    controller.on_item_clicked(first_album, 0)
    qapp.processEvents()
    assert [item.checkState(0) for item in controller._audio_file_items] == [
        Qt.Checked, Qt.Checked, Qt.Unchecked
    ]

    second_album = root.child(1)
    with patch("app.features.audio.controller.QApplication.keyboardModifiers", return_value=Qt.ControlModifier):
        controller.on_item_pressed(second_album, 0)
        controller.on_item_clicked(second_album, 0)
        qapp.processEvents()
    assert all(item.checkState(0) == Qt.Checked for item in controller._audio_file_items)


def test_audio_slider_commits_seek_on_release(qapp):
    class FakePlayer:
        def __init__(self):
            self.positions = []

        def duration(self):
            return 10000

        def setPosition(self, position):
            self.positions.append(position)

    controller = _build_audio_controller(qapp)
    controller._audio_player = FakePlayer()
    controller.page.audio_slider.setRange(0, 10000)

    controller.on_slider_pressed()
    controller.page.audio_slider.setValue(4200)
    controller.on_slider_moved(4200)
    assert controller._audio_player.positions == []
    controller.on_slider_released()

    assert controller._audio_player.positions == [4200]
    assert "00:04" in controller.page.audio_position_label.text()


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
            "name": "voice\\064\\cn\\064_in_02.wav",
            "dir": "voice\\064\\cn",
            "ext": "WAV",
            "size": 1,
            "path": "064_in_02.wav",
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
    assert all(voice_language.child(i).text(5) == "新" for i in range(voice_language.childCount()))
    assert all(
        voice_language.child(i).foreground(5).color().name() == DANGER
        for i in range(voice_language.childCount())
    )

    for index in range(voice_language.childCount()):
        leaf = voice_language.child(index)
        info = dict(leaf.data(0, Qt.UserRole))
        info["unread"] = False
        leaf.setData(0, Qt.UserRole, info)
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
    controller = _build_audio_controller(qapp, tmp_path)
    controller.load_catalog()
    controller.mark_all_read()

    assert all(not item.data(0, Qt.UserRole)["unread"] for item in controller._audio_file_items)
    assert all(item.text(5) == "" for item in _walk_tree(controller.page.audio_table))
    assert all(item.foreground(5).color().name() == TEXT_MUTED for item in _walk_tree(controller.page.audio_table))


def test_cancel_audio_worker_waits_for_thread_before_replacement(qapp):
    worker = MagicMock()
    worker.wait.return_value = True
    controller = _build_audio_controller(qapp)
    controller._audio_worker = worker

    assert controller.cancel_audio_worker() is True
    worker.cancel.assert_called_once_with()
    worker.wait.assert_called_once_with(30000)
    assert controller._audio_worker is None


def _build_audio_controller(qapp, output_root=None):
    page = AudioPage()
    return AudioController(
        page=page,
        service=AudioService(output_root or "E:/AI-Agent/Codex-GPT/tmp/audio-controller"),
        material_dir="",
        debank_dir="",
        lua_output_dir="",
        parent=page,
    )


def _walk_tree(table):
    items = []

    def visit(item):
        items.append(item)
        for index in range(item.childCount()):
            visit(item.child(index))

    for index in range(table.topLevelItemCount()):
        visit(table.topLevelItem(index))
    return items
