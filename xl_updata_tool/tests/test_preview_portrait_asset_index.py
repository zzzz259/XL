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


def test_asset_index_reads_real_assetstudio_array_and_bundle_extension(tmp_path):
    import json

    catalog = tmp_path / "assets_map.json"
    bundle_root = tmp_path / "bundles"
    bundle_root.mkdir()
    bundle = bundle_root / ("a" * 32 + ".bundle")
    bundle.write_bytes(b"real-bundle")
    catalog.write_text(json.dumps([
        {"Name": "hero.skel", "Container": "assets/art/cardspine/hero.prefab",
         "Source": r"C:\\AssetStudio\\input\\" + "a" * 32 + ".bundle",
         "PathID": -21, "Type": "TextAsset"},
        {"Name": "unrelated", "Container": "assets/art/cardspine/other.prefab",
         "Source": r"C:\\AssetStudio\\input\\" + "a" * 32 + ".bundle",
         "PathID": 22, "Type": "Texture2D"},
    ]), encoding="utf-8")

    index = AssetIndex.from_catalog(catalog, bundle_root, 402)

    assert index.version_timestamp == 402
    assert index.find_exact("a" * 32, -21)[0].asset_type == "TextAsset"
    assert index.find_by_container_exact("hero.prefab") == (index.assets[0],)
    assert index.bundle_path_for("a" * 32) == bundle
    assert index.assets[0].bundle_present is True
