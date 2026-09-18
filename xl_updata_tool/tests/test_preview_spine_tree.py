import os
from dataclasses import replace
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


def _record(role_id, skin_name, skel_name, attachment, resource_family="spine"):
    return SpineSkinRecord(
        character_id=role_id,
        source_skel=f"E:/material/{skel_name}.skel",
        atlas_path=f"E:/material/{skel_name}.atlas",
        skin_name=skin_name,
        attachment_fingerprint=attachment,
        display_name=f"显示 {skin_name}",
        status="ready",
        resource_family=resource_family,
    )


def _walk(item):
    yield item
    for index in range(item.childCount()):
        yield from _walk(item.child(index))


def test_spine_tree_retains_stable_identity_on_role_and_skin_nodes(qapp):
    record = _record("10080", "default", "cardspine_10080", "attachment-a")
    tree = PreviewSpineTree()

    tree.set_catalog(PreviewResourceCatalog.from_records([record]))

    role = tree.topLevelItem(0)
    skin = role.child(0)
    assert role.data(0, Qt.UserRole)["role_id"] == "10080"
    assert skin.data(0, Qt.UserRole)["skin_key"] == skin_key(record)
    assert skin.data(0, Qt.UserRole)["records"] == (record,)
    assert skin.text(0) == "Spine 10080"


def test_spine_tree_propagates_recursive_check_states_and_selected_records(qapp):
    first = _record("10080", "default", "cardspine_10080", "attachment-a")
    second = _record("10080", "default", "cardspine_10080_2", "attachment-b")
    tree = PreviewSpineTree()
    tree.set_catalog(PreviewResourceCatalog.from_records([first, second]))

    role = tree.topLevelItem(0)
    first_skin = role.child(0)
    second_skin = role.child(1)
    first_skin.setCheckState(0, Qt.Checked)

    assert first_skin.checkState(0) == Qt.Checked
    assert first_skin.checkState(0) == Qt.Checked
    assert role.checkState(0) == Qt.PartiallyChecked
    assert tree.selected_records() == (first,)

    role.setCheckState(0, Qt.Checked)

    assert role.checkState(0) == Qt.Checked
    assert second_skin.checkState(0) == Qt.Checked
    assert tree.selected_records() == (first, second)


def test_spine_tree_groups_character_and_background_into_one_skin_choice(qapp):
    character = _record("10080", "summer", "cardspine_10080", "character")
    background = _record("10080", "summer", "cardspine_10080_bg", "background")
    tree = PreviewSpineTree()

    tree.set_catalog(PreviewResourceCatalog.from_records([character, background]))

    role = tree.topLevelItem(0)
    assert role.childCount() == 1
    skin = role.child(0)
    assert skin.data(0, Qt.UserRole)["records"] == (character, background)

    skin.setCheckState(0, Qt.Checked)
    assert tree.selected_records() == (character,)


def test_spine_tree_groups_internal_default_and_motion_into_one_source_skin(qapp):
    default = _record("10080", "default", "cardspine_10080_2", "default")
    motion = _record("10080", "motion_angry", "cardspine_10080_2", "motion")
    default = replace(default, display_name="皮肤 2")
    motion = replace(motion, display_name="皮肤 2")
    tree = PreviewSpineTree()

    tree.set_catalog(PreviewResourceCatalog.from_records([default, motion]))

    role = tree.topLevelItem(0)
    assert role.childCount() == 1
    assert role.child(0).text(0) == "Spine 10080_2"
    assert "default" not in role.child(0).text(0).casefold()
    assert "motion" not in role.child(0).text(0).casefold()


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

        assert state.is_new(skin_key(second)) is False
        assert tree.topLevelItem(1).text(1) == ""
        assert all(item.text(1) == "" for item in _walk(tree.topLevelItem(1)))
    finally:
        state_path.unlink(missing_ok=True)


def test_checking_spine_skin_persists_read_state_for_a_new_state_instance(qapp):
    record = _record("10080", "default", "cardspine_10080", "attachment-a")
    state_path = Path.cwd() / f".task5-preview-persist-{uuid4().hex}.json"
    try:
        state = PreviewResourceState(state_path)
        tree = PreviewSpineTree(state=state)
        tree.set_catalog(PreviewResourceCatalog.from_records([record]))

        tree.topLevelItem(0).child(0).setCheckState(0, Qt.Checked)

        reloaded = PreviewResourceState(state_path)
        assert not reloaded.is_new(skin_key(record))
    finally:
        state_path.unlink(missing_ok=True)


def test_shared_attachment_fingerprint_does_not_cross_contaminate_skin_read_state(qapp):
    first = _record("10080", "default", "cardspine_10080", "same-attachment")
    second = _record("10080", "default", "cardspine_10080_2", "same-attachment")
    state_path = Path.cwd() / f".task5-preview-isolation-{uuid4().hex}.json"
    try:
        state = PreviewResourceState(state_path)
        tree = PreviewSpineTree(state=state)
        tree.set_catalog(PreviewResourceCatalog.from_records([first, second]))

        tree.topLevelItem(0).child(0).setCheckState(0, Qt.Checked)

        reloaded = PreviewResourceState(state_path)
        assert not reloaded.is_new(skin_key(first))
        assert reloaded.is_new(skin_key(second))
    finally:
        state_path.unlink(missing_ok=True)


