import pytest

from app.features.preview.material_catalog import GameMaterialCatalog, MaterialExportSummary
from app.features.preview.output_publisher import RawSpinePublishSummary
from app.features.preview.portrait_config.import_service import PortraitImportSummary
from app.features.preview.service import PreviewPreprocessSummary, PreviewService
from app.shared.contracts import ImportResult


def test_preprocess_builds_spine_and_game_material_outputs_after_import(tmp_path):
    service = PreviewService(tmp_path / "data" / "material", tmp_path / "output" / "character")
    events = []
    catalog = object()
    material_catalog = GameMaterialCatalog((), (), ())
    spine_summary = RawSpinePublishSummary(published=2, copied_files=6)
    material_summary = MaterialExportSummary(exported=4)

    service.discover_preview_resources = lambda: events.append("discover-spine") or catalog
    service.publish_raw_spine_resources = lambda value: events.append(("publish-spine", value)) or spine_summary
    service.discover_game_materials = lambda: events.append("discover-materials") or material_catalog
    service.export_game_materials = lambda value, progress_callback=None: events.append(("export-materials", value)) or material_summary

    progress = []
    result = service.preprocess_preview_resources(
        progress_callback=lambda current, total, message: progress.append((current, total, message))
    )

    assert isinstance(result, PreviewPreprocessSummary)
    assert result.catalog is catalog
    assert result.spine == spine_summary
    assert result.materials == material_summary
    assert events == [
        "discover-spine",
        ("publish-spine", catalog),
        "discover-materials",
        ("export-materials", material_catalog),
    ]
    assert progress[-1] == (5, 5, "图片资源预处理完成")


def test_preview_postprocess_generates_portrait_scenes_for_successful_lua_and_character_import(
    tmp_path, monkeypatch,
):
    data_root = tmp_path / "data"
    material = data_root / "material"
    output_lua = tmp_path / "output" / "lua" / "402"
    output_lua.mkdir(parents=True)
    bundle_root = data_root / "bundles" / "402"
    (bundle_root / "_map").mkdir(parents=True)
    catalog = bundle_root / "_map" / "assets_map.json"
    catalog.write_text("[]", encoding="utf-8")
    calls = []

    def generate(*args, **kwargs):
        calls.append((args, kwargs))
        return PortraitImportSummary("partial", roots=7, complete=6, incomplete=1, cache_hits=3)

    monkeypatch.setattr(
        "app.features.preview.service.generate_portrait_manifests",
        generate,
    )
    service = PreviewService(material, tmp_path / "output" / "character")
    service.discover_preview_resources = lambda: object()
    service.publish_raw_spine_resources = lambda _catalog: RawSpinePublishSummary()
    service.discover_game_materials = lambda: GameMaterialCatalog((), (), ())
    service.export_game_materials = lambda *_args, **_kwargs: MaterialExportSummary()
    result = ImportResult(
        categories=frozenset({"lua", "character"}),
        completed_categories=frozenset({"lua", "character"}),
        lua_export_result={"version": 402, "directory": str(output_lua)},
    )

    summary = service.preprocess_preview_resources(import_result=result)

    assert summary.portrait.complete == 6
    assert summary.portrait.incomplete == 1
    assert summary.portrait.cache_hits == 3
    assert calls[0][0] == (data_root, 402, catalog, output_lua.parent, bundle_root)


def test_preview_postprocess_skips_portrait_generation_without_both_completed_categories(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "app.features.preview.service.generate_portrait_manifests",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not generate")),
    )
    service = PreviewService(tmp_path / "data" / "material", tmp_path / "output" / "character")
    service.discover_preview_resources = lambda: object()
    service.publish_raw_spine_resources = lambda _catalog: RawSpinePublishSummary()
    service.discover_game_materials = lambda: GameMaterialCatalog((), (), ())
    service.export_game_materials = lambda *_args, **_kwargs: MaterialExportSummary()

    summary = service.preprocess_preview_resources(
        import_result=ImportResult(
            categories=frozenset({"lua", "character"}),
            completed_categories=frozenset({"lua"}),
        ),
    )

    assert summary.portrait is None


