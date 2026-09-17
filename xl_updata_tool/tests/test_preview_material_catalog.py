from app.features.preview.fgui_atlas import UIPackageTool
from app.features.preview.material_catalog import (
    AtlasResourceGroup,
    GameMaterialCatalog,
    GameMaterialRecord,
    discover_game_materials,
    export_game_materials,
    is_burst_head_resource,
)
from app.features.preview.service import PreviewService


def test_burst_head_token_variants_are_recognized_but_dialoghead_is_not(tmp_path):
    assert is_burst_head_resource(tmp_path / "burst-head" / "10080.png", {})
    assert is_burst_head_resource(tmp_path / "burst_head" / "10081.png", {})
    assert is_burst_head_resource(tmp_path / "bursthead" / "10082.png", {})
    assert not is_burst_head_resource(tmp_path / "dialoghead" / "10080.png", {})
    assert not is_burst_head_resource(tmp_path / "burst-head-extra" / "10080.png", {})


def test_metadata_only_classifies_an_explicitly_matching_resource(tmp_path):
    ordinary = tmp_path / "ordinary.png"
    explicit = tmp_path / "resource.png"
    ordinary.write_bytes(b"ordinary")
    explicit.write_bytes(b"burst head")
    metadata = {
        "type": "burst-head",
        "resources": {str(explicit): {"type": "burst-head"}},
    }

    assert not is_burst_head_resource(ordinary, metadata)
    assert is_burst_head_resource(explicit, metadata)

    catalog = discover_game_materials(tmp_path, metadata)
    assert [record.source_path for record in catalog.burst_heads] == [str(explicit)]
    assert [record.source_path for record in catalog.unmatched] == [str(ordinary)]


def test_material_discovery_groups_each_fui_package(tmp_path):
    ui = tmp_path / "assets" / "fairygui" / "ui"
    ui.mkdir(parents=True)
    (ui / "Card_fui.bank").write_bytes(b"bank")
    (ui / "Card_fui.bytes").write_bytes(b"bytes")
    (ui / "Battle_fui.bytes").write_bytes(b"bytes")

    catalog = discover_game_materials(tmp_path)

    assert [group.package_name for group in catalog.atlases] == ["Battle", "Card"]
    assert catalog.atlases[1].source_path == str(ui / "Card_fui.bytes")


def test_game_material_export_uses_stable_directories_and_reports_failures(tmp_path):
    burst_path = tmp_path / "burst_head" / "10080.png"
    burst_path.parent.mkdir(parents=True)
    burst_path.write_bytes(b"head")
    atlas_path = tmp_path / "assets" / "fairygui" / "ui" / "Card_fui.bytes"
    atlas_path.parent.mkdir(parents=True)
    atlas_path.write_bytes(b"bytes")
    catalog = GameMaterialCatalog(
        burst_heads=(GameMaterialRecord("burst-head", str(burst_path), "10080", "head-fp"),),
        atlases=(AtlasResourceGroup("Card", str(atlas_path), ()),),
        unmatched=(),
    )
    calls = []

    def splitter(source_path, destination_dir, is_override_exists=True):
        calls.append((source_path, destination_dir, is_override_exists))
        raise RuntimeError("atlas parse failed")

    summary = export_game_materials(catalog, tmp_path / "output", splitter)

    assert (tmp_path / "output" / "game_material" / "burst-head" / "10080.png").is_file()
    assert calls == [(str(atlas_path), str(tmp_path / "output" / "fgui" / "Card"), False)]
    assert summary.failed == 1
    assert "atlas parse failed" in summary.diagnostics[0]


def test_burst_head_export_adds_deterministic_suffix_without_overwriting_source(tmp_path):
    first = tmp_path / "burst-head" / "10080.png"
    second = tmp_path / "other" / "10080.png"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    output = tmp_path / "output" / "game_material" / "burst-head"
    output.mkdir(parents=True)
    existing = output / "10080.png"
    existing.write_bytes(b"existing")

    catalog = GameMaterialCatalog(
        burst_heads=(
            GameMaterialRecord("burst-head", str(first), "10080", "first-fp"),
            GameMaterialRecord("burst-head", str(second), "10080", "second-fp"),
        ),
        atlases=(),
        unmatched=(),
    )

    summary = export_game_materials(catalog, tmp_path / "output", lambda *args: None)

    assert summary.exported == 2
    assert existing.read_bytes() == b"existing"
    assert (output / "10080_first-fp.png").read_bytes() == b"first"
    assert (output / "10080_second-fp.png").read_bytes() == b"second"


def test_burst_head_export_is_idempotent_for_the_same_source_and_fingerprint(tmp_path):
    source = tmp_path / "material" / "burst-head" / "10080.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"head")
    catalog = discover_game_materials(source.parents[1])
    output = tmp_path / "output"

    first = export_game_materials(catalog, output, lambda *args: None)
    first_files = sorted(path.name for path in (output / "game_material" / "burst-head").glob("*.png"))
    second_catalog = discover_game_materials(source.parents[1])
    second = export_game_materials(second_catalog, output, lambda *args: None)
    second_files = sorted(path.name for path in (output / "game_material" / "burst-head").glob("*.png"))

    assert first.exported == second.exported == 1
    assert first.failed == second.failed == 0
    assert first_files == second_files == ["10080.png"]


def test_missing_sources_are_reported_without_erasing_existing_output(tmp_path):
    output = tmp_path / "output" / "game_material" / "burst-head"
    output.mkdir(parents=True)
    existing = output / "keep.png"
    existing.write_bytes(b"keep")
    missing = tmp_path / "burst-head" / "missing.png"
    catalog = GameMaterialCatalog(
        burst_heads=(GameMaterialRecord("burst-head", str(missing), "missing", "missing-fp"),),
        atlases=(),
        unmatched=(),
    )

    summary = export_game_materials(catalog, tmp_path / "output", lambda *args: None)

    assert summary.failed == 1
    assert "missing" in summary.diagnostics[0].lower()
    assert existing.read_bytes() == b"keep"


def test_preview_service_discovers_and_exports_game_materials(tmp_path):
    service = PreviewService(tmp_path / "material", tmp_path / "output" / "character")
    burst = tmp_path / "material" / "burst-head" / "10080.png"
    burst.parent.mkdir(parents=True)
    burst.write_bytes(b"head")
    calls = []

    def splitter(source_path, destination_dir, is_override_exists=True):
        calls.append((source_path, destination_dir, is_override_exists))

    catalog = service.discover_game_materials()
    summary = service.export_game_materials(catalog, splitter)

    assert len(catalog.burst_heads) == 1
    assert summary.exported == 1
    assert calls == []


def test_preview_service_uses_uipackage_tool_splitter_by_default(tmp_path, monkeypatch):
    service = PreviewService(tmp_path / "material", tmp_path / "output" / "character")
    source = tmp_path / "material" / "assets" / "fairygui" / "ui" / "Card_fui.bytes"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"bytes")
    calls = []

    def splitter(source_path, destination_dir, is_override_exists=True):
        calls.append((source_path, destination_dir, is_override_exists))

    monkeypatch.setattr(UIPackageTool, "split_atlas", splitter)
    service.export_game_materials()

    assert calls == [(str(source), str(tmp_path / "output" / "fgui" / "Card"), False)]
