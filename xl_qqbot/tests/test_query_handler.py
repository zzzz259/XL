"""QueryHandler 从事件 dict 到回复的全链路（路由器/旧入口共用处理链）。"""

import json

import pytest

from bot_app.groups import GroupStore
from bot_app.matcher import CharacterMatcher
from bot_app.query_handler import (
    QueryHandler,
    _extract_member_openid,
    _extract_query,
    event_to_dict,
)
from bot_app.querier import CharacterQuerier
from bot_app.selection import SelectionStore
from bot_app.tiers import GroupTier
from bot_app.updater import ServiceMute

CHARACTERS = {
    "10000101": {"name": "菲尼斯/Finis", "star": 5},
    "10000222": {"name": "迷迭香/Rosemary", "star": 6},
}


class FakeSender:
    def __init__(self):
        self.calls = []

    async def send_text(self, group, text, reply_to=""):
        self.calls.append(("text", group, text))
        return True

    async def send_image(self, file_path, content, groups, reply_to=""):
        self.calls.append(("image", list(groups), file_path))
        return {g: True for g in groups}


def make_handler(tmp_path, aliases=None, tiers=None, mute=None):
    data_dir = tmp_path / "data"
    char_file = data_dir / "character_data" / "current.json"
    char_file.parent.mkdir(parents=True, exist_ok=True)
    char_file.write_text(
        json.dumps({"characters": CHARACTERS}, ensure_ascii=False), encoding="utf-8"
    )
    if aliases:
        (data_dir / "aliases.json").write_text(
            json.dumps(aliases, ensure_ascii=False), encoding="utf-8"
        )
    versions_dir = data_dir / "versions"
    cards = versions_dir / "123" / "character_cards"
    cards.mkdir(parents=True)
    for cid in CHARACTERS:
        (cards / f"{cid}_角色档案_长图.png").write_bytes(b"png")
    (data_dir / "current_version.json").write_text('{"version": 123}', encoding="utf-8")

    sender = FakeSender()
    handler = QueryHandler(
        CharacterQuerier(char_file, versions_dir),
        CharacterMatcher(char_file, data_dir),
        SelectionStore(),
        sender,
        tiers=tiers or GroupTier(),
        bot_openid="",
        mute=mute,
    )
    return handler, sender, data_dir


# ---------- 工具函数 ----------

def test_extract_member_openid_variants():
    assert _extract_member_openid({"author": {"member_openid": "u1"}}) == "u1"

    class Obj:
        pass

    msg = Obj()
    msg.author = {"member_openid": "u2"}
    assert _extract_member_openid(msg) == "u2"
    assert _extract_member_openid({}) == ""
    assert _extract_member_openid({"author": {}}) == ""


def test_event_to_dict_passthrough_and_object():
    assert event_to_dict({"a": 1}) == {"a": 1}

    class Msg:
        id = "m1"
        group_openid = "g1"
        content = "hi"

    assert event_to_dict(Msg()) == {"id": "m1", "group_openid": "g1", "content": "hi"}


def at_event(group="g1", user="u1", content="<@BOT> 菲尼斯", message_id="m1"):
    return {"id": message_id, "group_openid": group, "content": content,
            "mentions": [], "author": {"member_openid": user}}


# ---------- 查询 ----------

@pytest.mark.asyncio
async def test_direct_hit_sends_image(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "菲尼斯", "u1")
    assert [c[0] for c in sender.calls] == ["image"]
    assert sender.calls[0][1] == ["g1"]


@pytest.mark.asyncio
async def test_guess_sends_notice_then_image(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "fns", "u1")
    assert [c[0] for c in sender.calls] == ["text", "image"]
    assert sender.calls[0][2] == "已为你匹配到：菲尼斯"


@pytest.mark.asyncio
async def test_candidates_set_pending_and_render_message(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "迷迭", "u1")
    assert [c[0] for c in sender.calls] == ["text"]
    text = sender.calls[0][2]
    assert text.startswith("没有完全一致的角色")
    assert "① 迷迭香 ★6" in text
    assert "② 都不是" in text
    assert "120秒内" in text
    pending = handler._selection.get("g1", "u1")
    assert pending is not None and pending.query == "迷迭"


@pytest.mark.asyncio
async def test_not_found_reply(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "zzzzzz", "u1")
    assert sender.calls == [("text", "g1", "未找到角色：zzzzzz")]


@pytest.mark.asyncio
async def test_new_query_clears_old_pending(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "迷迭", "u1")
    assert handler._selection.get("g1", "u1") is not None
    await handler._answer_query("g1", "菲尼斯", "u1")
    assert handler._selection.get("g1", "u1") is None


@pytest.mark.asyncio
async def test_mute_blocks_query(tmp_path):
    mute = ServiceMute()
    handler, sender, _ = make_handler(tmp_path, mute=mute)
    mute.muted = True
    await handler._answer_query("g1", "菲尼斯", "u1")
    assert sender.calls == []


