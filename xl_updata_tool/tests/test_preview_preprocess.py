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


def test_preprocess_fails_import_when_game_material_export_reports_failures():
    service = PreviewService("data/material", "output/character")
    service.discover_preview_resources = lambda: object()
    service.publish_raw_spine_resources = lambda _catalog: RawSpinePublishSummary(published=1)
    service.discover_game_materials = lambda: GameMaterialCatalog((), (), ())
    service.export_game_materials = lambda *_args, **_kwargs: MaterialExportSummary(
        exported=0,
        failed=1,
        diagnostics=("burst-head export failed: access denied",),
    )

    with pytest.raises(RuntimeError, match="游戏素材导出失败.*access denied"):
        service.preprocess_preview_resources()
