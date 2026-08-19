import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from app.core.character_profile import build_character_profile
from app.ui.widgets.character_profile import CharacterProfileView


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _character():
    return {
        "raw_id": 80100001,
        "name": "角色甲/备用名",
        "description": "这是角色简介。",
        "star": 6,
        "profession": "先锋",
        "element": "火",
        "faction": "测试阵营",
        "cv": "测试声优",
        "init_hp": 100,
        "max_hp": 1000,
        "init_atk": 20,
        "max_atk": 200,
        "init_def": 10,
        "max_def": 100,
        "crt": 1250,
        "normal_skill": "造成 30% 伤害，持续 2 秒。",
        "passive_skill_1": "攻击提升 +10 点",
        "awakening_skill_1": "冷却减少 1.5 秒",
        "breakthrough_costs": ["素材一", "素材二"],
        "personal_info": "个人资料",
        "voice_1": "你好。",
        "badge_info": "推荐徽章",
    }


def test_build_character_profile_groups_wiki_fields_without_qt():
    profile = build_character_profile(_character())

    assert profile.name == "角色甲"
    assert profile.raw_id == "80100001"
    assert profile.summary == "这是角色简介。"
    assert [(item.label, item.initial, item.maximum) for item in profile.primary_stats] == [
        ("生命", "100", "1000"),
        ("攻击", "20", "200"),
        ("防御", "10", "100"),
    ]
    assert profile.secondary_stats[0].value == "12%"
    assert [item.label for item in profile.skills] == ["普通技能", "被动技能 1", "觉醒技能 1"]
    assert profile.progression[0].value == "素材一"
    assert profile.story[0].label == "个人情报"
    assert profile.voices[0].label == "成员报道"
    assert profile.badge_info == "推荐徽章"


def test_character_profile_view_toggles_number_highlight(qapp):
    view = CharacterProfileView()
    view.set_profile(build_character_profile(_character()))

    description = view.findChildren(QLabel, "skillDescription")[0]
    assert "<span" not in description.text()

    view.number_highlight_button.click()
    assert "<span" in description.text()
    assert "30%" in description.text()
    assert view.number_highlight_button.text() == "关闭数字高亮"

    view.number_highlight_button.click()
    assert "<span" not in description.text()
    assert view.number_highlight_button.text() == "高亮技能数字"
