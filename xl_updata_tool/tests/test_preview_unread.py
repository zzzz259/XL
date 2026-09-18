from app.features.preview.output_browser import OutputBrowserCatalog, path_fingerprint
from app.features.preview.resource_model import PreviewResourceCatalog, SpineSkinRecord, skin_key
from app.features.preview.resource_state import PreviewResourceState
from app.features.preview.unread import build_preview_unread_snapshot
from app.features.preview.service import PreviewService


def test_unread_snapshot_aggregates_spine_character_and_material_leaves(tmp_path):
    output = tmp_path / "output"
    character_file = output / "character" / "10080" / "10080_4.png"
    material_file = output / "game_material" / "fgui" / "Card" / "button.png"
    character_file.parent.mkdir(parents=True)
    material_file.parent.mkdir(parents=True)
    character_file.write_bytes(b"character")
    material_file.write_bytes(b"material")
    record = SpineSkinRecord("10080", "hero.skel", "hero.atlas", "default", "fp", "皮肤 4", "ready")
    service = PreviewService(tmp_path / "material", output / "character")
    service.load_published_preview_resources = lambda: PreviewResourceCatalog.from_records([record])

    snapshot = build_preview_unread_snapshot(service)

    assert snapshot.spine == frozenset({skin_key(record)})
    assert snapshot.character_files == frozenset({path_fingerprint(character_file)})
    assert snapshot.material_files == frozenset({path_fingerprint(material_file)})


def test_output_folder_marker_aggregates_leaf_state_and_folder_return_marks_only_files(tmp_path):
    root = tmp_path / "output" / "character" / "10080"
    root.mkdir(parents=True)
    first = root / "10080_1.png"
    second = root / "10080_2.png"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    state = PreviewResourceState(tmp_path / "state.json")
    state.mark_read(path_fingerprint(first))

    entry = OutputBrowserCatalog.from_root(root.parent, state=state).entries[0]
    assert entry.is_new is True

    state.mark_many_read(OutputBrowserCatalog(root).file_fingerprints(recursive=False))
    state.save()

    assert not state.is_new(path_fingerprint(first))
    assert not state.is_new(path_fingerprint(second))