def test_preprocess_keeps_material_failures_in_partial_import_summary():
    service = PreviewService("data/material", "output/character")
    service.discover_preview_resources = lambda: object()
    service.publish_raw_spine_resources = lambda _catalog: RawSpinePublishSummary(published=1)
    service.discover_game_materials = lambda: GameMaterialCatalog((), (), ())
    service.export_game_materials = lambda *_args, **_kwargs: MaterialExportSummary(
        exported=0,
        failed=1,
        diagnostics=("burst-head export failed: access denied",),
    )

    summary = service.preprocess_preview_resources()
    assert summary.partial
    assert summary.materials.failed == 1
    assert 'access denied' in summary.materials.diagnostics[0]


@pytest.mark.parametrize("completed", [frozenset({"all"}), frozenset({"character"})])
def test_config_scenes_use_selected_version_for_full_or_separate_character_import(
    tmp_path, monkeypatch, completed,
):
    calls = []
    lua_version = tmp_path / "output" / "lua" / "402"
    lua_version.mkdir(parents=True)
    # A newer Lua directory must never silently replace the selected version.
    (lua_version.parent / "999").mkdir()
    monkeypatch.setattr(
        "app.features.preview.service.generate_portrait_manifests",
        lambda *args, **kwargs: calls.append(args) or PortraitImportSummary("complete", roots=1, complete=1),
    )
    service = PreviewService(tmp_path / "data" / "material", tmp_path / "output" / "character")
    service.discover_preview_resources = lambda: object()
    service.publish_raw_spine_resources = lambda _: RawSpinePublishSummary()
    service.discover_game_materials = lambda: GameMaterialCatalog((), (), ())
    service.export_game_materials = lambda *args, **kwargs: MaterialExportSummary()
    summary = service.preprocess_preview_resources(import_result=ImportResult(
        categories=completed, completed_categories=completed, version_timestamp=402,
    ))
    assert summary.portrait.complete == 1
    assert calls[0][1] == 402
    assert calls[0][3] == lua_version.parent


def test_full_legacy_import_generates_scenes_from_verified_lua_result(tmp_path, monkeypatch):
    calls = []
    lua_version = tmp_path / "output" / "lua" / "402"
    lua_version.mkdir(parents=True)
    monkeypatch.setattr(
        "app.features.preview.service.generate_portrait_manifests",
        lambda *args, **kwargs: calls.append(args) or PortraitImportSummary("complete", roots=1, complete=1),
    )
    service = PreviewService(tmp_path / "data" / "material", tmp_path / "output" / "character")
    service.discover_preview_resources = lambda: object()
    service.publish_raw_spine_resources = lambda _: RawSpinePublishSummary()
    service.discover_game_materials = lambda: GameMaterialCatalog((), (), ())
    service.export_game_materials = lambda *args, **kwargs: MaterialExportSummary()
    summary = service.preprocess_preview_resources(import_result=ImportResult(
        categories=frozenset({"all"}), completed_categories=frozenset({"all"}),
        lua_export_result={"version": 402, "directory": str(lua_version)},
    ))
    assert calls and summary.portrait.complete == 1


def test_worker_records_partial_scene_outcome_instead_of_success(monkeypatch):
    from app.features.preview.workers.preview_postprocess import PreviewPostprocessWorker
    outcomes = []
    monkeypatch.setattr(
        "app.features.preview.workers.preview_postprocess.set_task_outcome",
        lambda outcome, **kwargs: outcomes.append(outcome),
    )
    summary = PreviewPreprocessSummary(
        object(), RawSpinePublishSummary(), MaterialExportSummary(),
        PortraitImportSummary("partial", roots=2, complete=1, incomplete=1),
    )
    service = type("Service", (), {"preprocess_preview_resources": lambda self, **kwargs: summary})()
    worker = PreviewPostprocessWorker(service)
    worker.run()
    assert outcomes == ["partial"]


def test_character_import_without_version_reports_skipped_config_phase(tmp_path):
    service = PreviewService(tmp_path / "data" / "material", tmp_path / "output" / "character")
    service.discover_preview_resources = lambda: object()
    service.publish_raw_spine_resources = lambda _: RawSpinePublishSummary()
    service.discover_game_materials = lambda: GameMaterialCatalog((), (), ())
    service.export_game_materials = lambda *args, **kwargs: MaterialExportSummary()
    summary = service.preprocess_preview_resources(import_result=ImportResult(
        categories=frozenset({"character"}), completed_categories=frozenset({"character"}),
    ))
    assert summary.portrait.status == "skipped"
    assert summary.portrait.diagnostic == "portrait_version_context_missing"
