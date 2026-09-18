from app.features.preview.resource_model import PreviewResourceCatalog, SpineSkinRecord, skin_key
from app.features.preview.resource_state import PreviewResourceState


def test_parent_is_new_when_any_child_fingerprint_is_unread(tmp_path):
    seen = SpineSkinRecord("10080", "a.skel", "a.atlas", "seen", "hash-seen", "已读", "ready")
    unseen = SpineSkinRecord("10080", "a.skel", "a.atlas", "unseen", "hash-unseen", "未读", "ready")
    catalog = PreviewResourceCatalog.from_records([seen, unseen])
    state = PreviewResourceState(tmp_path / "preview_state.json")
    state.mark_read(skin_key(seen))

    parent_children = catalog.characters["10080"]
    assert state.is_new_for(skin_key(child) for child in parent_children)


def test_saved_read_state_is_loaded_by_a_new_instance(tmp_path):
    path = tmp_path / "preview_state.json"
    state = PreviewResourceState(path)
    state.mark_read("seen")
    state.save()

    reloaded = PreviewResourceState(path)

    assert not reloaded.is_new("seen")
    assert reloaded.is_new("unseen")


def test_malformed_state_file_recovers_as_unread_and_can_be_saved(tmp_path):
    path = tmp_path / "preview_state.json"
    path.write_text("{ interrupted", encoding="utf-8")

    state = PreviewResourceState(path)
    assert state.is_new("seen")

    state.mark_read("seen")
    state.save()

    assert not PreviewResourceState(path).is_new("seen")
