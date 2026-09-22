import json
import os
import tempfile

from bot_app.groups import GroupStore


def test_group_store_add_and_load():
    with tempfile.TemporaryDirectory() as data_dir:
        store = GroupStore(data_dir)
        store.add("g1", "Group One")
        store.add("g2", "Group Two")
        store.add("g1", "Duplicate")

        groups = store.load()
        openids = store.openids()
        assert openids == {"g1", "g2"}
        assert len(groups) == 2


def test_group_store_backward_compat():
    with tempfile.TemporaryDirectory() as data_dir:
        path = os.path.join(data_dir, "learned_groups.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"group_openids": ["old_g1", "old_g2"]}, f)

        store = GroupStore(data_dir)
        assert store.openids() == {"old_g1", "old_g2"}
