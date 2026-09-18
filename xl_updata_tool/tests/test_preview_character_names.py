import json

from app.features.preview.character_names import (
    CharacterNameResolver,
    display_resource_family,
    display_role_label,
    display_skin_label,
    display_skin_number,
)


def _write_repository(output_root, current, history=None):
    data_dir = output_root / "character_data"
    data_dir.mkdir(parents=True)
    repository = {
        "schema_version": 1,
        "current_version": "new",
        "current_characters": current,
        "history": history or {},
    }
    (data_dir / "characters_repository.json").write_text(
        json.dumps(repository, ensure_ascii=False), encoding="utf-8"
    )


def test_resolver_prefers_current_repository_and_falls_back_to_history(tmp_path):
    output_root = tmp_path / "output"
    history_file = tmp_path / "old.json"
    history_file.write_text(
        json.dumps({"characters_full": {"10080": {"name": "旧角色/Old"}}}),
        encoding="utf-8",
    )
    _write_repository(
        output_root,
        {"10080": {"name": "当前角色/Current"}},
        {"old": {"snapshot": str(history_file)}},
    )

    resolver = CharacterNameResolver.from_output_root(output_root)

    assert resolver.resolve("10080") == "当前角色"
    assert resolver.resolve("10081") is None

    history_file.write_text(
        json.dumps({"characters_full": {"10081": {"name": "旧角色/Old"}}}),
        encoding="utf-8",
    )
    resolver = CharacterNameResolver.from_output_root(output_root)
    assert resolver.resolve("10081") == "旧角色"


def test_resolver_uses_lua_fashion_card_id_as_spine_id_alias(tmp_path):
    output_root = tmp_path / "output"
    _write_repository(output_root, {"10000180": {"name": "鬼侍/Ghostsamurai"}})
    lua_dir = output_root / "lua" / "new"
    lua_dir.mkdir(parents=True)
    (lua_dir / "BaseFashion.lua").write_text(
        '[1] = { card_id = 10000180, spd = "battlespine_10080.prefab", '
        'show_spine = "cardspine_10080_1.prefab" },',
        encoding="utf-8",
    )

    resolver = CharacterNameResolver.from_output_root(output_root)

    assert resolver.resolve("10080") == "鬼侍"


def test_display_labels_keep_resource_families_distinct():
    assert display_resource_family("cardspine") == "角色立绘"
    assert display_resource_family("battlespine") == "战斗小人"
    assert display_resource_family("eventcovers") == "活动封面"
    assert display_role_label("10080", "cardspine", None) == "10080 · 角色立绘"


def test_skin_number_uses_source_suffix_and_has_stable_non_numeric_fallback():
    assert display_skin_number("cardspine_10080_4.skel", "10080", "cardspine") == "4"
    assert display_skin_number("cardspine_10080_4_bg.skel", "10080", "cardspine") == "4"
    assert display_skin_number("cardspine_10080_special.skel", "10080", "cardspine") == "special"
    assert display_skin_number("misc_model.skel", None, "spine") == "misc_model"


def test_display_skin_label_uses_resource_family_and_source_number():
    assert display_skin_label("battlespine_10080_2.skel", "10080", "battlespine") == "战斗小人 2"
    assert display_skin_label("cardspine_10080_4_bg.skel", "10080", "cardspine") == "角色立绘 4"


def test_display_skin_label_does_not_invent_number_for_unsuffixed_source():
    assert display_skin_label("battlespine_10118.skel", "10118", "battlespine") == "战斗小人 2"
