from pathlib import Path
from dataclasses import replace

from app.features.preview.portrait_config import builder
from app.features.preview.portrait_config.asset_index import AssetIndex, IndexedAsset
from app.features.preview.portrait_config.cache import PortraitSceneCache
from app.features.preview.portrait_config.lua_config import PortraitRoot
from app.features.preview.portrait_config.model import AssetRef
from app.features.preview.portrait_config.unity_scene import (
    TransformNode,
    UnityObjectPointer,
    UnitySceneParseResult,
    UnityScenePart,
)

VERSION = 402
PREFAB_HASH = "a" * 32
SKELETON_HASH = "b" * 32


def _asset(name, bundle_hash, path_id, bundle_root):
    path = bundle_root / bundle_hash
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bundle_hash.encode("ascii"))
    return IndexedAsset(
        name=name, container="Assets/Portrait", bundle_hash=bundle_hash,
        path_id=path_id, dependencies=(), version_timestamp=VERSION,
        bundle_path=path, bundle_present=True, evidence=f"catalog#/{bundle_hash}/{path_id}",
    )


def _root(tmp_path, *, skin_id="12002183", prefab_name="cardspine_10118_3.prefab",
          version=VERSION):
    source = tmp_path / str(version) / "BaseFashion.lua"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("return { [12002183] = { show_spine = 'cardspine_10118_3.prefab' } }\n",
                      encoding="utf-8")
    return PortraitRoot(
        skin_id, version, prefab_name, (394.0, -816.0),
        (("card_id", "10118"), ("entry_id", skin_id)),
        f"{source}:1", field_evidence=(("show_spine", f"{source}:1"),),
    )


def _parsed_scene():
    part = UnityScenePart(
        game_object_ref=AssetRef(PREFAB_HASH, 10),
        component_ref=AssetRef(PREFAB_HASH, 11),
        game_object_name="cardspine_10118_3",
        component_type="SkeletonAnimation",
        hierarchy_path="root/cardspine_10118_3",
        transform_chain=(TransformNode(
            AssetRef(PREFAB_HASH, 12), (4.0, 5.0, 0.0), (0.0, 0.0, 0.0, 1.0),
            (1.0, 1.0, 1.0), 0,
        ),),
        world_position=(4.0, 5.0, 0.0),
        enabled=True,
        sorting_layer_value=2,
        sorting_order=1,
        renderer_depth=0.0,
        skeleton_ref=AssetRef(SKELETON_HASH, 20),
        skeleton_json_ref=AssetRef(SKELETON_HASH, 21),
        atlas_refs=(AssetRef(SKELETON_HASH, 22),),
        animation="idle",
        loop=True,
        initial_skin="default",
    )
    return UnitySceneParseResult(
        PREFAB_HASH, (), (), (part,), (), (), version_timestamp=VERSION,
    )


def _index(tmp_path, *, duplicate=False):
    root = tmp_path / "bundles"
    assets = [
        _asset("cardspine_10118_3.prefab", PREFAB_HASH, 1, root),
        _asset("hero-data", SKELETON_HASH, 20, root),
        _asset("hero.json", SKELETON_HASH, 21, root),
        _asset("hero.atlas", SKELETON_HASH, 22, root),
        _asset("game-object", PREFAB_HASH, 10, root),
        _asset("skeleton-component", PREFAB_HASH, 11, root),
        _asset("transform", PREFAB_HASH, 12, root),
    ]
    if duplicate:
        assets.append(_asset("cardspine_10118_3.prefab", "c" * 32, 99, root))
    return AssetIndex(VERSION, tuple(assets))


def test_builder_maps_exact_prefab_scene_and_preserves_transform_and_order(tmp_path, monkeypatch):
    selected = _parsed_scene()
    monkeypatch.setattr(builder, "parse_unity_scene", lambda *_args: selected)

    scenes = builder.build_portrait_scenes(
        VERSION, [_root(tmp_path)], _index(tmp_path), game_version="v402",
    )

    assert len(scenes) == 1
    scene = scenes[0]
    assert scene.prefab == AssetRef(PREFAB_HASH, 1)
    assert scene.character_id == "10118"
    assert scene.skin_id == "12002183"
    assert scene.game_version == "v402"
    assert scene.parts[0].spine.default_animation == "idle"
    assert scene.parts[0].transform_chain[0].local_position == (4.0, 5.0, 0.0)
    assert scene.parts[0].sorting_layer_value == 2
    assert scene.parts[0].stable_order == 0
    assert not [item for item in scene.validate() if item.severity == "error"]


