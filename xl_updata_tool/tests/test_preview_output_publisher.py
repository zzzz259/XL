from pathlib import Path

from app.features.preview.output_publisher import publish_raw_spine_resources
from app.features.preview.resource_catalog import discover_preview_resources
from app.features.preview.service import PreviewService


class _Runner:
    def query_skins(self, skel_path, atlas_path):
        return type("Result", (), {"ok": True, "skin_names": ("default",), "attachment_fingerprints": {}})()


def _create_spine_source(material_dir: Path, name: str = "battlespine_10080_2") -> Path:
    source_dir = material_dir / "assets" / "art" / "models"
    source_dir.mkdir(parents=True, exist_ok=True)
    skel = source_dir / f"{name}.skel"
    atlas = source_dir / f"{name}.atlas"
    texture = source_dir / f"{name}.png"
    skel.write_bytes(b"skeleton")
    atlas.write_text(f"{texture.name}\nsize: 32,32\n", encoding="utf-8")
    texture.write_bytes(b"texture")
    return skel


def test_publish_raw_spine_keeps_source_and_copies_complete_resource_group(tmp_path):
    material_dir = tmp_path / "data" / "material"
    skel = _create_spine_source(material_dir)
    catalog = discover_preview_resources(material_dir, query_runner=_Runner())

    summary = publish_raw_spine_resources(catalog, material_dir, tmp_path / "output")

    assert summary.published == 1
    assert (tmp_path / "output" / "spine").is_dir()
    copied = list((tmp_path / "output" / "spine").rglob("*"))
    assert {path.name for path in copied if path.is_file()} == {
        skel.name,
        f"{skel.stem}.atlas",
        f"{skel.stem}.png",
    }
    assert skel.read_bytes() == b"skeleton"
    assert (skel.parent / f"{skel.stem}.atlas").read_text(encoding="utf-8").startswith(skel.stem)


def test_publish_raw_spine_is_idempotent_and_reports_missing_atlas_textures(tmp_path):
    material_dir = tmp_path / "data" / "material"
    skel = _create_spine_source(material_dir, "battlespine_10081_1")
    (skel.parent / f"{skel.stem}.png").unlink()
    catalog = discover_preview_resources(material_dir, query_runner=_Runner())

    first = publish_raw_spine_resources(catalog, material_dir, tmp_path / "output")
    second = publish_raw_spine_resources(catalog, material_dir, tmp_path / "output")

    assert first.published == second.published == 1
    assert second.copied_files == first.copied_files
    assert any("texture" in message.lower() for message in second.diagnostics)
    assert len(list((tmp_path / "output" / "spine").rglob("*.skel"))) == 1


def test_service_loads_published_spine_index_without_querying_source(tmp_path):
    material_dir = tmp_path / "data" / "material"
    _create_spine_source(material_dir, "battlespine_10082_1")
    output_root = tmp_path / "output"
    catalog = discover_preview_resources(material_dir, query_runner=_Runner())
    publish_raw_spine_resources(catalog, material_dir, output_root)

    service = PreviewService(material_dir, output_root / "character")
    loaded = service.load_published_preview_resources()

    assert [record.skin_name for record in loaded.skins.values()] == ["default"]


def test_publish_raw_spine_keeps_assetstudio_prefab_pair(tmp_path):
    material_dir = tmp_path / "data" / "material"
    source_dir = material_dir / "assets" / "art" / "models"
    source_dir.mkdir(parents=True)
    skel = source_dir / "cardspine_10123_2.skel.prefab"
    atlas = source_dir / "cardspine_10123_2.atlas.prefab"
    texture = source_dir / "cardspine_10123_2.png"
    skel.write_bytes(b"skeleton")
    atlas.write_text(f"{texture.name}\nsize: 32,32\n", encoding="utf-8")
    texture.write_bytes(b"texture")

    catalog = discover_preview_resources(material_dir, query_runner=_Runner())
    summary = publish_raw_spine_resources(catalog, material_dir, tmp_path / "output")

    assert summary.published == 1
    copied_names = {
        path.name
        for path in (tmp_path / "output" / "spine").rglob("*")
        if path.is_file()
    }
    assert copied_names == {"cardspine_10123_2.skel", "cardspine_10123_2.atlas", texture.name, "preview_index.json"} or copied_names == {
        "cardspine_10123_2.skel",
        "cardspine_10123_2.atlas",
        texture.name,
    }


def test_publish_raw_spine_routes_eventcovers_without_character_id_to_eventcovers(tmp_path):
    material_dir = tmp_path / "data" / "material"
    source_dir = material_dir / "assets" / "art" / "models" / "ui_spine" / "prefab" / "eventcovers"
    source_dir.mkdir(parents=True)
    skel = source_dir / "eventcovers_0038.skel.prefab"
    atlas = source_dir / "eventcovers_0038.atlas.prefab"
    texture = source_dir / "eventcovers_0038.png"
    skel.write_bytes(b"skeleton")
    atlas.write_text(f"{texture.name}\nsize: 32,32\n", encoding="utf-8")
    texture.write_bytes(b"texture")

    catalog = discover_preview_resources(material_dir, query_runner=_Runner())
    publish_raw_spine_resources(catalog, material_dir, tmp_path / "output")

    eventcovers_root = tmp_path / "output" / "spine" / "eventcovers"
    assert eventcovers_root.is_dir()
    assert (eventcovers_root / next(path.name for path in eventcovers_root.iterdir()) / "eventcovers_0038.skel").is_file()


def test_publish_raw_spine_separates_role_families(tmp_path):
    material_dir = tmp_path / "data" / "material"
    card = _create_spine_source(material_dir, "cardspine_10080_4")
    battle = _create_spine_source(material_dir, "battlespine_10080_2")
    catalog = discover_preview_resources(material_dir, query_runner=_Runner())

    publish_raw_spine_resources(catalog, material_dir, tmp_path / "output")

    assert (tmp_path / "output" / "spine" / "cardspine").is_dir()
    assert (tmp_path / "output" / "spine" / "battlespine").is_dir()
    assert (tmp_path / "output" / "spine" / "cardspine").rglob(f"{card.name}")
    assert (tmp_path / "output" / "spine" / "battlespine").rglob(f"{battle.name}")
