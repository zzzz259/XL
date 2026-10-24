import json

from app.features.preview.portrait_config.cache import PortraitSceneCache
from app.features.preview.portrait_config.model import PortraitScene, SceneDiagnostic
from app.features.preview.resource_catalog import load_portrait_scene_catalog
from app.features.preview.resource_model import portrait_scene_key, portrait_scene_status


def _scene(version, skin, *, diagnostics=(), prefab="cardspine_10080_4.prefab"):
    return PortraitScene(
        game_version="test",
        version_timestamp=str(version),
        entry_config_id=str(skin),
        character_id="10080",
        skin_id=str(skin),
        prefab=None,
        source_bundles=("bundle-a",),
        parts=(),
        diagnostics=tuple(diagnostics),
        prefab_container=f"assets/art/models/cardspine/prefab/{prefab}",
    )


def _store(cache, scene):
    if any(item.severity == "error" for item in scene.validate()):
        cache.store_snapshot(scene)
    else:
        cache.store(scene, "config-hash", {"bundle-a": "b" * 64})


def test_scene_catalog_keeps_same_skin_separate_per_version(tmp_path):
    cache = PortraitSceneCache(tmp_path, parser_version="3")
    first = _scene(20261001, 12002183)
    second = _scene(20261012, 12002183)
    _store(cache, first)
    _store(cache, second)

    entries, diagnostics = load_portrait_scene_catalog(tmp_path, parser_version="3")

    assert diagnostics == ()
    assert [scene.version_timestamp for scene in entries] == ["20261001", "20261012"]
    assert len({portrait_scene_key(scene) for scene in entries}) == 2
    assert all(portrait_scene_status(scene) == "complete" for scene in entries)


def test_scene_catalog_keeps_incomplete_scene_and_exposes_diagnostic(tmp_path):
    cache = PortraitSceneCache(tmp_path, parser_version="3")
    scene = _scene(20261012, 12002183, diagnostics=(
        SceneDiagnostic("missing_asset", "Spine atlas is missing"),
    ))
    cache.store_snapshot(scene)

    entries, diagnostics = load_portrait_scene_catalog(tmp_path, parser_version="3")

    assert diagnostics == ()
    assert len(entries) == 1
    assert portrait_scene_status(entries[0]) == "incomplete"
    assert entries[0].diagnostics[0].message == "Spine atlas is missing"


def test_incomplete_snapshot_does_not_replace_complete_build_cache(tmp_path):
    cache = PortraitSceneCache(tmp_path, parser_version="3")
    complete = _scene(20261012, 12002183)
    cache.store(complete, "config-hash", {"bundle-a": "b" * 64})
    complete_path = tmp_path / "20261012" / "12002183.json"
    before = complete_path.read_bytes()
    incomplete = _scene(20261012, 12002183, diagnostics=(
        SceneDiagnostic("missing_asset", "new source is incomplete"),
    ))

    cache.store_snapshot(incomplete)

    assert complete_path.read_bytes() == before
    entries, diagnostics = load_portrait_scene_catalog(tmp_path, parser_version="3")
    assert diagnostics == ()
    assert len(entries) == 1
    assert portrait_scene_status(entries[0]) == "incomplete"

    cache.store(complete, "config-hash-2", {"bundle-a": "c" * 64})
    assert not (tmp_path / "_incomplete" / "20261012" / "12002183.json").exists()


def test_scene_catalog_skips_corrupt_and_old_schema_entries_without_losing_valid_ones(tmp_path):
    cache = PortraitSceneCache(tmp_path, parser_version="3")
    _store(cache, _scene(20261012, 12002183))
    corrupt = tmp_path / "20261012" / "12002184.json"
    corrupt.write_text("{not-json", encoding="utf-8")

    entries, diagnostics = load_portrait_scene_catalog(tmp_path, parser_version="3")

    assert len(entries) == 1
    assert len(diagnostics) == 1
    assert "12002184.json" in diagnostics[0][0]

    old_schema = tmp_path / "20261012" / "12002186.json"
    envelope = json.loads((tmp_path / "20261012" / "12002183.json").read_text(encoding="utf-8"))
    envelope["schema_version"] = 2
    old_schema.write_text(json.dumps(envelope), encoding="utf-8")
    entries, diagnostics = load_portrait_scene_catalog(tmp_path, parser_version="3")
    assert len(entries) == 1
    assert len(diagnostics) == 2

    old_parser_cache = PortraitSceneCache(tmp_path, parser_version="2")
    _store(old_parser_cache, _scene(20261012, 12002185))
    entries, diagnostics = load_portrait_scene_catalog(tmp_path, parser_version="3")
    assert len(entries) == 1
    assert len(diagnostics) == 3


def test_scene_unread_identity_includes_version_and_skin_id():
    first = _scene(20261001, 12002183)
    second = _scene(20261012, 12002183)

    assert portrait_scene_key(first) != portrait_scene_key(second)