# ---------- 事件入口（handle / handle_event） ----------

@pytest.mark.asyncio
async def test_handle_group_at_event(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler.handle({"type": "group_at", "muted": False, "data": at_event()})
    assert [c[0] for c in sender.calls] == ["image"]


@pytest.mark.asyncio
async def test_handle_group_message_mention_query(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler.handle({"type": "group_message", "muted": False,
                          "data": at_event(content="<@SOMEONE> 菲尼斯")})
    assert [c[0] for c in sender.calls] == ["image"]


@pytest.mark.asyncio
async def test_handle_group_message_plain_ignored(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler.handle({"type": "group_message", "muted": False,
                          "data": at_event(content="今天天气不错")})
    assert sender.calls == []


@pytest.mark.asyncio
async def test_handle_muted_true_ignores_query(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler.handle({"type": "group_at", "muted": True, "data": at_event()})
    assert sender.calls == []


@pytest.mark.asyncio
async def test_handle_muted_true_ignores_selection(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler.handle({"type": "group_at", "muted": False, "data": at_event(content="<@B> 迷迭")})
    assert handler._selection.get("g1", "u1") is not None
    await handler.handle({"type": "group_message", "muted": True,
                          "data": at_event(content="1")})
    assert sender.calls[-1][0] == "text"  # 只有候选列表那条
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_handle_add_robot_noop_in_service(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler.handle({"type": "group_add_robot", "muted": False,
                          "data": {"group_openid": "g9"}})
    assert sender.calls == []


@pytest.mark.asyncio
async def test_handle_invalid_payload_ignored(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler.handle(None)
    await handler.handle({"type": "group_at", "data": None})
    await handler.handle({"type": "mystery", "data": {"group_openid": "g1"}})
    assert sender.calls == []


# ---------- 数字选择 ----------

@pytest.mark.asyncio
async def test_digit_selection_sends_image_and_learns(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "迷迭", "u1")
    handled = await handler._handle_selection("g1", "u1", "1")
    assert handled
    assert sender.calls[-1][0] == "image"
    learned = json.loads(
        (data_dir / "learned_aliases.json").read_text(encoding="utf-8")
    )
    assert "迷迭" in learned
    assert learned["迷迭"]["users"] == ["u1"]
    assert handler._selection.get("g1", "u1") is None


@pytest.mark.asyncio
async def test_digit_none_choice_cancels(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "迷迭", "u1")
    handled = await handler._handle_selection("g1", "u1", "2")
    assert handled
    assert sender.calls[-1] == ("text", "g1", "好的，已取消本次选择")
    assert handler._selection.get("g1", "u1") is None


@pytest.mark.asyncio
async def test_digit_invalid_keeps_pending(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "迷迭", "u1")
    handled = await handler._handle_selection("g1", "u1", "9")
    assert handled
    assert "请输入 1-2 之间的数字" in sender.calls[-1][2]
    assert handler._selection.get("g1", "u1") is not None


@pytest.mark.asyncio
async def test_digit_without_pending_not_handled(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    handled = await handler._handle_selection("g1", "u1", "1")
    assert not handled
    assert sender.calls == []


@pytest.mark.asyncio
async def test_digit_other_user_not_handled(tmp_path):
    handler, sender, _ = make_handler(tmp_path)
    await handler._answer_query("g1", "迷迭", "u1")
    handled = await handler._handle_selection("g1", "u2", "1")
    assert not handled
    assert handler._selection.get("g1", "u1") is not None


@pytest.mark.asyncio
async def test_mute_blocks_selection(tmp_path):
    mute = ServiceMute()
    handler, sender, _ = make_handler(tmp_path, mute=mute)
    await handler._answer_query("g1", "迷迭", "u1")
    mute.muted = True
    handled = await handler._handle_selection("g1", "u1", "1")
    assert handled
    assert len(sender.calls) == 1
    assert handler._selection.get("g1", "u1") is not None  # 静音保留状态


@pytest.mark.asyncio
async def test_learning_upgrades_alias_after_two_users(tmp_path):
    handler, sender, data_dir = make_handler(tmp_path)
    await handler._answer_query("g1", "迷迭", "u1")
    await handler._handle_selection("g1", "u1", "1")
    assert handler._matcher.match("迷迭").kind == "candidates"
    await handler._answer_query("g2", "迷迭", "u2")
    await handler._handle_selection("g2", "u2", "1")
    outcome = handler._matcher.match("迷迭")
    assert outcome.kind == "direct"
    assert outcome.match.name == "迷迭香"


# ---------- 旧入口兼容：_extract_query 仍可从 bot_app.main 导入 ----------

def test_extract_query_importable_from_main():
    from bot_app.main import _extract_query as main_extract

    mentioned, query = main_extract("<@AB6AD997A31245CD31CAF9D6D793F8F0> 菲尼斯", None)
    assert mentioned and query == "菲尼斯"
