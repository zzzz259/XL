import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.features.preview.material_catalog import AtlasResourceGroup, GameMaterialCatalog, GameMaterialRecord
from app.features.preview.page import PreviewPage
from app.features.preview.resource_model import PreviewResourceCatalog, SpineSkinRecord


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
    assert labels == ["Burst Head / 大头照", "图集 · Battle"]
    assert page.material_empty_label.isHidden()
    assert not page.spine_empty_label.isHidden()

    page.set_game_material_catalog(GameMaterialCatalog((), (), ()))

    assert page.material_tree.topLevelItemCount() == 0
    assert not page.material_empty_label.isHidden()
    page.close()
