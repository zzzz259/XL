from pathlib import Path

from app.core.file_utils import atomic_write_bytes, replace_directory

CORE_DIR = Path(__file__).parents[1] / "app" / "core"


def test_core_layer_does_not_import_qt():
    """Keep the core layer usable in CLI/tests without a Qt runtime."""
    offenders = []
    for path in CORE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "PySide6" in text or "PyQt" in text:
            offenders.append(path.name)

    assert offenders == []


def test_legacy_asset_browser_has_explicit_compatibility_entrypoint():
    from app.ui.legacy.asset_browser_entry import open_legacy_asset_browser

    assert callable(open_legacy_asset_browser)


def test_ui_feature_boundaries_have_explicit_entrypoints():
    from app.ui.features.audio_controller import populate_audio_tree
    from app.ui.features.preview_controller import build_preview_item
    from app.ui.views.version_view import create_version_table

    assert callable(populate_audio_tree)
    assert callable(build_preview_item)
    assert callable(create_version_table)


def test_atomic_write_preserves_existing_file_when_transform_fails(tmp_path):
    destination = tmp_path / "asset.bundle"
    destination.write_bytes(b"known-good")

    try:
        atomic_write_bytes(destination, b"new-data", transform=lambda _: False)
    except OSError:
        pass
    else:
        raise AssertionError("transform failure must abort the atomic write")

    assert destination.read_bytes() == b"known-good"
    assert list(tmp_path.glob("*.tmp")) == []


def test_replace_directory_swaps_staged_output(tmp_path):
    destination = tmp_path / "material"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    source = tmp_path / "staging" / "assets"
    source.mkdir(parents=True)
    (source / "new.txt").write_text("new", encoding="utf-8")

    replace_directory(source, destination)

    assert (destination / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (destination / "old.txt").exists()
