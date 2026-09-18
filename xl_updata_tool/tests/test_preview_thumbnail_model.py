from app.features.preview.thumbnail_model import ThumbnailCatalog, ThumbnailEntry


def test_thumbnail_catalog_groups_by_directory_identity_and_pages():
    catalog = ThumbnailCatalog(
        (
            ThumbnailEntry("output/10080/z.png", role_id="10080"),
            ThumbnailEntry("output/10081/a.png", role_id="10081"),
            ThumbnailEntry("output/10080/a.png", role_id="10080"),
        ),
        page_size=2,
    )

    assert list(catalog.grouped) == ["10080", "10081"]
    assert catalog.page_count == 2
    assert [entry.path for entry in catalog.page(0)] == ["output/10080/a.png", "output/10080/z.png"]
    assert [entry.path for entry in catalog.page(1)] == ["output/10081/a.png"]


def test_thumbnail_catalog_from_paths_never_uses_filename_as_role(tmp_path):
    root = tmp_path / "character"
    nested = root / "10080"
    nested.mkdir(parents=True)
    direct = root / "legacy.png"
    direct.write_bytes(b"")
    nested_file = nested / "not_a_role_name.png"
    nested_file.write_bytes(b"")

    catalog = ThumbnailCatalog.from_paths((str(direct), str(nested_file)), root, page_size=10)

    assert [(entry.path, entry.role_id) for entry in catalog.entries] == [
        (str(direct), ""),
        (str(nested_file), "10080"),
    ]

