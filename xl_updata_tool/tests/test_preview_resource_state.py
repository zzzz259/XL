from app.features.preview.resource_state import PreviewResourceState


def test_parent_is_new_when_any_child_fingerprint_is_unread(tmp_path):
    state = PreviewResourceState(tmp_path / "preview_state.json")
    state.mark_read("seen")

    assert not state.is_new("seen")
    assert state.is_new("unseen")
