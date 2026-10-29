from app.features.preview.portrait_config.asset_index import AssetIndex
from app.features.preview.portrait_config.model import AssetRef
from app.features.preview.portrait_config import unity_scene
from types import SimpleNamespace
from test_preview_portrait_unity_scene import FakeObject


def test_catalog_omitted_shader_requires_verified_cab_and_path(monkeypatch, tmp_path):
    cab = "a" * 32
    path = tmp_path / "physical.bundle"
    path.write_bytes(b"fake")
    obj = FakeObject(17, "Shader", {"m_ParsedForm": {"m_Name": "exact shader"}})
    obj.assets_file = SimpleNamespace(name="CAB-" + cab)
    monkeypatch.setattr(unity_scene, "_load_environment", lambda p: SimpleNamespace(objects=[obj]))
    monkeypatch.setattr("app.features.preview.portrait_config.cab_index.scan_headers", lambda root: {cab: (path,)})
    index = AssetIndex(1, (), bundle_root=tmp_path)
    matches = index.find_external("CAB-" + cab, 17)
    assert len(matches) == 1 and matches[0].asset_type == "Shader"
    assert index.object_tree(AssetRef(matches[0].bundle_hash, 17))["m_ParsedForm"]["m_Name"] == "exact shader"
    assert not index.find_external("CAB-" + ("b" * 32), 17)


def test_header_cache_is_version_and_directory_fingerprint_bound(tmp_path, monkeypatch):
    from app.features.preview.portrait_config import cab_index

    root = tmp_path / "bundles"
    root.mkdir()
    bundle = root / "physical.bundle"
    bundle.write_bytes(b"one")
    cache = tmp_path / "cache" / "headers.json"
    calls = []

    def scan(directory):
        calls.append(directory)
        return {"a" * 32: (bundle,)}

    monkeypatch.setattr(cab_index, "scan_headers", scan)
    assert cab_index.load_headers(root, 1, cache)["a" * 32] == (bundle,)
    assert cab_index.load_headers(root, 1, cache)["a" * 32] == (bundle,)
    assert len(calls) == 1
    cab_index.load_headers(root, 2, cache)
    assert len(calls) == 2
    bundle.write_bytes(b"changed")
    cab_index.load_headers(root, 2, cache)
    assert len(calls) == 3
