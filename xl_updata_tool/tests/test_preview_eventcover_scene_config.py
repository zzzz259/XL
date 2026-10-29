from __future__ import annotations

from app.features.preview.portrait_config.lua_config import load_eventcover_roots


def test_reads_eventcover_bg_main_root_and_ignores_sequential_path_lookup(tmp_path):
    lua = tmp_path / "lua" / "402"
    lua.mkdir(parents=True)
    (lua / "BaseActivityStageConfig.lua").write_text(
        '''BaseActivityStageConfig = {
  [70440005] = {
    id = 70440005,
    bg_main = "Assets/Art/Models/UI_spine/prefab/Eventcovers/eventcovers_0005.prefab",
  },
}
''',
        encoding="utf-8",
    )
    (lua / "Activity5EntranceAnimWindow.lua").write_text(
        '''local PATH_LOOKUP = {
  [1] = "Assets/Art/Models/UI_spine/prefab/Eventcovers/eventcovers_0005_4.prefab",
  [2] = "Assets/Art/Models/UI_spine/prefab/Eventcovers/eventcovers_0005_1.prefab",
}
''',
        encoding="utf-8",
    )

    roots = load_eventcover_roots(tmp_path / "lua", 402)

    assert len(roots) == 1
    assert roots[0].skin_id == "eventcovers_0005"
    assert roots[0].prefab_name == (
        "Assets/Art/Models/UI_spine/prefab/Eventcovers/eventcovers_0005.prefab"
    )
    assert dict(roots[0].metadata)["entry_id"] == "70440005"
    assert "BaseActivityStageConfig.lua" in roots[0].evidence


def test_deduplicates_multiple_activity_rows_that_use_same_cover_root(tmp_path):
    lua = tmp_path / "lua" / "402"
    lua.mkdir(parents=True)
    (lua / "BaseActivityStageConfig.lua").write_text(
        '''BaseActivityStageConfig = {
  [1] = { id = 1, bg_main = "Assets/Eventcovers/eventcovers_0005.prefab" },
  [2] = { id = 2, bg_main = "Assets/Eventcovers/eventcovers_0005.prefab" },
}
''',
        encoding="utf-8",
    )

    roots = load_eventcover_roots(tmp_path / "lua", 402)

    assert [root.skin_id for root in roots] == ["eventcovers_0005"]


def test_ignores_comments_and_non_eventcover_bg_main(tmp_path):
    lua = tmp_path / "lua" / "402"
    lua.mkdir(parents=True)
    (lua / "BaseActivityStageConfig.lua").write_text(
        '''BaseActivityStageConfig = {
  [1] = {
    id = 1,
    -- bg_main = "Assets/Eventcovers/eventcovers_0001.prefab",
    bg_main = "Assets/Other/regular_background.prefab",
  },
}
''',
        encoding="utf-8",
    )

    assert load_eventcover_roots(tmp_path / "lua", 402) == []


def test_reads_only_requested_version_eventcover_config(tmp_path):
    for version, event_id in ((401, "0001"), (402, "0002")):
        lua = tmp_path / "lua" / str(version)
        lua.mkdir(parents=True)
        (lua / "BaseActivityStageConfig.lua").write_text(
            f'''BaseActivityStageConfig = {{
  [1] = {{ id = 1, bg_main = "Assets/Eventcovers/eventcovers_{event_id}.prefab" }},
}}
''',
            encoding="utf-8",
        )

    roots = load_eventcover_roots(tmp_path / "lua", 402)

    assert [root.skin_id for root in roots] == ["eventcovers_0002"]


def test_import_manifest_builder_receives_eventcover_scene_roots(tmp_path, monkeypatch):
    from app.features.preview.portrait_config import import_service
    from app.features.preview.portrait_config.asset_index import AssetIndex

    captured = []
    monkeypatch.setattr(import_service, "load_portrait_roots", lambda *_: [])
    monkeypatch.setattr(AssetIndex, "from_catalog", lambda *_, **_kwargs: type("Index", (), {
        "assets": (),
    })())

    def build(_version, roots, _index, **_kwargs):
        captured.extend(roots)
        return [type("Scene", (), {
            "diagnostics": (),
            "skin_id": roots[0].skin_id,
            "character_id": "",
            "parts": (),
            "prefab_container": roots[0].prefab_name,
        })()]

    monkeypatch.setattr(import_service, "build_portrait_scenes", build)
    data_root = tmp_path / "data"
    bundle_root = data_root / "bundles" / "402"
    bundle_root.mkdir(parents=True)
    catalog = bundle_root / "_map" / "assets_map.json"
    catalog.parent.mkdir()
    catalog.write_text("[]", encoding="utf-8")
    lua_root = tmp_path / "lua" / "402"
    lua_root.mkdir(parents=True)
    (lua_root / "BaseActivityStageConfig.lua").write_text(
        '''BaseActivityStageConfig = {
  [70440005] = { id = 70440005,
    bg_main = "Assets/Art/Models/UI_spine/prefab/Eventcovers/eventcovers_0005.prefab" },
}
''',
        encoding="utf-8",
    )

    result = import_service.generate_portrait_manifests(
        data_root, 402, catalog, tmp_path / "lua", bundle_root,
    )

    assert result.status == "complete"
    assert [root.skin_id for root in captured] == ["eventcovers_0005"]
    assert captured[0].prefab_name.endswith("eventcovers_0005.prefab")
