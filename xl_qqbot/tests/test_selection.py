import pytest

from bot_app.matcher import Match
from bot_app.selection import PendingSelection, SelectionStore


def make_options(*names):
    return [Match(character_id=str(i), name=n, star=5, score=80.0, via="x")
            for i, n in enumerate(names, 1)]


def test_set_get_choose_picked():
    store = SelectionStore()
    options = make_options("甲", "乙")
    store.set("g1", "u1", "某词", options)
    pending = store.get("g1", "u1")
    assert pending is not None
    assert pending.query == "某词" and len(pending.options) == 2

    status, payload = store.choose("g1", "u1", "2")
    assert status == "picked"
    assert payload.name == "乙"
    # 选择后状态清除
    assert store.get("g1", "u1") is None


def test_choose_none_clears_state():
    store = SelectionStore()
    store.set("g1", "u1", "q", make_options("甲", "乙"))
    status, _ = store.choose("g1", "u1", "3")  # 第 3 项 = 都不是
    assert status == "none"
    assert store.get("g1", "u1") is None


def test_choose_invalid_keeps_state():
    store = SelectionStore()
    store.set("g1", "u1", "q", make_options("甲", "乙"))
    assert store.choose("g1", "u1", "9") == ("invalid", None)
    assert store.choose("g1", "u1", "abc") == ("invalid", None)
    assert store.get("g1", "u1") is not None  # 保留待选择


def test_no_pending():
    store = SelectionStore()
    assert store.choose("g1", "u1", "1") == ("no_pending", None)
    assert store.get("g1", "u1") is None


def test_ttl_expiry(monkeypatch):
    store = SelectionStore()
    store.set("g1", "u1", "q", make_options("甲"))
    pending = store._pending[("g1", "u1")]
    pending.created_at -= 200  # 模拟 120 秒前创建
    assert store.get("g1", "u1") is None
    assert store.choose("g1", "u1", "1") == ("no_pending", None)


def test_two_users_same_group_do_not_interfere():
    store = SelectionStore()
    store.set("g1", "u1", "q1", make_options("甲"))
    store.set("g1", "u2", "q2", make_options("乙", "丙"))

    status, payload = store.choose("g1", "u2", "1")
    assert status == "picked" and payload.name == "乙"
    # u1 的待选择不受影响
    assert store.get("g1", "u1") is not None
    status, payload = store.choose("g1", "u1", "1")
    assert status == "picked" and payload.name == "甲"


def test_new_selection_overrides_old():
    store = SelectionStore()
    store.set("g1", "u1", "旧词", make_options("甲"))
    store.set("g1", "u1", "新词", make_options("乙", "丙"))
    pending = store.get("g1", "u1")
    assert pending.query == "新词"
    assert [m.name for m in pending.options] == ["乙", "丙"]
    assert len(store) == 1


def test_clear():
    store = SelectionStore()
    store.set("g1", "u1", "q", make_options("甲"))
    store.clear("g1", "u1")
    assert store.get("g1", "u1") is None
    assert len(store) == 0


def test_pending_expired_uses_ttl_constant():
    assert PendingSelection(query="q", options=[]).expired() is False
