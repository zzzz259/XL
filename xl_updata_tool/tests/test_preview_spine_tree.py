import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.features.preview.resource_model import PreviewResourceCatalog, SpineSkinRecord, skin_key
from app.features.preview.resource_state import PreviewResourceState
from app.features.preview.spine_tree import PreviewSpineTree


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _record(role_id, skin_name, skel_name, attachment):
    return SpineSkinRecord(
        character_id=role_id,
        source_skel=f"E:/material/{skel_name}.skel",
        atlas_path=f"E:/material/{skel_name}.atlas",
        skin_name=skin_name,
        attachment_fingerprint=attachment,
        display_name=f"显示 {skin_name}",
        status="ready",
    )


def _walk(item):
    yield item
    for index in range(item.childCount()):
        yield from _walk(item.child(index))


def test_spine_tree_retains_stable_identity_on_role_skin_and_file_nodes(qapp):
    record = _record("10080", "default", "cardspine_10080", "attachment-a")
    tree = PreviewSpineTree()

    tree.set_catalog(PreviewResourceCatalog.from_records([record]))

    role = tree.topLevelItem(0)
    skin = role.child(0)
    spine_file = skin.child(0)
    assert role.data(0, Qt.UserRole)["role_id"] == "10080"
    assert skin.data(0, Qt.UserRole)["skin_key"] == skin_key(record)
    assert spine_file.data(0, Qt.UserRole)["skel_path"] == record.source_skel
    assert spine_file.data(0, Qt.UserRole)["atlas_path"] == record.atlas_path
    assert "显示 default" in skin.text(0)
    assert spine_file.text(0) == "cardspine_10080.skel"


def test_spine_tree_propagates_recursive_check_states_and_selected_records(qapp):
    first = _record("10080", "default", "cardspine_10080", "attachment-a")
    second = _record("10080", "summer", "cardspine_10080", "attachment-b")
    tree = PreviewSpineTree()
    tree.set_catalog(PreviewResourceCatalog.from_records([first, second]))

    role = tree.topLevelItem(0)
    first_skin = role.child(0)
    second_skin = role.child(1)
    first_skin.setCheckState(0, Qt.Checked)

    assert first_skin.checkState(0) == Qt.Checked
    assert first_skin.child(0).checkState(0) == Qt.Checked
    assert role.checkState(0) == Qt.PartiallyChecked
    assert tree.selected_records() == (first,)

    role.setCheckState(0, Qt.Checked)

    assert role.checkState(0) == Qt.Checked
    assert second_skin.checkState(0) == Qt.Checked
    assert tree.selected_records() == (first, second)


def test_spine_tree_semantic_selection_api_and_read_state_refresh(qapp):
    first = _record("10080", "default", "cardspine_10080", "attachment-a")
    second = _record("10081", "default", "cardspine_10081", "attachment-b")
    state_path = Path.cwd() / f".task5-preview-state-{uuid4().hex}.json"
    try:
        state = PreviewResourceState(state_path)
        tree = PreviewSpineTree(state=state)
        tree.set_catalog(PreviewResourceCatalog.from_records([first, second]))
        received = []
        tree.selection_changed.connect(received.append)

        tree.set_selected_records([second])
        assert tree.selected_records() == (second,)
        assert received[-1] == (second,)

        tree.mark_selected_read()

        assert state.is_new(second.attachment_fingerprint) is False
        assert tree.topLevelItem(1).text(1) == ""
        assert all(item.text(1) == "" for item in _walk(tree.topLevelItem(1)))
    finally:
        state_path.unlink(missing_ok=True)
