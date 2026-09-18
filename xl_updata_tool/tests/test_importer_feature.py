import re
from pathlib import Path

from app.features.importer.service import ImporterService
from app.features.importer.postprocessing import PostProcessorRegistry
from app.features.importer.spec import CATEGORY_DIRS, CATEGORY_ROOTS, EXPORT_SPECS, build_category_commands


def test_export_specs_cover_existing_categories_without_overlap():
    assert set(EXPORT_SPECS) == {"lua", "character", "fgui", "audio"}
    assert set(CATEGORY_DIRS) == set(EXPORT_SPECS)
    assert len(set(CATEGORY_DIRS.values())) == len(CATEGORY_DIRS)


def test_export_spec_commands_preserve_assetstudio_filters():
    commands = build_category_commands({"lua", "audio"})
    assert commands[0][0] == "导出 audio"
    assert commands[-1][0] == "导出 lua"
    assert any("assets/fmodassets/bgm" in args for _label, args in commands)
    assert commands[-1][1] == ["--types", "TextAsset", "--containers", "assets/lua"]


def test_character_export_specs_follow_verified_spine_containers():
    rules = EXPORT_SPECS["character"].rules
    containers = {rule.container_regex for rule in rules}

    assert "assets/art/models/cardspine" in containers
    assert "assets/art/models/battlespine" in containers
    assert "assets/art/models/ui_spine/prefab/eventcovers" in containers
    assert all("carspine" not in (rule.container_regex or "") for rule in rules)
    assert all("carspine" not in (rule.name_regex or "") for rule in rules)


def test_character_export_includes_standalone_texture_roots():
    assert "assets/art/texturesingle/bursthead" in CATEGORY_ROOTS["character"]
    assert "assets/art/texturesingle/lotterybg" in CATEGORY_ROOTS["character"]


def test_character_texture_rules_exclude_normal_and_effect_variants():
    texture_rules = [
        rule for rule in EXPORT_SPECS["character"].rules if rule.asset_type == "Texture2D"
    ]

    assert texture_rules
    for rule in texture_rules:
        assert rule.name_regex
        assert all(
            not re.fullmatch(rule.name_regex, name)
            for name in ("body_n", "body-e", "body_normal", "body.normalmap")
        )
        assert re.fullmatch(rule.name_regex, "body")


def test_unknown_export_category_is_rejected():
    try:
        build_category_commands({"lua", "not-a-category"})
    except ValueError as error:
        assert "not-a-category" in str(error)
    else:
        raise AssertionError("unknown export category must be rejected")


def test_importer_service_selects_mapped_bundles_and_builds_result(tmp_path):
    bundle_a = tmp_path / "a.bundle"
    bundle_b = tmp_path / "b.bundle"
    bundle_a.write_bytes(b"a")
    bundle_b.write_bytes(b"b")
    map_path = tmp_path / "assets_map.json"
    map_path.write_text(
        '[{"Source":"a.bundle","Container":"assets/lua/BaseCard.lua"}]',
        encoding="utf-8",
    )
    service = ImporterService(tmp_path / "material", tmp_path / "output" / "lua")

    selected, mapped, count = service.select_bundles("lua", [str(bundle_a), str(bundle_b)], map_path)
    result = service.result(
        {"lua"}, {"lua"}, published_outputs=[tmp_path / "output" / "lua" / "20260824"],
        lua_export_result={"version": 20260824},
    )

    assert selected == [str(bundle_a)]
    assert mapped is True
    assert count == 1
    assert result.succeeded
    assert result.postprocess_categories == frozenset({"lua"})
    assert result.lua_export_result["version"] == 20260824
    assert Path(result.published_outputs[0]).name == "20260824"


def test_importer_service_represents_legacy_full_import():
    result = ImporterService("material", "lua").result(
        (), {"all"}, message="完成"
    )
    assert result.categories == frozenset({"all"})
    assert result.succeeded


def test_importer_service_schedules_preview_processing_after_assets_are_committed():
    service = ImporterService("material", "lua")

    result = service.result({"character", "fgui"}, {"character", "fgui"})

    assert result.postprocess_categories == frozenset({"preview"})


def test_importer_service_schedules_preview_for_legacy_full_import():
    result = ImporterService("material", "lua").result((), {"all"})

    assert result.postprocess_categories == frozenset({"preview"})


def test_postprocessor_registry_filters_result_categories():
    result = ImporterService("material", "lua").result(
        {"lua", "audio"}, {"lua", "audio"}
    )
    registry = PostProcessorRegistry(("lua",))
    assert registry.pending(result) == frozenset({"lua"})
    assert registry.handles("lua")
    assert not registry.handles("audio")
