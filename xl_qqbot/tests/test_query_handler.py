"""_EventClient 查询/候选选择/静音 集成测试（不经 botpy 构造，直接装配依赖）。"""

import json

import pytest

from bot_app.groups import GroupStore
from bot_app.main import _EventClient, _extract_member_openid
from bot_app.matcher import CharacterMatcher, render_candidates_message
from bot_app.querier import CharacterQuerier
from bot_app.selection import SelectionStore
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
        self.calls.append(("image", groups, file_path))
        return {g: True for g in groups}


def make_env(tmp_path, aliases=None):
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
    client = object.__new__(_EventClient)
    client._group_store = GroupStore(str(data_dir))
    client._querier = CharacterQuerier(char_file, versions_dir)
    client._matcher = CharacterMatcher(char_file, data_dir)
    client._selection = SelectionStore()
    client._sender = sender
    client._mute = ServiceMute()
    client._bot_openid = ""
    return client, sender, data_dir


# ---------- _extract_member_openid ----------

def test_extract_member_openid_variants():
    assert _extract_member_openid({"author": {"member_openid": "u1"}}) == "u1"

    class Obj:
        pass

    msg = Obj()
    msg.author = {"member_openid": "u2"}
    assert _extract_member_openid(msg) == "u2"
    assert _extract_member_openid({}) == ""
    assert _extract_member_openid({"author": {}}) == ""


# ---------- _answer_query ----------

@pytest.mark.asyncio
async def test_direct_hit_sends_image(tmp_path):
    client, sender, _ = make_env(tmp_path)
    await client._answer_query("g1", "菲尼斯", "u1")
    assert [c[0] for c in sender.calls] == ["image"]
    assert sender.calls[0][1] == ["g1"]
    assert "10000101" in sender.calls[0][2]


@pytest.mark.asyncio
async def test_guess_sends_notice_then_image(tmp_path):
    client, sender, _ = make_env(tmp_path)
    await client._answer_query("g1", "fns", "u1")
    assert [c[0] for c in sender.calls] == ["text", "image"]
    assert sender.calls[0][2] == "已为你匹配到：菲尼斯"


@pytest.mark.asyncio
async def test_candidates_sets_pending_and_renders_message(tmp_path):
    client, sender, _ = make_env(tmp_path)
    await client._answer_query("g1", "迷迭", "u1")
    assert [c[0] for c in sender.calls] == ["text"]
    text = sender.calls[0][2]
    assert text.startswith("没有完全一致的角色")
    assert "① 迷迭香 ★6" in text
    assert "② 都不是" in text
    assert "120秒内" in text
    pending = client._selection.get("g1", "u1")
    assert pending is not None and pending.query == "迷迭"


@pytest.mark.asyncio
async def test_not_found_reply(tmp_path):
    client, sender, _ = make_env(tmp_path)
    await client._answer_query("g1", "zzzzzz", "u1")
    assert sender.calls == [("text", "g1", "未找到角色：zzzzzz")]


@pytest.mark.asyncio
async def test_new_query_clears_old_pending(tmp_path):
    client, sender, _ = make_env(tmp_path)
    await client._answer_query("g1", "迷迭", "u1")
    assert client._selection.get("g1", "u1") is not None
    await client._answer_query("g1", "菲尼斯", "u1")
    assert client._selection.get("g1", "u1") is None  # 新查询覆盖旧待选择


@pytest.mark.asyncio
async def test_mute_blocks_query(tmp_path):
    client, sender, _ = make_env(tmp_path)
    client._mute.muted = True
    await client._answer_query("g1", "菲尼斯", "u1")
    assert sender.calls == []


# ---------- _handle_selection（数字回复） ----------

@pytest.mark.asyncio
async def test_digit_selection_sends_image_and_learns(tmp_path):
    client, sender, data_dir = make_env(tmp_path)
    await client._answer_query("g1", "迷迭", "u1")
    assert client._handle_selection  # 存在性
    handled = await client._handle_selection("g1", "u1", "1")
    assert handled
    # 选中后发图 + 触发学习记录
    assert sender.calls[-1][0] == "image"
    learned = json.loads(
        (data_dir / "learned_aliases.json").read_text(encoding="utf-8")
    )
    assert "迷迭" in learned
    assert learned["迷迭"]["users"] == ["u1"]
    assert client._selection.get("g1", "u1") is None


@pytest.mark.asyncio
async def test_digit_none_choice_cancels(tmp_path):
    client, sender, _ = make_env(tmp_path)
    await client._answer_query("g1", "迷迭", "u1")
    handled = await client._handle_selection("g1", "u1", "2")  # ② 都不是
    assert handled
    assert sender.calls[-1] == ("text", "g1", "好的，已取消本次选择")
    assert client._selection.get("g1", "u1") is None


@pytest.mark.asyncio
async def test_digit_invalid_keeps_pending(tmp_path):
    client, sender, _ = make_env(tmp_path)
    await client._answer_query("g1", "迷迭", "u1")
    handled = await client._handle_selection("g1", "u1", "9")
    assert handled
    assert "请输入 1-2 之间的数字" in sender.calls[-1][2]
    assert client._selection.get("g1", "u1") is not None


@pytest.mark.asyncio
async def test_digit_without_pending_not_handled(tmp_path):
    client, sender, _ = make_env(tmp_path)
    handled = await client._handle_selection("g1", "u1", "1")
    assert not handled
    assert sender.calls == []  # 无待选择时数字消息不消费、走正常流程（不会被误当查询）


@pytest.mark.asyncio
async def test_digit_other_user_not_handled(tmp_path):
    client, sender, _ = make_env(tmp_path)
    await client._answer_query("g1", "迷迭", "u1")
    # u2 没有待选择状态，其数字消息不受影响路由
    handled = await client._handle_selection("g1", "u2", "1")
    assert not handled
    assert client._selection.get("g1", "u1") is not None


@pytest.mark.asyncio
async def test_mute_blocks_selection(tmp_path):
    client, sender, _ = make_env(tmp_path)
    await client._answer_query("g1", "迷迭", "u1")
    client._mute.muted = True
    handled = await client._handle_selection("g1", "u1", "1")
    assert handled  # 消息被消费（静默期抑制）
    assert sender.calls[-1][0] == "text"  # 候选列表那条，无新增发送
    assert len(sender.calls) == 1
    # 静音时不应清除状态？——当前实现保留状态，等待恢复后可继续选
    assert client._selection.get("g1", "u1") is not None


@pytest.mark.asyncio
async def test_learning_upgrades_alias_after_two_users(tmp_path):
    client, sender, data_dir = make_env(tmp_path)
    # u1 查询触发候选并选择
    await client._answer_query("g1", "迷迭", "u1")
    await client._handle_selection("g1", "u1", "1")
    assert client._matcher.match("迷迭").kind == "candidates"  # 1 用户未升级
    # u2 同样流程 → 升级别名
    await client._answer_query("g2", "迷迭", "u2")
    await client._handle_selection("g2", "u2", "1")
    outcome = client._matcher.match("迷迭")
    assert outcome.kind == "direct"
    assert outcome.match.name == "迷迭香"
