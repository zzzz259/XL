import json

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from app.features.characters.repository import merge_snapshot
from app.features.characters.controller import CharacterController
from app.features.characters.page import CharacterPage
from app.features.characters.service import CharacterService
from app.ui.views.character_view import create_character_view


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _character(raw_id=80101001, name="测试角色", skill="造成 100 点伤害"):
    return {
        "raw_id": raw_id,
        "name": name,
        "element_type": 1,
        "max_hp": 100,
        "atk": 20,
        "def": 10,
        "skills": [{"name": "技能", "description": skill}],
    }


def test_character_page_owns_controls_and_exposes_semantic_signals(qapp):
    page = CharacterPage()

    assert page.objectName() == "viewContainer"
    assert page.character_table.columnCount() == 3
    assert page.character_profile_view is not None
    assert page.findChild(QObject, "pageHeader") is not None
    assert page.findChild(QObject, "pageCommandBar") is not None

    emitted = []
    page.refresh_requested.connect(lambda: emitted.append("refresh"))
    page.refresh_requested.emit()
    assert emitted == ["refresh"]


def test_character_service_restores_repository_without_parsing(tmp_path):
    data_dir = tmp_path / "character_data"
    lua_dir = tmp_path / "lua"
    lua_dir.mkdir()
    snapshot = {"1001": _character()}
    merge_snapshot(data_dir, 20260824, snapshot, source_dir=lua_dir)

    service = CharacterService(data_dir, lua_dir)
    state = service.load_local()

    assert state is not None
    assert state["source"] == "repository"
    assert state["version"] == 20260824
    assert state["characters_full"][1001]["name"] == "测试角色"


def test_character_controller_filters_and_marks_all_read(qapp, tmp_path):
    data_dir = tmp_path / "character_data"
    lua_dir = tmp_path / "lua"
    lua_dir.mkdir()
    service = CharacterService(data_dir, lua_dir)
    merged = service.merge_version(20260824, {1001: _character()}, str(lua_dir))
    page = CharacterPage()
    controller = CharacterController(page, service)

    controller._apply_state(merged["characters_full"], merged["unread"], 20260824)
    assert page.character_table.rowCount() == 1
    assert controller.has_unread is True

    controller.filter_characters("不存在")
    assert page.character_table.isRowHidden(0) is True
    controller.filter_characters("测试")
    assert page.character_table.isRowHidden(0) is False

    controller.mark_all_read()
    assert controller.has_unread is False
    assert page.character_table.item(0, 2).text() == ""


def test_legacy_character_view_is_a_compatibility_wrapper(qapp):
    page, controls = create_character_view()

    assert isinstance(page, CharacterPage)
    assert controls["character_table"] is page.character_table
    assert controls["btn_mark_all_read"] is page.btn_mark_all_read


def test_character_feature_files_keep_ui_and_service_boundaries():
    from pathlib import Path

    feature_dir = Path(__file__).parents[1] / "app" / "features" / "characters"
    page_text = (feature_dir / "page.py").read_text(encoding="utf-8")
    service_text = (feature_dir / "service.py").read_text(encoding="utf-8")

    assert "parent._" not in page_text
    assert "controls =" not in page_text
    assert "PySide6" not in service_text


def test_character_service_cache_fallback(tmp_path):
    data_dir = tmp_path / "character_data"
    lua_dir = tmp_path / "lua"
    lua_dir.mkdir()
    service = CharacterService(data_dir, lua_dir)
    service.save_legacy_cache({1001: _character()}, lua_dir)

    state = service.load_local()
    assert state["source"] == "cache"
    payload = json.loads(service.cache_file.read_text(encoding="utf-8"))
    assert payload["characters_full"]["1001"]["name"] == "测试角色"
