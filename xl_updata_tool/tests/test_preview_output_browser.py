from app.features.preview.output_browser import OutputBrowserCatalog


def test_output_browser_catalog_exposes_folders_and_final_images_only(tmp_path):
    root = tmp_path / "output" / "character"
    skin = root / "10080" / "skin-key"
    skin.mkdir(parents=True)
    (skin / "static.png").write_bytes(b"png")
    (root / "10081").mkdir()
    (root / "legacy.txt").write_text("legacy", encoding="utf-8")

    catalog = OutputBrowserCatalog.from_root(root)

    assert [(entry.name, entry.kind, entry.child_count) for entry in catalog.entries] == [
        ("10080", "folder", 1),
        ("10081", "folder", 0),
    ]
    skin_entries = catalog.children(root / "10080")
    assert [(entry.name, entry.kind, entry.child_count) for entry in skin_entries] == [("skin-key", "folder", 1)]
    assert [entry.name for entry in catalog.children(skin)] == ["static.png"]
