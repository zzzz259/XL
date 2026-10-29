import json
from pathlib import Path

import pytest

from app.features.preview.portrait_config.asset_index import AssetIndex, IndexedAsset


def _asset(name, container, bundle_hash="AbC", path_id=11, version=402,
           bundle_path=Path("bundles/abc")):
    return IndexedAsset(
        name=name, container=container, bundle_hash=bundle_hash, path_id=path_id,
        dependencies=(), version_timestamp=version, bundle_path=bundle_path,
        bundle_present=True, evidence="test",
    )


class _CountingAssets(tuple):
    """Count catalog visits without relying on machine-dependent timings."""

    def __iter__(self):
        for asset in super().__iter__():
            self.visits += 1
            yield asset


@pytest.mark.parametrize("method,args,missing_args,missing_result", [
    ("find_exact", ("ABC", "11"), ("missing", 11), ()),
    ("has_bundle", ("ABC",), ("missing",), False),
    ("find_by_name_exact", ("Hero.prefab",), ("missing",), ()),
    ("find_by_container_exact", ("HERO.PREFAB",), ("missing.prefab",), ()),
    ("find_by_container_exact", (r"/ASSETS\HERO.PREFAB/",), ("Assets/missing",), ()),
    ("bundle_path_for", ("ABC",), ("missing",), None),
    ("resolve", ("ASSETS/HERO.PREFAB", "hero.prefab"), ("Assets/missing", "missing"), None),
    ("find_external", ("CAB-" + "a" * 32, 11), ("CAB-" + "b" * 32, 11), ()),
])
def test_repeated_asset_lookups_do_not_rescan_catalog(method, args, missing_args, missing_result, monkeypatch):
    from types import SimpleNamespace
    from app.features.preview.portrait_config import unity_scene

    monkeypatch.setattr(unity_scene, "_load_environment", lambda path: SimpleNamespace(objects=[
        SimpleNamespace(path_id=11, type=SimpleNamespace(name="Texture2D"),
                        assets_file=SimpleNamespace(name=f"CAB-{path.name}")),
    ]))
    assets = _CountingAssets([
        _asset("Hero.prefab", "Assets/Hero.prefab"),
        _asset("external", "Assets/external", bundle_hash="a" * 32, bundle_path=Path("a" * 32)),
        *(_asset(f"other-{number}", f"Assets/other-{number}") for number in range(100)),
    ])
    assets.visits = 0
    index = AssetIndex(402, assets)
    lookup = getattr(index, method)
    first = lookup(*args)
    assert first is not None and first != ()
    visits_after_first_lookup = assets.visits

    for _ in range(5):
        assert lookup(*args) == first
        assert lookup(*missing_args) == missing_result

    assert assets.visits == visits_after_first_lookup
    assert assets.visits <= len(assets)


def test_lookup_tables_preserve_order_duplicates_case_and_version_boundaries():
    old = _asset("Hero.prefab", "Assets/Hero.prefab", version=401)
    first = _asset("Hero.prefab", "Assets/Hero.prefab")
    other_bundle = _asset("hero.prefab", "Other/HERO.PREFAB", bundle_hash="DEF")
    last = _asset("Hero.prefab", "Assets/Hero.prefab", path_id=12)
    index = AssetIndex(402, (old, first, other_bundle, first, last))

    assert index.find_exact("aBc", "11") == (first, first)
    assert index.find_by_name_exact("Hero.prefab") == (first, first, last)
    assert index.find_by_name_exact("hero.prefab") == (other_bundle,)
    assert index.find_by_name_exact("HERO.PREFAB") == ()
    assert index.find_by_container_exact("hero.PREFAB") == (first, other_bundle, first, last)
    assert index.find_by_container_exact(r"/ASSETS\HERO.PREFAB/") == (first, first, last)
    assert index.has_bundle("abc") is True
    assert index.bundle_path_for("ABC") == Path("bundles/abc")
    assert index.find_exact("missing", 11) == ()
    assert index.has_bundle("missing") is False
    assert index.bundle_path_for("missing") is None

    previous = AssetIndex(401, index.assets)
    assert previous.find_exact("ABC", 11) == (old,)
    assert previous.find_by_name_exact("Hero.prefab") == (old,)
    assert previous.find_by_container_exact("hero.prefab") == (old,)
    assert previous.has_bundle("DEF") is False
    assert previous.bundle_path_for("DEF") is None
    assert index.find_exact("ABC", 11) == (first, first)


def test_indexed_bundle_paths_and_resolve_keep_ambiguity_explicit():
    first = _asset("Folder/Hero.prefab", "Assets/Hero.prefab")
    second = _asset(r"Other\HERO.PREFAB", "Assets/Hero.prefab",
                    bundle_path=Path("other/abc"))
    index = AssetIndex(402, (first, second))

    with pytest.raises(ValueError, match="ambiguous bundle path"):
        index.bundle_path_for("ABC")
    with pytest.raises(ValueError, match="ambiguous catalog asset"):
        index.resolve(r"/ASSETS\HERO.PREFAB/", "hero.prefab")
    assert index.resolve("Assets/missing", "hero.prefab") is None
    assert AssetIndex(402, (first,)).resolve("ASSETS/HERO.PREFAB", r"other\HERO.PREFAB") == first


def test_resolve_preserves_legacy_matching_of_complete_catalog():
    old = _asset("Hero.prefab", "Assets/Hero.prefab", version=401)
    index = AssetIndex(402, (old,))

    assert index.resolve("Assets/Hero.prefab", "hero.prefab") == old
    assert index.find_by_container_exact("Hero.prefab") == ()


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
def test_catalog_checks_each_physical_bundle_once(tmp_path, monkeypatch):
    import json
    from app.features.preview.portrait_config.asset_index import AssetIndex
    bundle = tmp_path / 'source.bundle'
    bundle.write_bytes(b'bundle')
    catalog = tmp_path / 'map.json'
    catalog.write_text(json.dumps([{'bundle_hash': 'source', 'PathID': i, 'Name': str(i)} for i in range(100)]))
    original = Path.is_file
    checks = []
    def is_file(path):
        checks.append(path)
        return original(path)
    monkeypatch.setattr(Path, 'is_file', is_file)
    index = AssetIndex.from_catalog(catalog, tmp_path, 123)
    assert all(a.bundle_present for a in index.assets)
    assert len(checks) < 10
