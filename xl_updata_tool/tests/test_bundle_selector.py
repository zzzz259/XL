import json

from app.features.importer.bundle_selector import select_audio_bundles, select_lua_bundles


def test_select_lua_bundles_uses_container_and_source_mapping(tmp_path):
    first = tmp_path / "a.bundle"
    second = tmp_path / "b.bundle"
    third = tmp_path / "c.bundle"
    for path in (first, second, third):
        path.write_bytes(b"bundle")
    map_path = tmp_path / "_map" / "assets_map.json"
    map_path.parent.mkdir()
    map_path.write_text(json.dumps([
        {"Container": "assets/lua/BaseCard.lua.bytes", "Source": str(second)},
        {"Container": "assets/fmodassets/voice.bytes", "Source": str(third)},
    ]), encoding="utf-8")

    selected, mapped, asset_count = select_lua_bundles(
        [str(first), str(second), str(third)], map_path
    )

    assert selected == [str(second)]
    assert mapped is True
    assert asset_count == 1


def test_select_lua_bundles_falls_back_when_map_is_missing(tmp_path):
    paths = [str(tmp_path / "a.bundle"), str(tmp_path / "b.bundle")]

    selected, mapped, asset_count = select_lua_bundles(paths, tmp_path / "missing.json")

    assert selected == paths
    assert mapped is False
    assert asset_count == 0


def test_select_audio_bundles_uses_all_fmodassets_containers(tmp_path):
    first = tmp_path / "a.bundle"
    second = tmp_path / "b.bundle"
    third = tmp_path / "c.bundle"
    for path in (first, second, third):
        path.write_bytes(b"bundle")
    map_path = tmp_path / "_map" / "assets_map.json"
    map_path.parent.mkdir()
    map_path.write_text(json.dumps([
        {"Container": "assets/fmodassets/bgm/bgm_system/event_33.bytes", "Source": str(second)},
        {"Container": "assets/fmodassets/voice_cn/btl/116.bytes", "Source": str(third)},
        {"Container": "assets/lua/BaseCard.lua.bytes", "Source": str(first)},
    ]), encoding="utf-8")

    selected, mapped, asset_count = select_audio_bundles(
        [str(first), str(second), str(third)], map_path
    )

    assert selected == [str(second), str(third)]
    assert mapped is True
    assert asset_count == 2
