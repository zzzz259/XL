import json

import pytest

from bot_app.matcher import (
    VIA_ALIAS,
    VIA_CN,
    VIA_ID,
    VIA_INITIAL,
    VIA_PINYIN,
    CharacterMatcher,
    render_candidates_message,
    Match,
)

CHARACTERS = {
    "10000101": {"name": "菲尼斯/Finis", "star": 5},
    "10000222": {"name": "迷迭香/Rosemary", "star": 6},
    "10000333": {"name": "列蒂西雅/Lettisia", "star": 5},
    "10000444": {"name": "列蒂西雅·泳装/LettisiaSwim", "star": 5},
    "10000555": {"name": "艾莉丝/Alice", "star": 4},
    "10000666": {"name": "可莉·火花骑士/KleeSpark", "star": 5},
}


def make_dirs(tmp_path, aliases=None, learned=None):
    data_dir = tmp_path / "data"
    char_file = data_dir / "character_data" / "current.json"
    char_file.parent.mkdir(parents=True, exist_ok=True)
    char_file.write_text(
        json.dumps({"characters": CHARACTERS}, ensure_ascii=False), encoding="utf-8"
    )
    if aliases is not None:
        (data_dir / "aliases.json").write_text(
            json.dumps(aliases, ensure_ascii=False), encoding="utf-8"
        )
    if learned is not None:
        (data_dir / "learned_aliases.json").write_text(
            json.dumps(learned, ensure_ascii=False), encoding="utf-8"
        )
    return char_file, data_dir


def make_matcher(tmp_path, aliases=None, learned=None):
    char_file, data_dir = make_dirs(tmp_path, aliases, learned)
    return CharacterMatcher(char_file, data_dir)


# ---------- 评分档位 ----------

def test_cn_name_exact_direct_100(tmp_path):
    outcome = make_matcher(tmp_path).match("菲尼斯")
    assert outcome.kind == "direct"
    assert outcome.match.score == 100.0
    assert outcome.match.via == VIA_CN
    assert outcome.match.character_id == "10000101"
    assert outcome.match.star == 5


def test_character_id_exact_direct(tmp_path):
    outcome = make_matcher(tmp_path).match("10000101")
    assert outcome.kind == "direct"
    assert outcome.match.via == VIA_ID


def test_en_name_case_insensitive_98(tmp_path):
    outcome = make_matcher(tmp_path).match("fINis")
    assert outcome.kind == "direct"  # 98 >= 95
    assert outcome.match.score == 98.0
    assert outcome.match.via == "英文名"


def test_manual_alias_exact_100(tmp_path):
    matcher = make_matcher(tmp_path, aliases={"菲尼斯": ["火女"]})
    outcome = matcher.match("火女")
    assert outcome.kind == "direct"
    assert outcome.match.score == 100.0
    assert outcome.match.via == VIA_ALIAS


def test_pinyin_full_direct_96(tmp_path):
    outcome = make_matcher(tmp_path).match("feinisi")
    assert outcome.kind == "direct"
    assert outcome.match.score == 96.0
    assert outcome.match.via == VIA_PINYIN
    assert outcome.match.name == "菲尼斯"


def test_pinyin_initials_guess_94(tmp_path):
    outcome = make_matcher(tmp_path).match("fns")
    assert outcome.kind == "guess"  # 94，且无接近的第二候选
    assert outcome.match.score == 94.0
    assert outcome.match.via == VIA_INITIAL


@pytest.mark.parametrize("variant", ["菲尼丝", "菲妮斯", "菲尼思"])  # 同音字（思/丝/妮 ≈ 斯/尼）
def test_homophone_characters_direct(tmp_path, variant):
    outcome = make_matcher(tmp_path).match(variant)
    assert outcome.kind == "direct"
    assert outcome.match.name == "菲尼斯"


def test_fuzzy_edit_distance_guess(tmp_path):
    # 7 字名错 1 字：ratio ≈ 0.857 → 90+ 档
    outcome = make_matcher(tmp_path).match("可莉·火花骑土")
    assert outcome.kind == "guess"
    assert outcome.match.name == "可莉·火花骑士"
    assert outcome.match.score >= 90.0


def test_contain_goes_candidates(tmp_path):
    outcome = make_matcher(tmp_path).match("迷迭")
    assert outcome.kind == "candidates"
    assert outcome.candidates[0].name == "迷迭香"
    assert all(m.score < 85 for m in outcome.candidates)


def test_not_found_below_70(tmp_path):
    assert make_matcher(tmp_path).match("zzzzzz").kind == "not_found"
    assert make_matcher(tmp_path).match("   ").kind == "not_found"


# ---------- 阈值判定 ----------

def test_insufficient_margin_goes_candidates(tmp_path):
    # 两个角色同别名 → 同分 100，分差 0 → 候选
    matcher = make_matcher(tmp_path, aliases={"列蒂西雅": ["列蒂"], "列蒂西雅·泳装": ["列蒂"]})
    outcome = matcher.match("列蒂")
    assert outcome.kind == "candidates"
    names = [m.name for m in outcome.candidates]
    assert "列蒂西雅" in names and "列蒂西雅·泳装" in names


