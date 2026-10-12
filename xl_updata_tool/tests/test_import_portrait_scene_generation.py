from app.features.preview.portrait_config.import_service import generate_portrait_manifests


class _EmptyIndex:
    assets = ()


def test_generates_scenes_only_when_inputs_ready_and_reports_progress(tmp_path, monkeypatch):
    calls = []
    roots = [type("Root", (), {"skin_id": "1"})(), type("Root", (), {"skin_id": "2"})()]
    scenes = [type("Scene", (), {
        "diagnostics": (), "skin_id": "1", "character_id": "10118",
        "parts": (), "prefab_container": "container/1.prefab",
    })(), type("Scene", (), {
        "diagnostics": (type("D", (), {
            "severity": "error", "code": "incomplete", "message": "missing",
            "source": None,
        })(),),
        "skin_id": "2", "character_id": "10118", "parts": (),
        "prefab_container": "container/2.prefab",
    })()]
    monkeypatch.setattr("app.features.preview.portrait_config.import_service.load_portrait_roots", lambda *_: roots)
    monkeypatch.setattr("app.features.preview.portrait_config.import_service.AssetIndex.from_catalog", lambda *_: _EmptyIndex())
    def fake_build(_version, selected_roots, _index, **_kwargs):
        calls.append(selected_roots)
        return [scenes[int(selected_roots[0].skin_id) - 1]]
    monkeypatch.setattr("app.features.preview.portrait_config.import_service.build_portrait_scenes", fake_build)
    data_root = tmp_path / "data"
    bundle = data_root / "bundles" / "123"
    bundle.mkdir(parents=True)
    catalog = bundle / "_map" / "assets_map.json"
    catalog.parent.mkdir()
    catalog.write_text('{"version_timestamp":123}')
    lua = tmp_path / "lua"
    lua.mkdir()
    progress = []
    result = generate_portrait_manifests(data_root, 123, catalog, lua, bundle,
                                         progress_callback=lambda *a: progress.append(a))
    assert calls and result.status == "partial"
    assert result.complete == 1 and result.incomplete == 1
    assert progress[-1] == ("2", 2, 2)


def test_missing_required_inputs_are_nonfatal_structured_status(tmp_path):
    result = generate_portrait_manifests(
        tmp_path, 123, tmp_path / "missing.json", tmp_path / "lua",
        tmp_path / "bundles" / "123",
    )
    assert result.status == "skipped"
    assert result.diagnostic == "portrait_inputs_missing"


def test_accepts_assetstudio_raw_array_catalog_without_embedded_version(tmp_path, monkeypatch):
    roots = [type("Root", (), {"skin_id": "1"})()]
    scene = type("Scene", (), {
        "diagnostics": (), "skin_id": "1", "character_id": "10118",
        "parts": (), "prefab_container": "assets/cardspine/hero.prefab",
    })()
    monkeypatch.setattr("app.features.preview.portrait_config.import_service.load_portrait_roots", lambda *_: roots)
    monkeypatch.setattr("app.features.preview.portrait_config.import_service.AssetIndex.from_catalog", lambda *_: _EmptyIndex())
    monkeypatch.setattr("app.features.preview.portrait_config.import_service.build_portrait_scenes",
                        lambda *_args, **_kwargs: [scene])
    data_root = tmp_path / "data"
    bundle = data_root / "bundles" / "123"
    bundle.mkdir(parents=True)
    catalog = bundle / "_map" / "assets_map.json"
    catalog.parent.mkdir()
    (bundle / ("a" * 32 + ".bundle")).write_bytes(b"bundle")
    catalog.write_text('[{"Name":"hero.skel","Container":"assets/cardspine/hero.prefab",'
                       '"Source":"C:/input/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.bundle",'
                       '"PathID":1,"Type":"TextAsset"}]', encoding="utf-8")
    lua = tmp_path / "lua"
    lua.mkdir()

    result = generate_portrait_manifests(data_root, 123, catalog, lua, bundle)

    assert result.status == "complete"
    assert result.complete == 1


def test_raw_catalog_must_be_under_the_selected_version_bundle_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.features.preview.portrait_config.import_service.load_portrait_roots",
        lambda *_: (_ for _ in ()).throw(AssertionError("wrong version path must not load Lua")),
    )
    data_root = tmp_path / "data"
    bundle = data_root / "bundles" / "999"
    bundle.mkdir(parents=True)
    catalog = bundle / "_map" / "assets_map.json"
    catalog.parent.mkdir()
    catalog.write_text("[]", encoding="utf-8")
    lua = tmp_path / "lua"
    lua.mkdir()

    result = generate_portrait_manifests(data_root, 123, catalog, lua, bundle)

    assert result.status == "skipped"
    assert result.diagnostic == "portrait_version_path_mismatch"


def test_cancellation_after_one_skin_reports_partial_work_and_stops_next_skin(tmp_path, monkeypatch):
    roots = [type("Root", (), {"skin_id": str(number)})() for number in (1, 2)]
    calls = []
    cancelled = [False]
    scene = type("Scene", (), {
        "diagnostics": (), "skin_id": "1", "character_id": "10118",
        "parts": (), "prefab_container": "container/1.prefab",
    })()
    monkeypatch.setattr(
        "app.features.preview.portrait_config.import_service.load_portrait_roots",
        lambda *_: roots,
    )
    monkeypatch.setattr(
        "app.features.preview.portrait_config.import_service.AssetIndex.from_catalog",
        lambda *_: _EmptyIndex(),
    )

    def build(_version, selected_roots, _index, **_kwargs):
        calls.append(selected_roots[0].skin_id)
        return [scene]

    monkeypatch.setattr(
        "app.features.preview.portrait_config.import_service.build_portrait_scenes", build,
    )
    data_root = tmp_path / "data"
    bundle = data_root / "bundles" / "123"
    bundle.mkdir(parents=True)
    catalog = bundle / "_map" / "assets_map.json"
    catalog.parent.mkdir()
    catalog.write_text("[]", encoding="utf-8")
    lua = tmp_path / "lua"
    lua.mkdir()

    result = generate_portrait_manifests(
        data_root, 123, catalog, lua, bundle,
        cancel_check=lambda: cancelled[0],
        progress_callback=lambda _skin, current, _total: cancelled.__setitem__(0, current == 1),
    )

    assert result.status == "cancelled"
    assert result.roots == 1 and result.complete == 1
    assert calls == ["1"]


def test_cancellation_stops_before_loading_or_building(tmp_path, monkeypatch):
    monkeypatch.setattr("app.features.preview.portrait_config.import_service.load_portrait_roots",
                        lambda *_: (_ for _ in ()).throw(AssertionError("must not parse")))
    lua = tmp_path / "lua"
    lua.mkdir()
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"version_timestamp":123}')
    result = generate_portrait_manifests(tmp_path, 123, catalog, lua,
                                         tmp_path / "bundles" / "123",
                                         cancel_check=lambda: True)
    assert result.status == "cancelled"