def test_spine_recursive_check_ignores_invalid_leaves_and_selected_records(qapp):
    valid = _record("10080", "valid", "cardspine_10080", "valid-attachment")
    invalid = SpineSkinRecord(
        character_id="10080",
        source_skel="E:/material/cardspine_10080_missing.skel",
        atlas_path="",
        skin_name="invalid",
        attachment_fingerprint="invalid-attachment",
        display_name="显示 invalid",
        status="invalid",
    )
    tree = PreviewSpineTree()
    tree.set_catalog(PreviewResourceCatalog.from_records([valid, invalid]))

    role = tree.topLevelItem(0)
    role.setCheckState(0, Qt.Checked)

    valid_item = next(item for item in (role.child(0), role.child(1)) if item.text(0) == "Spine 10080")
    invalid_item = next(item for item in (role.child(0), role.child(1)) if item.text(0) == "Spine 10080_missing")
    assert role.checkState(0) == Qt.Checked
    assert valid_item.checkState(0) == Qt.Checked
    assert invalid_item.checkState(0) == Qt.Unchecked
    assert not (invalid_item.flags() & Qt.ItemIsUserCheckable)
    assert tree.selected_records() == (valid,)


def test_spine_tree_keeps_a_wide_resource_name_column(qapp):
    tree = PreviewSpineTree()

    assert tree.header().sectionSize(0) >= 500
    assert tree.header().sectionSize(1) == 96
    tree.close()


def test_spine_tree_separates_card_and_battle_families_and_localizes_eventcovers(qapp):
    card = _record("10080", "default", "cardspine_10080_4", "card", "cardspine")
    battle = _record("10080", "default", "battlespine_10080_2", "battle", "battlespine")
    event = _record(None, "default", "eventcovers_0038", "event", "eventcovers")
    tree = PreviewSpineTree()

    tree.set_catalog(PreviewResourceCatalog.from_records([card, battle, event]))

    labels = [tree.topLevelItem(index).text(0) for index in range(tree.topLevelItemCount())]
    assert any("10080" in label for label in labels)
    assert any(label == "活动封面" for label in labels)
    assert tree.topLevelItemCount() == 2
    role = next(
        tree.topLevelItem(index)
        for index in range(tree.topLevelItemCount())
        if "10080" in tree.topLevelItem(index).text(0)
    )
    child_labels = [role.child(index).text(0) for index in range(role.childCount())]
    assert "角色立绘 4" in child_labels
    assert "战斗小人 2" in child_labels
    assert all("default" not in label and "motion" not in label for label in child_labels)


def test_spine_tree_uses_one_visible_skin_for_card_and_background(qapp):
    card = _record("10080", "default", "cardspine_10080_4", "card", "cardspine")
    background = _record("10080", "default", "cardspine_10080_4_bg", "background", "cardspine")
    tree = PreviewSpineTree()

    tree.set_catalog(PreviewResourceCatalog.from_records([card, background]))

    role = tree.topLevelItem(0)
    assert role.childCount() == 1
    assert role.child(0).text(0) == "角色立绘 4"
    assert len(role.child(0).data(0, Qt.UserRole)["records"]) == 2


def test_spine_tree_selected_group_contains_one_record_per_source_part(qapp):
    card_default = _record("10080", "default", "cardspine_10080_4", "card", "cardspine")
    card_motion = _record("10080", "motion_angry", "cardspine_10080_4", "card-motion", "cardspine")
    background = _record("10080", "default", "cardspine_10080_4_bg", "background", "cardspine")
    tree = PreviewSpineTree()

    tree.set_catalog(PreviewResourceCatalog.from_records([card_default, card_motion, background]))
    tree.topLevelItem(0).child(0).setCheckState(0, Qt.Checked)

    groups = tree.selected_record_groups()
    assert len(groups) == 1
    assert [record.source_skel for record in groups[0]] == [
        cardspine_path
        for cardspine_path in (card_default.source_skel, background.source_skel)
    ]


def test_spine_tree_exposes_internal_skin_options_separately_from_visible_label(qapp):
    default = _record("10080", "default", "battlespine_10080_2", "default", "battlespine")
    motion = _record("10080", "motion_angry", "battlespine_10080_2", "motion", "battlespine")
    tree = PreviewSpineTree()
    tree.set_catalog(PreviewResourceCatalog.from_records([default, motion]))
    tree.topLevelItem(0).child(0).setCheckState(0, Qt.Checked)

    assert tree.topLevelItem(0).child(0).text(0) == "战斗小人 2"
    assert tree.selected_skin_names() == ("default", "motion_angry")


def test_spine_tree_default_selection_is_single_and_ctrl_allows_multiple(qapp):
    first = _record("10080", "default", "cardspine_10080_1", "first", "cardspine")
    second = _record("10080", "default", "cardspine_10080_2", "second", "cardspine")
    tree = PreviewSpineTree()
    tree.set_catalog(PreviewResourceCatalog.from_records([first, second]))
    role = tree.topLevelItem(0)

    role.child(0).setCheckState(0, Qt.Checked)
    role.child(1).setCheckState(0, Qt.Checked)
    assert tree.selected_records() == (second,)

    tree._mouse_multi_select = True
    role.child(0).setCheckState(0, Qt.Checked)
    assert set(tree.selected_records()) == {first, second}
