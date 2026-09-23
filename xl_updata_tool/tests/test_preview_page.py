import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QTabBar

from app.features.preview.material_catalog import AtlasResourceGroup, GameMaterialCatalog, GameMaterialRecord
from app.features.preview.page import PreviewPage
from app.features.preview.resource_model import PreviewResourceCatalog, SpineSkinRecord
from app.features.preview.output_browser import OutputBrowserEntry, path_fingerprint
from app.features.preview.controller import PreviewController
from app.features.preview.service import PreviewService
from app.features.preview.resource_state import PreviewResourceState


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_preview_page_exposes_three_named_tabs_and_legacy_character_controls(qapp):
    page = PreviewPage()

    assert page.tabs.count() == 3
    assert [page.tabs.tabText(index) for index in range(3)] == [
        "角色 Spine",
        "角色导出立绘",
        "游戏素材",
    ]
    assert page.tabs.spine_tab is page.tabs.widget(0)
    assert page.tabs.character_tab is page.tabs.widget(1)
    assert page.tabs.material_tab is page.tabs.widget(2)
    assert page.tabs.accessibleName() == "预览资源分页"
    assert page.spine_tree.objectName() == "previewSpineTree"
    assert page.material_tree.objectName() == "previewMaterialGroups"
    assert page.image_list.objectName() == "previewImageList"
    assert page.character_browser.objectName() == "previewIconBrowser"
    assert page.material_browser.objectName() == "previewIconBrowser"
    assert page.character_filter.accessibleName() == "角色筛选"
    assert page.preview_progress.objectName() == "previewProgress"
    assert page.preview_status.objectName() == "pageStatus"
    assert page.btn_mark_all_read.objectName() == "markAllPreviewReadButton"
    assert not hasattr(page, "btn_close_preview")
    assert page.btn_reload.isHidden()
    assert page.character_filter.isHidden()
    assert page.btn_thumbnail_previous.isHidden()
    assert page.btn_thumbnail_next.isHidden()
    assert page.tabs.tabBar().tabButton(0, QTabBar.RightSide).text() == "●"


def test_preview_progress_is_next_to_mark_all_read_without_preview_count_header(qapp):
    page = PreviewPage()

    assert page.command_layout.indexOf(page.preview_progress) == (
        page.command_layout.indexOf(page.btn_mark_all_read) + 1
    )
    assert not any(
        label.text().startswith("角色预览器")
        for label in page.findChildren(QLabel)
    )
    page.close()


def test_output_folder_buttons_open_current_character_and_material_folders(qapp, tmp_path, monkeypatch):
    from app.features.preview import controller as controller_module

    opened = []
    monkeypatch.setattr(controller_module.subprocess, "Popen", lambda command: opened.append(command))
    preview_root = tmp_path / "output" / "character"
    material_root = tmp_path / "output" / "game_material"
    character_folder = preview_root / "10080 - character"
    material_folder = material_root / "fgui" / "Battle"
    character_folder.mkdir(parents=True)
    material_folder.mkdir(parents=True)
    page = PreviewPage()
    _controller = PreviewController(
        page,
        PreviewService(tmp_path / "material", preview_root),
    )
    page.set_character_output_root(preview_root)
    page.open_character_output_folder(character_folder)
    page.open_material_output_folder(material_folder)

    page.btn_open_character_folder.click()
    page.btn_open_material_folder.click()

    assert opened == [
        ["explorer", os.path.normpath(str(character_folder))],
        ["explorer", os.path.normpath(str(material_folder))],
    ]
    page.close()


def test_material_folder_button_uses_game_material_root_at_catalog_root(qapp, tmp_path, monkeypatch):
    from app.features.preview import controller as controller_module

    opened = []
    monkeypatch.setattr(controller_module.subprocess, "Popen", lambda command: opened.append(command))
    output_root = tmp_path / "output"
    material_root = output_root / "game_material"
    material_root.mkdir(parents=True)
    page = PreviewPage()
    _controller = PreviewController(
        page,
        PreviewService(tmp_path / "material", output_root / "character"),
    )

    page.btn_open_material_folder.click()

    assert opened == [["explorer", os.path.normpath(str(material_root))]]
    page.close()


def test_preview_page_cascades_tab_badges_and_icon_new_marker(qapp):
    page = PreviewPage()
    page.character_browser.set_entries(
        [OutputBrowserEntry("菲尼斯", "E:/output/菲尼斯", "folder", 1, "folder-fp", True)]
    )
    page.tabs.set_tab_unread("角色导出立绘", True)

    item = page.character_browser.item(0)
    badge = page.tabs._tab_badges["角色导出立绘"]
    assert "新" not in item.text()
    assert item.data(Qt.UserRole)["is_new"] is True
    assert badge.property("unread") is True

    page.tabs.set_tab_unread("角色导出立绘", False)
    assert badge.property("unread") is False
    page.close()


def test_character_root_return_button_is_disabled_at_root(qapp, tmp_path):
    page = PreviewPage()
    page.set_character_output_root(tmp_path, None)

    assert not page.btn_character_up.isEnabled()
    page.open_character_output_folder(tmp_path / "10080")
    assert page.btn_character_up.isEnabled()
    page.close()


def test_icon_badge_is_removed_when_leaf_becomes_read(qapp, tmp_path):
    image = tmp_path / "10080_4.png"
    image.write_bytes(b"png")
    state = PreviewResourceState(tmp_path / "state.json")
    page = PreviewPage()
    browser = page.character_browser
    browser.set_entries(
        [OutputBrowserEntry(image.name, str(image), "file", 0, path_fingerprint(image), True)]
    )
    item = browser.item(0)
    assert item.data(Qt.UserRole)["is_new"] is True

    state.mark_read(path_fingerprint(image))
    browser.refresh_entry_statuses(state)

    assert item.data(Qt.UserRole)["is_new"] is False
    page.close()


