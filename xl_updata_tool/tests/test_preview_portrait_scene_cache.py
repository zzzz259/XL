import hashlib
import json
import os

import pytest

from app.features.preview.portrait_config.asset_index import AssetIndex, IndexedAsset
from app.features.preview.portrait_config.cache import PortraitSceneCache
from app.features.preview.portrait_config.model import AssetRef, PortraitScene, SceneDiagnostic

VERSION = 402
HASH_A = "a" * 32
HASH_B = "b" * 32


def _indexed(bundle_root, bundle_hash, content):
    path = bundle_root / bundle_hash
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return IndexedAsset(
        f"asset-{bundle_hash}", "Assets", bundle_hash, 1, (), VERSION,
        path, True, f"catalog#/{bundle_hash}/1",
    )


def _scene(bundle_hash, skin_id):
    ref = AssetRef(bundle_hash, 1)
    return PortraitScene(
        "v402", str(VERSION), skin_id, "10118", skin_id, ref,
        (bundle_hash,), (), (ref,),
    )


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cache_hit_requires_same_config_and_only_its_source_bundle_hashes(tmp_path):
    bundle_root = tmp_path / "bundles"
    asset_a = _indexed(bundle_root, HASH_A, b"bundle-a")
    asset_b = _indexed(bundle_root, HASH_B, b"bundle-b")
    index = AssetIndex(VERSION, (asset_a, asset_b))
    cache = PortraitSceneCache(tmp_path / "data" / "cache" / "portrait-scenes", "p1")
    scene_a, scene_b = _scene(HASH_A, "1201"), _scene(HASH_B, "1202")
    config = "lua-config-digest"
    cache.store(scene_a, config, {HASH_A: _digest(asset_a.bundle_path)})
    cache.store(scene_b, config, {HASH_B: _digest(asset_b.bundle_path)})

    asset_a.bundle_path.write_bytes(b"changed-a")

    assert cache.load(cache.key_for(VERSION, "1201"), config, index) is None
    assert cache.load(cache.key_for(VERSION, "1202"), config, index) == scene_b
    assert cache.load(cache.key_for(VERSION, "1202"), "changed-config", index) is None


def test_schema_and_parser_version_changes_invalidate_cached_scene(tmp_path):
    bundle_root = tmp_path / "bundles"
    asset = _indexed(bundle_root, HASH_A, b"bundle")
    index = AssetIndex(VERSION, (asset,))
    cache = PortraitSceneCache(tmp_path / "cache", "p1")
    scene = _scene(HASH_A, "1201")
    cache.store(scene, "config", {HASH_A: _digest(asset.bundle_path)})
    key = cache.key_for(VERSION, "1201")
    path = tmp_path / "cache" / key
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] -= 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.load(key, "config", index) is None

    cache.store(scene, "config", {HASH_A: _digest(asset.bundle_path)})
    assert PortraitSceneCache(tmp_path / "cache", "p2").load(key, "config", index) is None


def test_failed_atomic_replace_preserves_previous_record_and_cleans_temp(tmp_path, monkeypatch):
    bundle_root = tmp_path / "bundles"
    asset = _indexed(bundle_root, HASH_A, b"bundle")
    cache = PortraitSceneCache(tmp_path / "cache", "p1")
    scene = _scene(HASH_A, "1201")
    source_hashes = {HASH_A: _digest(asset.bundle_path)}
    cache.store(scene, "config", source_hashes)
    path = tmp_path / "cache" / cache.key_for(VERSION, scene.skin_id)
    original = path.read_bytes()

    def interrupted(*_args):
        raise OSError("simulated atomic-replace interruption")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError, match="interruption"):
        cache.store(scene, "different-config", source_hashes)

    assert path.read_bytes() == original
    assert list(path.parent.glob("*.tmp")) == []


def test_invalid_scene_cannot_overwrite_a_previous_valid_record(tmp_path):
    from dataclasses import replace

    bundle_root = tmp_path / "bundles"
    asset = _indexed(bundle_root, HASH_A, b"bundle")
    cache = PortraitSceneCache(tmp_path / "cache", "p1")
    scene = _scene(HASH_A, "1201")
    source_hashes = {HASH_A: _digest(asset.bundle_path)}
    cache.store(scene, "config", source_hashes)
    path = tmp_path / "cache" / cache.key_for(VERSION, scene.skin_id)
    original = path.read_bytes()

    with pytest.raises(ValueError, match="complete"):
        cache.store(replace(scene, prefab=None), "new-config", source_hashes)

    assert path.read_bytes() == original


def test_cache_rejects_path_traversal(tmp_path):
    cache = PortraitSceneCache(tmp_path / "cache", "p1")
    with pytest.raises(ValueError, match="cache key"):
        cache.load("../outside.json", "config", AssetIndex(VERSION, ()))
    with pytest.raises(ValueError, match="cache key"):
        cache.load("402", "config", AssetIndex(VERSION, ()))


def test_cache_round_trips_eventcover_scene_ids(tmp_path):
    bundle_root = tmp_path / "bundles"
    asset = _indexed(bundle_root, HASH_A, b"bundle")
    index = AssetIndex(VERSION, (asset,))
    cache = PortraitSceneCache(tmp_path / "cache", "p1")
    scene = _scene(HASH_A, "eventcovers_0005")
    key = cache.key_for(VERSION, scene.skin_id)

    cache.store(scene, "config", {HASH_A: _digest(asset.bundle_path)})

    assert key == "402/eventcovers_0005.json"
    assert cache.load(key, "config", index) == scene
    assert cache.load_manifest(key) == scene


def test_incomplete_eventcover_snapshot_is_discoverable(tmp_path):
    cache = PortraitSceneCache(tmp_path / "cache", "p1")
    scene = PortraitScene(
        "v402", str(VERSION), "70440005", "", "eventcovers_0005", None, (), (),
        diagnostics=(SceneDiagnostic("prefab_asset_missing", "root missing"),),
    )

    key = cache.store_snapshot(scene)

    assert key == "402/eventcovers_0005.json"
    incomplete_cache = PortraitSceneCache(tmp_path / "cache" / "_incomplete", "p1")
    assert incomplete_cache.load_manifest(key) == scene


@pytest.mark.parametrize("payload", [[], None, {"scene": []}, {"scene": None}])
def test_malformed_json_cache_envelopes_are_cache_misses(tmp_path, payload):
    bundle_root = tmp_path / "bundles"
    asset = _indexed(bundle_root, HASH_A, b"bundle")
    index = AssetIndex(VERSION, (asset,))
    cache = PortraitSceneCache(tmp_path / "cache", "p1")
    key = cache.key_for(VERSION, "1201")
    path = tmp_path / "cache" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.load(key, "config", index) is None
