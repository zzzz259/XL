import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.features.preview.material_catalog import AtlasResourceGroup, GameMaterialCatalog, GameMaterialRecord
from app.features.preview.page import PreviewPage
from app.features.preview.resource_model import PreviewResourceCatalog, SpineSkinRecord
from app.features.preview.controller import PreviewController
from app.features.preview.service import PreviewService


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
    assert labels == ["game_material/burst-head", "fgui/Battle"]
    burst_data = page.material_tree.topLevelItem(0).data(0, Qt.UserRole)
    atlas_data = page.material_tree.topLevelItem(1).data(0, Qt.UserRole)
    assert burst_data["kind"] == "burst-head"
    assert burst_data["path"] == "game_material/burst-head"
    assert atlas_data["kind"] == "atlas"
    assert atlas_data["package"] == "Battle"
    assert atlas_data["path"] == "fgui/Battle"
    assert atlas_data["source"] == "Battle_fui.bytes"
    assert page.material_empty_label.isHidden()
    assert not page.spine_empty_label.isHidden()

    page.set_game_material_catalog(GameMaterialCatalog((), (), ()))

    assert page.material_tree.topLevelItemCount() == 0
    assert not page.material_empty_label.isHidden()
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
