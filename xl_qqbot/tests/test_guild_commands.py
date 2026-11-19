import asyncio
from pathlib import Path

import pytest
from bot_app.guild_commands import GuildCommands
from bot_app.guild_roster import GuildEntry, GuildReply, GuildRosterStore, GuildSnapshot
from bot_app.keyword_store import KeywordStore
from bot_app.matcher import CharacterMatcher
from bot_app.querier import CharacterQuerier
from bot_app.query_handler import QueryHandler
from bot_app.selection import SelectionStore


class Sender:
    def __init__(self):
        self.sent = asyncio.Queue()

    async def send_text(self, group, text, reply_to):
        await self.sent.put(("text", group, text, reply_to))
        return True

    async def send_local_image(self, file_path, content, groups, reply_to):
        await self.sent.put(("image", tuple(groups), Path(file_path).read_bytes(), reply_to, content))
        return {group: True for group in groups}

    async def receive(self):
        return await asyncio.wait_for(self.sent.get(), 3)


class Jobs:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    async def request(self, operation, value):
        self.calls.append((operation, value))
        return self.replies[operation]


def build_handler(tmp_path, replies, openid="bot"):
    jobs = Jobs(replies)
    selection = SelectionStore()
    sender = Sender()
    store = GuildRosterStore(tmp_path / "guild-data")
    store.save(GuildSnapshot(()), b"cached-guild-poster")
    guilds = GuildCommands(
        selection, sender, store, jobs, bot_openid=openid, bot_display_name="星落罗伯特"
    )
    chars = tmp_path / "characters.json"
    handler = QueryHandler(
        CharacterQuerier(chars, tmp_path),
        CharacterMatcher(chars, tmp_path),
        selection, sender, bot_openid=openid, keyword_store=KeywordStore(str(tmp_path)),
        guild_commands=guilds,
    )
    return handler, sender, jobs, guilds


async def deliver(handler, content, message_id="msg1", *, actor="u1", group="g1", at=False, muted=False,
                  role=None):
    author = {"member_openid": actor}
    if role is not None:
        author["member_role"] = role
    await handler.handle({
        "event_type": "group_at" if at else "group_message",
        "data": {
            "id": message_id, "group_openid": group, "content": content,
            "author": author, "mentions": [],
        },
        "muted": muted,
    })


@pytest.mark.asyncio
async def test_list_uses_cache_for_real_and_literal_at(tmp_path):
    handler, sender, jobs, guilds = build_handler(tmp_path, {}, openid="")
    try:
        await deliver(handler, "<@SomeOpenID> 公会", at=True)
        assert await sender.receive() == ("image", ("g1",), b"cached-guild-poster", "msg1", "")
        await deliver(handler, "@星落罗伯特 公会", "msg2", role="member")
        assert (await sender.receive())[3] == "msg2"
        assert jobs.calls == []
    finally:
        await guilds.close()


@pytest.mark.asyncio
async def test_add_and_delete_route_ahead_of_custom_keywords_and_return_new_poster(tmp_path):
    handler, sender, jobs, guilds = build_handler(tmp_path, {
        "add": GuildReply("added", "全世界最好的K."),
        "delete": GuildReply("deleted", "全世界最好的K."),
    })
    try:
        await deliver(handler, "@星落罗伯特 添加公会全世界最好的K", "add1", role="admin")
        assert (await sender.receive())[3:] == ("add1", "已添加「全世界最好的K.」")
        await deliver(handler, "<@bot> 删除公会 全世界最好的K.", "del1", at=True, role="owner")
        assert (await sender.receive())[3:] == ("del1", "已删除「全世界最好的K.」")
        assert jobs.calls == [("add", "全世界最好的K"), ("delete", "全世界最好的K.")]
    finally:
        await guilds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["member", None, ["owner"]])
@pytest.mark.parametrize(("content", "at", "action", "status"), [
    ("@星落罗伯特 添加公会休闲屋", False, "add", "added"),
    ("<@bot> 删除公会 休闲屋", True, "delete", "deleted"),
])
async def test_guild_writes_require_admin_or_owner(tmp_path, content, at, action, status, role):
    handler, sender, jobs, guilds = build_handler(tmp_path, {action: GuildReply(status, "休闲屋")})
    try:
        await deliver(handler, content, "forbidden", at=at, role=role)
        reply = await sender.receive()
        assert jobs.calls == []
        assert reply == ("text", "g1", "只有本群管理员或群主可以添加或删除公会。", "forbidden")
    finally:
        await guilds.close()