def test_missing_or_ambiguous_prefab_is_explicit_and_never_filename_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        builder, "parse_unity_scene",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not parse an uncertain prefab")),
    )

    missing = builder.build_portrait_scenes(
        VERSION, [_root(tmp_path, prefab_name="different.prefab")], _index(tmp_path),
    )[0]
    ambiguous = builder.build_portrait_scenes(
        VERSION, [_root(tmp_path)], _index(tmp_path, duplicate=True),
    )[0]

    assert missing.prefab is None
    assert any(item.code == "prefab_asset_missing" for item in missing.diagnostics)
    missing_round_trip = type(missing).from_dict(missing.to_dict())
    assert missing_round_trip.prefab is None
    assert any(item.code == "prefab_asset_missing" for item in missing_round_trip.diagnostics)
    assert ambiguous.prefab is None
    assert any(item.code == "ambiguous_prefab_asset" for item in ambiguous.diagnostics)


def test_builder_resolves_assetstudio_container_and_keeps_only_its_renderers(tmp_path, monkeypatch):
    import json

    bundle_root = tmp_path / "bundles"
    bundle_root.mkdir()
    bundle = bundle_root / f"{PREFAB_HASH}.bundle"
    bundle.write_bytes(b"bundle")
    catalog = tmp_path / "assets_map.json"
    catalog.write_text(json.dumps([
        {"Name": "hero-data", "Container": "assets/cardspine/cardspine_10118_3.prefab",
         "Source": f"C:/input/{PREFAB_HASH}.bundle", "PathID": 20, "Type": "MonoBehaviour"},
        {"Name": "cardspine_10118_3.skel", "Container": "assets/cardspine/cardspine_10118_3.prefab",
         "Source": f"C:/input/{PREFAB_HASH}.bundle", "PathID": 21, "Type": "TextAsset"},
        {"Name": "cardspine_10118_3.atlas", "Container": "assets/cardspine/cardspine_10118_3.prefab",
         "Source": f"C:/input/{PREFAB_HASH}.bundle", "PathID": 22, "Type": "TextAsset"},
        {"Name": "other.skel", "Container": "assets/cardspine/other.prefab",
         "Source": f"C:/input/{PREFAB_HASH}.bundle", "PathID": 31, "Type": "TextAsset"},
    ]), encoding="utf-8")
    index = AssetIndex.from_catalog(catalog, bundle_root, VERSION)
    parsed = _parsed_scene()
    selected_part = replace(
        parsed.parts[0], skeleton_ref=AssetRef(PREFAB_HASH, 20),
        skeleton_json_ref=AssetRef(PREFAB_HASH, 21),
        atlas_refs=(AssetRef(PREFAB_HASH, 22),),
    )
    other_part = replace(
        selected_part, component_ref=AssetRef(PREFAB_HASH, 32),
        skeleton_json_ref=AssetRef(PREFAB_HASH, 31),
        atlas_refs=(AssetRef(PREFAB_HASH, 33),),
    )
    unrelated_part = replace(
        selected_part, component_ref=AssetRef(PREFAB_HASH, 44),
        skeleton_json_ref=AssetRef(PREFAB_HASH, 999),
        atlas_refs=(AssetRef(PREFAB_HASH, 22),),
    )
    monkeypatch.setattr(builder, "parse_unity_scene",
                        lambda *_args: replace(parsed, parts=(selected_part, other_part, unrelated_part)))

    scene = builder.build_portrait_scenes(VERSION, [_root(tmp_path)], index)[0]

    assert scene.prefab is None
    assert scene.prefab_container == "assets/cardspine/cardspine_10118_3.prefab"
    assert len(scene.parts) == 1
    assert scene.parts[0].object_ref == selected_part.component_ref
    assert not [item for item in scene.validate() if item.severity == "error"]
    restored = type(scene).from_dict(scene.to_dict())
    assert restored.prefab is None
    assert restored.prefab_container == scene.prefab_container