def test_guess_requires_margin_5(tmp_path):
    # "列蒂西雅" 的拼音全拼直达 96（直回档，无需分差）；构造 85~95 且唯一高分 → guess
    matcher = make_matcher(tmp_path)
    outcome = matcher.match("midiexiang")  # 迷迭香拼音全拼 96 → direct
    assert outcome.kind == "direct"
    # 85~95 唯一候选：可莉·火花骑土（模糊相似 94.x，无第二高分）
    outcome2 = matcher.match("可莉·火花骑土")
    assert outcome2.kind == "guess"


def test_clean_query_strips_quotes(tmp_path):
    outcome = make_matcher(tmp_path).match("“菲尼斯”")
    assert outcome.kind == "direct"


# ---------- 候选消息 ----------

def test_render_candidates_message(tmp_path):
    options = [
        Match(character_id="1", name="列蒂西雅", star=5, score=83.0, via="名称包含"),
        Match(character_id="2", name="列蒂西雅·泳装", star=5, score=81.0, via="名称包含"),
    ]
    text = render_candidates_message(options)
    assert text == (
        "没有完全一致的角色，你是不是想找：\n"
        "① 列蒂西雅 ★5\n"
        "② 列蒂西雅·泳装 ★5\n"
        "③ 都不是\n"
        "回复数字 1/2/3 选择（仅本次提问有效，120秒内）"
    )


def test_render_candidates_without_star(tmp_path):
    options = [Match(character_id="1", name="某角色", star=0, score=80.0, via="x")]
    text = render_candidates_message(options)
    assert "① 某角色\n" in text
    assert "★" not in text


# ---------- 纠错学习 ----------

def test_record_selection_counts_and_upgrade(tmp_path):
    matcher = make_matcher(tmp_path)
    assert matcher.match("火女").kind == "not_found"

    r1 = matcher.record_selection("火女", "10000101", "u1")
    assert r1["count"] == 1 and r1["users"] == 1 and not r1["upgraded"]
    # 同一用户重复选：次数+1 但不升级（需要 ≥2 不同用户）
    r2 = matcher.record_selection("火女", "10000101", "u1")
    assert r2["count"] == 2 and r2["users"] == 1 and not r2["upgraded"]
    assert matcher.match("火女").kind == "not_found"  # 尚未升级
    # 第二个用户选定同一角色 → 升级
    r3 = matcher.record_selection("火女", "10000101", "u2")
    assert r3["upgraded"]
    outcome = matcher.match("火女")
    assert outcome.kind == "direct"
    assert outcome.match.via == VIA_ALIAS
    assert outcome.match.name == "菲尼斯"
    # 不污染人工 aliases.json；只写 learned_aliases.json
    assert not (tmp_path / "data" / "aliases.json").exists()
    learned = json.loads(
        (tmp_path / "data" / "learned_aliases.json").read_text(encoding="utf-8")
    )
    assert learned["火女"]["10000101"] == 3
    assert sorted(learned["火女"]["users"]) == ["u1", "u2"]


def test_learned_alias_survives_restart(tmp_path):
    matcher = make_matcher(tmp_path)
    matcher.record_selection("火女", "10000101", "u1")
    matcher.record_selection("火女", "10000101", "u2")
    # 新实例（模拟重启）：从 learned_aliases.json 重建升级别名
    char_file, data_dir = make_dirs(tmp_path)
    reloaded = CharacterMatcher(char_file, data_dir)
    assert reloaded.match("火女").kind == "direct"


def test_record_selection_skips_existing_alias(tmp_path):
    matcher = make_matcher(tmp_path, aliases={"菲尼斯": ["火女"]})
    assert matcher.record_selection("菲尼斯", "10000101", "u1") is None   # 官方中文名
    assert matcher.record_selection("finis", "10000101", "u1") is None    # 官方英文名
    assert matcher.record_selection("火女", "10000101", "u1") is None     # 人工别名
    assert not (tmp_path / "data" / "learned_aliases.json").exists()


def test_record_selection_skips_unknown_character(tmp_path):
    matcher = make_matcher(tmp_path)
    assert matcher.record_selection("某词", "99999999", "u1") is None


def test_learned_file_preloaded_upgrades_alias(tmp_path):
    learned = {"呆毛王": {"10000222": 2, "users": ["a", "b"]}}
    matcher = make_matcher(tmp_path, learned=learned)
    outcome = matcher.match("呆毛王")
    assert outcome.kind == "direct"
    assert outcome.match.name == "迷迭香"


def test_split_users_do_not_upgrade(tmp_path):
    # 两个用户选定不同角色 → 不升级（学习记录仍在，但不进别名表）
    matcher = make_matcher(tmp_path)
    matcher.record_selection("海猫", "10000222", "u1")
    r = matcher.record_selection("海猫", "10000555", "u2")
    assert not r["upgraded"]
    assert not matcher.is_alias_of("海猫", "10000222")
    assert not matcher.is_alias_of("海猫", "10000555")
    # 但次数与 users 都累计了
    learned = json.loads(
        (tmp_path / "data" / "learned_aliases.json").read_text(encoding="utf-8")
    )
    assert learned["海猫"]["users"] == ["u1", "u2"]