@pytest.mark.asyncio
async def test_candidates_use_portrait_digit_rules_and_cannot_cross_groups_or_members(tmp_path):
    choices = (GuildEntry("1", "甲公会"), GuildEntry("2", "乙公会"), GuildEntry("3", "丙公会"))
    handler, sender, jobs, guilds = build_handler(tmp_path, {
        "add": GuildReply("candidates", options=choices),
        "add_selected": GuildReply("added", choices[1].name),
    })
    try:
        await deliver(handler, "@星落罗伯特 添加公会公会", "search1", role="admin")
        prompt = (await sender.receive())[2]
        assert all(f"{number} {row.name}" in prompt for number, row in zip("①②③", choices))
        assert "回复数字 1/2/3/4" in prompt and "120秒内" in prompt
        await deliver(handler, "2", "other-member", actor="u2", role="owner")
        await deliver(handler, "2", "other-group", group="g2", role="owner")
        await deliver(handler, "2", "muted", muted=True, role="admin")
        assert jobs.calls == [("add", "公会")]
        await deliver(handler, "2", "choose2", role="admin")
        assert (await sender.receive())[3:] == ("choose2", "已添加「乙公会」")
        assert jobs.calls == [("add", "公会"), ("add_selected", choices[1])]
    finally:
        await guilds.close()


@pytest.mark.asyncio
async def test_delete_candidate_does_not_call_add_and_keeps_passive_reply_id(tmp_path):
    choices = (GuildEntry("1", "恶魔の心跳"), GuildEntry("2", "恶魔の呢喃"))
    handler, sender, jobs, guilds = build_handler(tmp_path, {
        "delete": GuildReply("candidates", options=choices),
        "delete_selected": GuildReply("deleted", choices[0].name),
    })
    try:
        await deliver(handler, "@星落罗伯特 删除公会恶魔", "search", role="owner")
        assert "恶魔の呢喃" in (await sender.receive())[2]
        await deliver(handler, "1", "chosen", role="owner")
        assert (await sender.receive())[3:] == ("chosen", "已删除「恶魔の心跳」")
        assert jobs.calls == [("delete", "恶魔"), ("delete_selected", "1")]
    finally:
        await guilds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "verb", "status"), [
    ("add", "添加", "added"),
    ("delete", "删除", "deleted"),
])
@pytest.mark.parametrize("role", ["member", None])
async def test_guild_candidate_rechecks_current_role(tmp_path, action, verb, status, role):
    choices = (GuildEntry("1", "甲公会"), GuildEntry("2", "乙公会"))
    handler, sender, jobs, guilds = build_handler(tmp_path, {
        action: GuildReply("candidates", options=choices),
        f"{action}_selected": GuildReply(status, choices[0].name),
    })
    try:
        await deliver(handler, f"@星落罗伯特 {verb}公会公会", "search", role="admin")
        assert "回复数字" in (await sender.receive())[2]
        await deliver(handler, "1", "forbidden", role=role)
        reply = await sender.receive()
        assert jobs.calls == [(action, "公会")]
        assert reply == ("text", "g1", "只有本群管理员或群主可以添加或删除公会。", "forbidden")
    finally:
        await guilds.close()


@pytest.mark.asyncio
async def test_refresh_replies_promptly_and_then_sends_image(tmp_path):
    handler, sender, jobs, guilds = build_handler(tmp_path, {})
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def hold(operation, value):
        assert (operation, value) == ("refresh", None)
        entered.set()
        await finish.wait()
        return GuildReply("partial", failed=1)

    jobs.request = hold
    try:
        await asyncio.wait_for(deliver(handler, "@星落罗伯特 公会刷新", "ref1", role="member"), 1)
        assert "正在刷新" in (await sender.receive())[2]
        await asyncio.wait_for(entered.wait(), 1)
        assert sender.sent.empty()
        finish.set()
        result = await sender.receive()
        assert result[:4] == ("image", ("g1",), b"cached-guild-poster", "ref1")
        assert "1 家" in result[4]
    finally:
        await guilds.close()