def test_builder_rejects_container_name_shared_across_bundles(tmp_path, monkeypatch):
    first = _asset("hero.skel", PREFAB_HASH, 21, tmp_path / "bundles")
    second = _asset("hero.skel", "c" * 32, 22, tmp_path / "bundles")
    first = replace(first, container="assets/cardspine/cardspine_10118_3.prefab")
    second = replace(second, container="assets/cardspine/cardspine_10118_3.prefab")
    index = AssetIndex(VERSION, (first, second))
    monkeypatch.setattr(
        builder, "parse_unity_scene",
        lambda *_args: (_ for _ in ()).throw(AssertionError("ambiguous container must not parse")),
    )

    scene = builder.build_portrait_scenes(VERSION, [_root(tmp_path)], index)[0]

    assert scene.prefab is None
    assert any(item.code == "ambiguous_prefab_asset" for item in scene.diagnostics)


def test_builder_rejects_same_container_basename_in_different_paths(tmp_path, monkeypatch):
    first = _asset("hero.skel", PREFAB_HASH, 21, tmp_path / "bundles")
    second = replace(
        first, name="other.skel", path_id=22,
        container="assets/other/cardspine_10118_3.prefab",
    )
    first = replace(first, container="assets/cardspine/cardspine_10118_3.prefab")
    index = AssetIndex(VERSION, (first, second))
    monkeypatch.setattr(
        builder, "parse_unity_scene",
        lambda *_args: (_ for _ in ()).throw(AssertionError("basename collision must not parse")),
    )

    scene = builder.build_portrait_scenes(VERSION, [_root(tmp_path)], index)[0]

    assert scene.prefab is None
    assert any(item.code == "ambiguous_prefab_asset" for item in scene.diagnostics)


def test_root_or_index_version_mismatch_does_not_parse_bundle(tmp_path, monkeypatch):
    root = _root(tmp_path, version=401)
    monkeypatch.setattr(
        builder, "parse_unity_scene",
        lambda *_args: (_ for _ in ()).throw(AssertionError("wrong version must not parse")),
    )

    result = builder.build_portrait_scenes(VERSION, [root], _index(tmp_path))[0]

    assert result.prefab is None
    assert any(item.code == "root_version_mismatch" for item in result.diagnostics)
    try:
        builder.build_portrait_scenes(VERSION, [], AssetIndex(401, ()))
    except ValueError as exc:
        assert "version" in str(exc).casefold()
    else:
        raise AssertionError("mismatched AssetIndex must be rejected")


def test_repeat_build_uses_cache_without_reparsing_and_versions_are_isolated(tmp_path, monkeypatch):
    selected = _parsed_scene()
    calls = []

    def parse(*args):
        calls.append(args)
        return selected

    monkeypatch.setattr(builder, "parse_unity_scene", parse)
    cache = PortraitSceneCache(tmp_path / "cache", parser_version="p1")
    index = _index(tmp_path)
    root = _root(tmp_path)

    first = builder.build_portrait_scenes(VERSION, [root], index, cache=cache)
    second = builder.build_portrait_scenes(VERSION, [root], index, cache=cache)
    other_version_key = cache.key_for(VERSION + 1, root.skin_id)

    assert first == second
    assert len(calls) == 1
    assert other_version_key != cache.key_for(VERSION, root.skin_id)


