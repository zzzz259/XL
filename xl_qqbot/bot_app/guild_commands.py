"""QQ recruitment commands and replies to the originating group message."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, OrderedDict
from pathlib import Path
from tempfile import TemporaryDirectory

from .guild_ipc import guild_query_valid
from .keyword_handler import addressed_command, literal_bot_mention
from .selection import SelectionStore, render_numbered_candidates

_logger = logging.getLogger(__name__)
GUILD_SELECTIONS = {"guild_add": "add_selected", "guild_delete": "delete_selected"}


def parse_guild_command(text: str) -> tuple[str, str | None] | None:
    query = text.strip()
    if query == "公会":
        return "show", None
    if query == "公会刷新":
        return "refresh", None
    for prefix, action in (("添加公会", "add"), ("删除公会", "delete")):
        if query.startswith(prefix):
            return action, query[len(prefix):].strip()
    return None


class GuildCommands:
    def __init__(
        self, selection: SelectionStore, sender, store, client, *, bot_openid: str, bot_display_name: str
    ):
        self._selection = selection
        self._sender = sender
        self._store = store
        self._client = client
        self._bot_openid = bot_openid
        self._literal_mention = literal_bot_mention(bot_display_name)
        self._tasks: set[asyncio.Task] = set()
        self._seen: OrderedDict[tuple[str, str], float] = OrderedDict()

    def start(self, event_type: str, data: dict, member: str, reply_to: str) -> bool:
        group = data.get("group_openid")
        if event_type not in {"group_message", "group_at"} or not group or not member or not reply_to:
            return False
        addressed, text = addressed_command(event_type, data, self._bot_openid, self._literal_mention)
        command = parse_guild_command(text) if addressed else None
        if command is None:
            return False
        self._queue(*command, group, member, reply_to)
        return True

    def choose(self, intent: str, row, group: str, member: str, reply_to: str) -> None:
        action = GUILD_SELECTIONS[intent]
        value = row if action == "add_selected" else row.guild_uid
        self._queue(action, value, group, member, reply_to)

    def _queue(self, action, value, group, member, reply_to):
        now = time.monotonic()
        while self._seen and next(iter(self._seen.values())) <= now - 300:
            self._seen.popitem(last=False)
        delivery = (group, reply_to)
        if delivery in self._seen:
            return
        self._seen[delivery] = now
        self._selection.clear(group, member)
        pending = None
        if action in {"add", "delete"} and guild_query_valid(value):
            self._selection.set(group, member, value, [], intent=f"guild_{action}")
            pending = self._selection.get(group, member)
        task = asyncio.create_task(self._answer(action, value, group, member, reply_to, pending))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _answer(self, action, value, group, member, reply_to, pending):
        try:
            if action == "show":
                await self._send_poster(group, reply_to)
                return
            if action in {"add", "delete"} and pending is None:
                verb = "添加" if action == "add" else "删除"
                await self._sender.send_text(group, f"请发送「{verb}公会 公会名」。", reply_to)
                return
            if action == "refresh":
                await self._sender.send_text(group, "正在刷新公会，请稍候。", reply_to)
            reply = await self._client.request(action, value)
            if reply.status == "candidates":
                if pending is not None and self._selection.get(group, member) is pending:
                    await self._show_candidates(group, member, reply_to, pending, reply.options)
                return
            if reply.status in {"added", "deleted", "updated", "partial", "empty"}:
                labels = {
                    "added": f"已添加「{reply.name}」",
                    "deleted": f"已删除「{reply.name}」",
                    "partial": f"{reply.failed} 家暂未查到，图中保留之前的数据。",
                }
                await self._send_poster(group, reply_to, labels.get(reply.status, ""))
                return
            label = {
                "exists": f"「{reply.name}」已在公会名单中。",
                "missing": "没有找到匹配的公会。",
                "busy": "采集账号正在处理另一轮游戏查询，请稍后再试。",
            }.get(reply.status, "公会数据暂时无法更新，请稍后再试。")
            await self._sender.send_text(group, label, reply_to)
        except Exception:
            _logger.exception("guild command failed action=%s group=%s", action, group)
            await self._sender.send_text(group, "公会服务暂时不可用，请稍后再试。", reply_to)
        finally:
            if pending is not None and self._selection.get(group, member) is pending:
                self._selection.clear(group, member)

    async def _show_candidates(self, group, member, reply_to, pending, options):
        counts = Counter(row.name for row in options)
        labels = [
            row.name if counts[row.name] == 1 else f"{row.name}（ID {row.guild_uid}）"
            for row in options
        ]
        verb = "添加" if pending.intent == "guild_add" else "删除"
        self._selection.set(group, member, pending.query, options, intent=pending.intent)
        ready = self._selection.get(group, member)
        if not await self._sender.send_text(
            group, render_numbered_candidates(f"请选择要{verb}的公会", labels), reply_to
        ) and self._selection.get(group, member) is ready:
            self._selection.clear(group, member)

    async def _send_poster(self, group: str, reply_to: str, content: str = ""):
        image = await asyncio.to_thread(self._store.read_poster)
        if image is None:
            await self._sender.send_text(group, "公会名单正在初始化，请稍后再试。", reply_to)
            return
        with TemporaryDirectory(prefix="guild-roster-") as directory:
            path = Path(directory) / "guild-roster.png"
            path.write_bytes(image)
            await self._sender.send_local_image(str(path), content, [group], reply_to)
