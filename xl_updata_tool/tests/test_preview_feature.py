from app.features.preview.service import PreviewService


def test_preview_export_compatibility_entrypoints_resolve_to_same_callable():
    from app.features.preview.export_controller import batch_export_with_dialog
    from app.ui.features.export_controller import batch_export_with_dialog as legacy_batch_export

    assert batch_export_with_dialog is legacy_batch_export


def test_preview_service_scans_final_images_recursively_and_roles(tmp_path):
    material = tmp_path / "material"
    cardspine = material / "assets" / "art" / "models" / "cardspine"
    cardspine.mkdir(parents=True)
    (cardspine / "zeta.skel").write_bytes(b"")
    (cardspine / "zeta.atlas").write_bytes(b"")

    preview = tmp_path / "output" / "character"
    (preview / "zeta").mkdir(parents=True)
    first = preview / "zeta" / "zeta.png"
    first.write_bytes(b"png")

    service = PreviewService(material, preview)

    assert service.image_paths() == [str(first)]
    assert service.has_images()
    assert service.cardspine_roles() == ["zeta"]
    assert service.preview_roles() == ["zeta"]
    assert service.skel_map()["zeta"] == (
        str(cardspine / "zeta.skel"), str(cardspine / "zeta.atlas")
    )


def test_preview_service_creates_final_output_directory(tmp_path):
    service = PreviewService(tmp_path / "material", tmp_path / "output" / "character")

    output = service.ensure_output_dir()

    assert output.is_dir()
    assert service.image_paths() == []
