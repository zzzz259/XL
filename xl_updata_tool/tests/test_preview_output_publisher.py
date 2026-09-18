from pathlib import Path

from app.features.preview.output_publisher import publish_raw_spine_resources
from app.features.preview.resource_catalog import discover_preview_resources


class _Runner:
    def query_skins(self, skel_path, atlas_path):
        return type("Result", (), {"ok": True, "skin_names": ("default",), "attachment_fingerprints": {}})()


def _create_spine_source(material_dir: Path, name: str = "battlespine_10080_2") -> Path:
    source_dir = material_dir / "assets" / "art" / "models"
    source_dir.mkdir(parents=True)
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