def test_builder_resolves_component_pointers_by_source_field_identity(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.features.preview.portrait_config import unity_scene

    monkeypatch.setattr(unity_scene, "_load_environment", lambda path: SimpleNamespace(objects=[
        SimpleNamespace(path_id=20, type=SimpleNamespace(name="MonoBehaviour"),
                        assets_file=SimpleNamespace(name=f"CAB-{path.name}")),
    ]))
    parsed = _parsed_scene()
    part = replace(
        parsed.parts[0], skeleton_ref=None, skeleton_json_ref=None, atlas_refs=(),
    )
    pointer = UnityObjectPointer(
        part.component_ref, "skeletonDataAsset", 1, 20,
        f"CAB-{SKELETON_HASH}", f"archive:/CAB-{SKELETON_HASH}/CAB-{SKELETON_HASH}",
    )
    parsed = replace(parsed, parts=(part,), references=(pointer,))
    monkeypatch.setattr(builder, "parse_unity_scene", lambda *_args: parsed)

    result = builder.build_portrait_scenes(
        VERSION, [_root(tmp_path)], _index(tmp_path),
    )[0]

    assert result.parts[0].spine.skeleton == AssetRef(SKELETON_HASH, 20)
    assert SKELETON_HASH in result.source_bundles
    assert result.source_bundle_details[0].path
    assert result.asset_dependencies[0].target == AssetRef(SKELETON_HASH, 20)
    assert not [item for item in result.diagnostics if item.code.startswith("dependency_")]


def test_lua_config_content_change_invalidates_only_that_skin_cache(tmp_path, monkeypatch):
    selected = _parsed_scene()
    calls = []
    monkeypatch.setattr(builder, "parse_unity_scene", lambda *_args: calls.append(1) or selected)
    cache = PortraitSceneCache(tmp_path / "cache", parser_version="p1")
    index = _index(tmp_path)
    root = _root(tmp_path)

    builder.build_portrait_scenes(VERSION, [root], index, cache=cache)
    builder.build_portrait_scenes(VERSION, [root], index, cache=cache)
    Path(root.evidence.rsplit(":", 1)[0]).write_text(
        "return { [12002183] = { show_spine = 'renamed.prefab' } }\n", encoding="utf-8",
    )
    renamed_prefab = _asset("renamed.prefab", PREFAB_HASH, 2, tmp_path / "bundles")
    index = AssetIndex(VERSION, (*index.assets, renamed_prefab))
    builder.build_portrait_scenes(
        VERSION, [replace(root, prefab_name="renamed.prefab")], index, cache=cache,
    )

    assert len(calls) == 2


def test_unrelated_lua_entry_change_does_not_invalidate_other_skin_cache(tmp_path, monkeypatch):
    selected = _parsed_scene()
    calls = []
    monkeypatch.setattr(builder, "parse_unity_scene", lambda *_args: calls.append(1) or selected)
    cache = PortraitSceneCache(tmp_path / "cache", parser_version="p1")
    index = _index(tmp_path)
    root_a = _root(tmp_path, skin_id="12002183")
    root_b = _root(tmp_path, skin_id="12002184")

    builder.build_portrait_scenes(VERSION, [root_a, root_b], index, cache=cache)
    assert len(calls) == 2

    source = Path(root_a.evidence.rsplit(":", 1)[0])
    source.write_text(source.read_text(encoding="utf-8") + "-- unrelated Lua entry changed\n", encoding="utf-8")
    builder.build_portrait_scenes(VERSION, [root_b], index, cache=cache)
    assert len(calls) == 2

    changed_root_a = replace(root_a, position=(1.0, 2.0))
    builder.build_portrait_scenes(VERSION, [changed_root_a], index, cache=cache)
    assert len(calls) == 3


def test_missing_skeleton_data_marks_scene_incomplete_and_prevents_cache(tmp_path, monkeypatch):
    parsed = _parsed_scene()
    missing_skeleton = replace(
        parsed.parts[0], skeleton_ref=None, skeleton_json_ref=None, atlas_refs=(),
    )
    parsed = replace(parsed, parts=(missing_skeleton,), references=())
    calls = []
    monkeypatch.setattr(builder, "parse_unity_scene", lambda *_args: calls.append(1) or parsed)
    cache = PortraitSceneCache(tmp_path / "cache", parser_version="p1")
    index = _index(tmp_path)
    root = _root(tmp_path)

    first = builder.build_portrait_scenes(VERSION, [root], index, cache=cache)[0]
    second = builder.build_portrait_scenes(VERSION, [root], index, cache=cache)[0]

    assert any(item.code == "spine_resources_incomplete" for item in first.diagnostics)
    assert any(item.code == "spine_resources_incomplete" for item in second.diagnostics)
    assert len(calls) == 2


def test_multiple_roots_have_stable_skin_order(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "parse_unity_scene", lambda *_args: _parsed_scene())
    roots = [
        _root(tmp_path, skin_id="12002184"),
        _root(tmp_path, skin_id="12002182"),
    ]

    result = builder.build_portrait_scenes(VERSION, roots, _index(tmp_path))

    assert [scene.skin_id for scene in result] == ["12002182", "12002184"]