def test_preview_page_renders_spine_catalog_without_replacing_character_state(qapp):
    page = PreviewPage()
    record = SpineSkinRecord(
        "10080",
        "E:/material/cardspine_10080.skel",
        "E:/material/cardspine_10080.atlas",
        "default",
        "attachment-a",
        "默认外观",
        "ready",
    )
    page.set_spine_catalog(PreviewResourceCatalog.from_records([record]))
    page.image_list.addItem("legacy image")
    page.spine_tree.set_selected_records([record])

    assert page.spine_tree.topLevelItemCount() == 1
    assert page.selected_spine_records() == (record,)
    assert page.image_list.count() == 1
    page.tabs.setCurrentWidget(page.tabs.spine_tab)
    page.tabs.setCurrentWidget(page.tabs.character_tab)
    assert page.image_list.count() == 1
    assert page.selected_spine_records() == (record,)


def test_preview_page_accepts_game_material_catalog_and_has_explicit_empty_states(qapp):
    page = PreviewPage()
    page.set_game_material_catalog(
        GameMaterialCatalog(
            burst_heads=(GameMaterialRecord("burst-head", "burst-head/10080.png", "10080", "head-fp"),),
            atlases=(AtlasResourceGroup("Battle", "Battle_fui.bytes", ("Battle_bg.png",)),),
            unmatched=(),
        )
    )

    labels = [page.material_tree.topLevelItem(index).text(0) for index in range(page.material_tree.topLevelItemCount())]
    assert labels == ["game_material/burst-head", "game_material/fgui/Battle"]
    burst_data = page.material_tree.topLevelItem(0).data(0, Qt.UserRole)
    atlas_data = page.material_tree.topLevelItem(1).data(0, Qt.UserRole)
    assert burst_data["kind"] == "burst-head"
    assert burst_data["path"] == "game_material/burst-head"
    assert atlas_data["kind"] == "atlas"
    assert atlas_data["package"] == "Battle"
    assert atlas_data["path"] == "game_material/fgui/Battle"
    assert atlas_data["source"] == "Battle_fui.bytes"
    assert page.material_empty_label.isHidden()
    assert not page.spine_empty_label.isHidden()

    page.set_game_material_catalog(GameMaterialCatalog((), (), ()))

    assert page.material_tree.topLevelItemCount() == 0
    assert not page.material_empty_label.isHidden()
    page.close()


def test_preview_page_renders_standalone_game_material_groups(qapp):
    page = PreviewPage()
    page.set_game_material_catalog(
        GameMaterialCatalog(
            burst_heads=(),
            atlases=(),
            unmatched=(),
            standalone=(
                GameMaterialRecord("lottery-bg", "output/game_material/lottery-bg/LotteryBg_042.png", "LotteryBg_042", "fp"),
            ),
        )
    )

    item = page.material_tree.topLevelItem(0)
    assert item.text(0) == "game_material/lottery-bg"
    assert item.data(0, Qt.UserRole)["kind"] == "lottery-bg"
    assert page.material_browser.count() == 1
    page.close()


def test_controller_style_empty_updates_are_routed_to_the_current_tab(qapp):
    page = PreviewPage()
    root = Path.cwd() / f".task5-preview-controller-{uuid4().hex}"
    controller = PreviewController(
        page,
        PreviewService(root / "material", root / "output" / "character"),
    )

    page.tabs.setCurrentWidget(page.tabs.spine_tab)
    controller._on_load_finished([])
    assert page.empty_label.isHidden()
    page.tabs.setCurrentWidget(page.tabs.material_tab)
    assert page.empty_label.isHidden()
    page.tabs.setCurrentWidget(page.tabs.character_tab)
    assert not page.empty_label.isHidden()

    page.close()


def test_controller_keeps_image_thread_alive_until_qthread_finished(qapp):
    page = PreviewPage()
    root = Path.cwd() / f".task5-preview-worker-{uuid4().hex}"
    controller = PreviewController(
        page,
        PreviewService(root / "material", root / "output" / "character"),
    )
    worker = object()
    controller._image_worker = worker

    controller._on_load_finished([])

    assert controller._image_worker is worker
    page.close()


def test_icon_browser_keeps_thumbnail_thread_alive_until_qthread_finished(qapp):
    page = PreviewPage()
    worker = object()
    page.material_browser._thumbnail_worker = worker

    page.material_browser._on_thumbnail_finished([])

    assert page.material_browser._thumbnail_worker is worker
    page.close()


def test_legacy_character_controls_and_state_survive_tab_switches(qapp):
    page = PreviewPage()
    page.character_filter.addItem("全部角色", "")
    page.character_filter.addItem("10080", "10080")
    page.character_filter.setCurrentIndex(1)
    page.preview_progress.setVisible(True)
    page.preview_progress.setValue(3)
    page.preview_status.setText("legacy status")
    page.image_list.addItem("legacy image")
    page.image_list.item(0).setSelected(True)

    for tab in (page.tabs.spine_tab, page.tabs.material_tab, page.tabs.character_tab):
        page.tabs.setCurrentWidget(tab)

    assert page.image_list.count() == 1
    assert page.image_list.item(0).isSelected()
    assert page.character_filter.currentData() == "10080"
    assert not page.preview_progress.isHidden()
    assert page.preview_progress.value() == 3
    assert page.preview_status.text() == "legacy status"
    page.close()
