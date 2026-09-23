import pytest

from app.features.preview.material_catalog import GameMaterialCatalog, MaterialExportSummary
from app.features.preview.output_publisher import RawSpinePublishSummary
from app.features.preview.service import PreviewPreprocessSummary, PreviewService


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
    assert progress[-1] == (4, 4, "图片资源预处理完成")


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
