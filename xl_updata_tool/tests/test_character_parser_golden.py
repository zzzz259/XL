from pathlib import Path

from app.features.characters.parser import load_character_data


FIXTURE = Path(__file__).parent / "fixtures" / "character_parser"


def test_sanitized_character_fixture_preserves_complete_shape():
    characters, full, word_map = load_character_data(str(FIXTURE))

    assert characters == [{
        "name": "测试角色",
        "char_id": 80100001,
        "raw_id": 80100001,
        "display_index": 80100001,
    }]
    assert word_map[80100001] == "测试角色"
    character = full[80100001]
    assert character["name"] == "测试角色/Test Character"
    assert character["max_hp"] == 111
    assert character["max_atk"] == 40
    assert character["max_def"] == 40
    assert character["cv"] == "甲/乙"
    assert character["normal_skill_id"] == 700001
    assert len([key for key in character if key.startswith("voice_")]) == 34
    assert character["breakthrough_costs"][0] == "测试阵营 * 2"
