import json

from app.features.preview.portrait_config.asset_index import AssetIndex


def test_asset_index_is_version_bound_and_keeps_missing_bundle_explicit(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "version_timestamp": 402,
        "assets": [
            {"name": "shared.prefab", "container": "Assets/A", "bundle_hash": "a1",
             "path_id": 11, "dependencies": ["d1"]},
            {"name": "shared.prefab", "container": "Assets/B", "bundle_hash": "b2",
             "path_id": 22, "dependencies": []},
            {"name": "orphan.prefab", "container": "Assets/C", "bundle_hash": "gone",
             "path_id": 33, "dependencies": []},
        ],
    }), encoding="utf-8")
    bundle_root = tmp_path / "bundles"
    bundle_root.mkdir()
    (bundle_root / "a1").write_bytes(b"a")
    (bundle_root / "b2").write_bytes(b"b")
    index = AssetIndex.from_catalog(catalog, bundle_root, 402)
    assert index.resolve("Assets/A", "shared.prefab").path_id == 11
    assert index.resolve("Assets/B", "shared.prefab").path_id == 22
    missing = index.resolve("Assets/C", "orphan.prefab")
    assert missing.bundle_present is False
    assert missing.bundle_hash == "gone"
    assert missing.version_timestamp == 402
    assert missing.evidence.endswith("#/assets/2")
