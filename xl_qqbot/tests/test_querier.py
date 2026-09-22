import json

from bot_app.querier import CharacterQuerier


def _make_data(tmp_path, characters, version=123):
    data_dir = tmp_path / "data"
    char_data = data_dir / "character_data" / "current.json"
    char_data.parent.mkdir(parents=True)
    char_data.write_text(json.dumps({"version": version, "characters": characters}, ensure_ascii=False), encoding="utf-8")
    (data_dir / "current_version.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    versions_dir = data_dir / "versions"
    cards = versions_dir / str(version) / "character_cards"
    cards.mkdir(parents=True)
    return char_data, versions_dir, cards


CHARACTERS = {
    "10000101": {"name": "菲尼斯/Finis"},
    "10000222": {"name": "迷迭香/Rosemary"},
}


def test_match_by_cn_name(tmp_path):
    char_data, versions_dir, cards = _make_data(tmp_path, CHARACTERS)
    (cards / "10000101_菲尼斯_角色档案_长图.png").write_bytes(b"png")
    result = CharacterQuerier(char_data, versions_dir).find("菲尼斯")
    assert result.status == "found"
    assert result.character_id == "10000101"
    assert result.image_path.name.startswith("10000101_")


def test_match_by_en_name_case_insensitive(tmp_path):
    char_data, versions_dir, cards = _make_data(tmp_path, CHARACTERS)
    (cards / "10000222_迷迭香_角色档案_长图.png").write_bytes(b"png")
    result = CharacterQuerier(char_data, versions_dir).find("rosemary")
    assert result.status == "found"
    assert result.character_id == "10000222"


def test_match_by_character_id(tmp_path):
    char_data, versions_dir, cards = _make_data(tmp_path, CHARACTERS)
    (cards / "10000101_菲尼斯_角色档案_长图.png").write_bytes(b"png")
    result = CharacterQuerier(char_data, versions_dir).find("10000101")
    assert result.status == "found"


def test_not_found(tmp_path):
    char_data, versions_dir, _ = _make_data(tmp_path, CHARACTERS)
    result = CharacterQuerier(char_data, versions_dir).find("不存在的角色")
    assert result.status == "not_found"


def test_character_exists_but_image_missing(tmp_path):
    char_data, versions_dir, _ = _make_data(tmp_path, CHARACTERS)
    result = CharacterQuerier(char_data, versions_dir).find("菲尼斯")
    assert result.status == "no_image"
    assert result.name == "菲尼斯"


def test_query_with_quotes_and_spaces(tmp_path):
    char_data, versions_dir, cards = _make_data(tmp_path, CHARACTERS)
    (cards / "10000101_菲尼斯_角色档案_长图.png").write_bytes(b"png")
    result = CharacterQuerier(char_data, versions_dir).find('  “菲尼斯” ')
    assert result.status == "found"


def test_extract_query_strips_mention_markup():
    from bot_app.main import _extract_query

    mentioned, query = _extract_query("<@AB6AD997A31245CD31CAF9D6D793F8F0> 菲尼斯", None)
    assert mentioned and query == "菲尼斯"

    mentioned, query = _extract_query("<@AB6AD997A31245CD31CAF9D6D793F8F0> rosemary", None)
    assert mentioned and query == "rosemary"

    mentioned, query = _extract_query("今天天气不错", None)
    assert not mentioned and query == ""

    mentioned, query = _extract_query("@星落罗伯特 迷迭香", None)
    assert mentioned and query == "迷迭香"


def test_extract_query_ignores_mentions_of_others():
    from bot_app.main import _extract_query

    # @别人（openid 不是机器人）不触发
    mentioned, query = _extract_query("<@SOMEONEELSE123> 菲尼斯", None, "AB6AD997A31245CD31CAF9D6D793F8F0")
    assert not mentioned and query == ""

    # @机器人（精确 openid）触发
    mentioned, query = _extract_query("<@AB6AD997A31245CD31CAF9D6D793F8F0> 菲尼斯", None, "AB6AD997A31245CD31CAF9D6D793F8F0")
    assert mentioned and query == "菲尼斯"
