from types import SimpleNamespace

from server_app.catalog import read_catalog


class _FakePointer:
    def __init__(self, value):
        self.asset = SimpleNamespace(read=lambda: value)


def test_read_catalog_converts_unitypy_manifest_objects(monkeypatch, tmp_path):
    bundle = SimpleNamespace(
        m_Container=[("assets/arts.asset", _FakePointer(SimpleNamespace(
            bundles=[SimpleNamespace(name="card.bundle", hash="abc", size=12, deps=[0])],
            assets=[SimpleNamespace(name="cardspine_10001.skel.bytes", bundle=0, dir=1)],
            dirs=["Assets/Art/Models/CardSpine", "Assets/Lua"],
        )))],
    )
    fake_environment = SimpleNamespace(
        objects=[SimpleNamespace(type=SimpleNamespace(name="AssetBundle"), read=lambda: bundle)]
    )
    monkeypatch.setattr("server_app.catalog.UnityPy.load", lambda _path: fake_environment)
    result = read_catalog(tmp_path / "catalog.bundle", b"0123456789abcdef")
    assert result.bundles[0].hash == "abc"
    assert result.assets[0].container == "Assets/Lua"
