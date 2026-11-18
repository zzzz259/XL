"""Record and replay custom keyword replies from raw QQ group events."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import asdict
from weakref import WeakValueDictionary

import aiohttp

from .keyword_media import (
    MAX_REPLIES,
    MAX_TEXT_LENGTH,
    KeywordMedia,
    KeywordMediaError,
    ReplyPart,
    plan_replies,
)
from .keyword_store import KeywordDraft, KeywordEntry, KeywordStore

_logger = logging.getLogger(__name__)
_END = "结束添加"
_CANCEL = "取消添加"
_LIST_COMMAND = re.compile(r"关键词列表(?:[ \t\u3000]+([0-9]+))?")
_NO_DRAFT = "当前没有进行中的关键词录入。"
_REPLY_WINDOW = 300
_EXPIRE_INTERVAL = 60


def _field(value: object) -> str:
    return value if isinstance(value, str) else ""


def _addressed(event_type: str, data: dict, bot_openid: str) -> tuple[bool, str]:
    text = _field(data.get("content"))
    addressed = event_type == "group_at"
    targets = {bot_openid} if bot_openid else set()
    mentions = data.get("mentions")
    if isinstance(mentions, list):
        for mention in mentions:
            if isinstance(mention, dict) and mention.get("is_you") is True:
                addressed = True
                targets.update(
                    _field(mention.get(key)) for key in ("member_openid", "id", "openid")
                )
    for target in targets - {""}:
        for marker in (f"<@{target}>", f"<@!{target}>"):
            if marker in text:
                addressed = True
                text = text.replace(marker, "")
    return addressed, text


def _replies(parts: Sequence[dict[str, str]]) -> tuple[ReplyPart, ...]:
    return tuple(ReplyPart(**part) for part in parts)


def _split_keyword_list(body: str, page_length: int) -> list[str]:
    pages = []
    start = 0
    while start < len(body):
        end = min(start + page_length, len(body))
        if end < len(body):
            boundary = body.rfind("\n", start, end + 1)
            if boundary > start:
                pages.append(body[start:boundary])
                start = boundary + 1
                continue
        pages.append(body[start:end])
        start = end
    return pages


class KeywordHandler:
    def __init__(
        self, store: KeywordStore, sender, *, bot_openid: str, reserved: Callable[[str], bool]
    ) -> None:
        self._store = store
        self._media = KeywordMedia(store.data_dir)
        self._sender = sender
        self._bot_openid = bot_openid
        self._reserved = reserved
        self._locks: WeakValueDictionary[tuple[str, str], asyncio.Lock] = WeakValueDictionary()
        self._handled: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._playing: dict[str, int] = {}
        self._retired: set[str] = set()
        self._next_expiry = time.monotonic() + _EXPIRE_INTERVAL
        self._store.expire(int(time.time()))
        self._media.discard_unreferenced(self._store.referenced_media())

    async def handle(self, event_type: str, data: dict) -> bool:
        if event_type not in {"group_message", "group_at"}:
            return False
        author = data.get("author")
        group = _field(data.get("group_openid"))
        member = _field(author.get("member_openid")) if isinstance(author, dict) else ""
        message_id = _field(data.get("id"))
        if not group or not member or not message_id:
            return False

        received = time.monotonic()
        if received >= self._next_expiry and not self._locks:
            for expired_id in self._store.expire(int(time.time())):
                self._discard(expired_id)
            self._next_expiry = received + _EXPIRE_INTERVAL
        while self._handled and next(iter(self._handled.values())) <= received - _REPLY_WINDOW:
            self._handled.popitem(last=False)
        delivery = (group, message_id)
        if delivery in self._handled:
            return True

        addressed, text = _addressed(event_type, data, self._bot_openid)
        actor = (group, member)
        lock = self._locks.get(actor)
        if lock is None:
            if not addressed and self._store.get_draft(*actor) is None:
                return False
            lock = self._locks[actor] = asyncio.Lock()
        async with lock:
            if delivery in self._handled:
                return True
            handled = await self._handle_group(actor, message_id, event_type, data, text, addressed)
            if handled:
                self._handled[delivery] = time.monotonic()
            return handled

    async def _handle_group(
        self, actor: tuple[str, str], message_id: str, event_type: str, data: dict, text: str, addressed: bool
    ) -> bool:
        group, member = actor
        now = int(time.time())
        draft = self._store.get_draft(group, member)
        if draft and draft.expires_at <= now:
            self._cancel(draft)
            draft = None

        query = text.strip()
        list_command = _LIST_COMMAND.fullmatch(query) if addressed else None
        if list_command:
            await self._show_list(group, message_id, list_command.group(1) or "1")
        elif addressed and query.startswith("添加"):
            await self._start(group, member, message_id, query[2:].strip(), now)
        elif addressed and query.startswith("删除"):
            role = _field(data["author"].get("member_role"))
            await self._delete(group, member, message_id, query[2:].strip(), role)
        elif self._is_control(query, data) and (draft or addressed):
            if draft is None:
                await self._notice(group, message_id, _NO_DRAFT)
            elif query == _CANCEL:
                self._cancel(draft)
                await self._notice(group, message_id, f"已取消录入『{draft.label}』。")
            else:
                await self._publish(draft, message_id, now)
        elif draft is not None:
            await self._append(draft, message_id, event_type, data, text, now)
        elif addressed and query:
            entry = self._store.lookup(group, query)
            if entry is None or self._reserved(query):
                return False
            await self._play(group, message_id, entry)
        else:
            return False
        return True

    @staticmethod
    def _is_control(query: str, data: dict) -> bool:
        return (
            query in {_END, _CANCEL}
            and data.get("message_type") in (None, 0)
            and not any(data.get(key) for key in ("attachments", "msg_elements", "ark_data"))
        )

    async def _start(self, group: str, member: str, message_id: str, label: str, now: int) -> None:
        if not label or len(label.splitlines()) != 1:
            message = "请使用『@星落罗伯特 添加关键词』格式，词名写在一行。"
        elif (
            label.startswith(("添加", "删除"))
            or label in {_END, _CANCEL}
            or _LIST_COMMAND.fullmatch(label)
            or self._reserved(label)
        ):
            message = "这个名称与现有命令或角色查询冲突，请换个名字。"
        else:
            status, draft = self._store.start(group, member, label, now)
            if status == "busy":
                message = "请先结束或取消当前的关键词录入。"
            elif status == "exists":
                message = "本群已有同名关键词，请换个名字。"
            else:
                message = f"已开始录入『{draft.label}』，发送内容后用『{_END}』保存，或用『{_CANCEL}』放弃。"
                _logger.info("stage=keyword_start status=started")
        await self._notice(group, message_id, message)

    async def _show_list(self, group: str, message_id: str, requested_page: str) -> None:
        labels = self._store.list_labels(group)
        if not labels:
            await self._notice(group, message_id, "本群还没有已保存的关键词。")
            return

        heading = f"本群关键词（共 {len(labels)} 个）"
        body = "\n".join(labels)
        body_limit = MAX_TEXT_LENGTH - len(heading) - 1
        # Leave room for the page number and the next-page command on long lists.
        pages = [body] if len(body) <= body_limit else _split_keyword_list(body, body_limit - 100)
        try:
            page = int(requested_page)
        except ValueError:
            page = 0
        if not 1 <= page <= len(pages):
            await self._notice(group, message_id, f"本群关键词共 {len(pages)} 页，请输入有效页码。")
            return

        reply = f"{heading}\n{pages[page - 1]}"
        if len(pages) > 1:
            reply += f"\n第 {page}/{len(pages)} 页"
        if page < len(pages):
            reply += f"\n发送『@星落罗伯特 关键词列表 {page + 1}』查看下一页。"
        await self._notice(group, message_id, reply)

    async def _append(self, draft: KeywordDraft, message_id: str, event_type: str, data: dict, text: str, now: int) -> None:
        if self._store.has_message(draft, message_id):
            return
        group = draft.group_openid
        try:
            parts = await self._media.capture(draft.draft_id, message_id, data, text)
            if not parts:
                return
            plan_replies(_replies(self._store.contents(draft)) + parts)
        except KeywordMediaError as error:
            self._discard(draft.draft_id, message_id)
            _logger.info("stage=keyword_record status=rejected event=%s", event_type)
            await self._notice(group, message_id, str(error))
            return

        status = self._store.append(draft, message_id, [asdict(part) for part in parts], now, completed_at=int(time.time()))
        if status == "added":
            image_count = sum(part.kind == "image" for part in parts)
            _logger.info("stage=keyword_record status=added event=%s images=%d", event_type, image_count)
        elif status == "inactive":
            self._discard(draft.draft_id, message_id)
            await self._notice(group, message_id, _NO_DRAFT)

    async def _publish(self, draft: KeywordDraft, message_id: str, now: int) -> None:
        replies: tuple[ReplyPart, ...] = ()

        def prepare(parts: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
            nonlocal replies
            replies = plan_replies(_replies(parts))
            return [asdict(part) for part in replies]

        try:
            status = self._store.publish(draft, now, prepare)
        except KeywordMediaError as error:
            await self._notice(draft.group_openid, message_id, str(error))
            return
        if status == "saved":
            message = f"已添加关键词『{draft.label}』，触发时发送 {len(replies)} 条消息。"
            _logger.info("stage=keyword_publish status=saved replies=%d", len(replies))
        elif status == "exists":
            message = "本群已有同名关键词。当前草稿仍在，可以取消录入。"
        elif status == "empty":
            message = "草稿还没有内容，请继续录入文字或图片。"
        else:
            message = _NO_DRAFT
        await self._notice(draft.group_openid, message_id, message)

    async def _delete(self, group: str, member: str, message_id: str, label: str, role: str) -> None:
        if not label or len(label.splitlines()) != 1:
            await self._notice(group, message_id, "请使用『@星落罗伯特 删除关键词』格式。")
            return
        status, media_id = self._store.delete(group, label, member, role)
        _logger.info("stage=keyword_delete status=%s", status)
        if media_id is not None:
            self._retire(media_id)
        await self._notice(group, message_id, {
            "deleted": f"已删除本群关键词『{label}』。",
            "forbidden": "创建者或本群管理员可以删除。",
            "missing": "本群没有这个关键词。",
        }[status])

    async def _notice(self, group: str, message_id: str, text: str) -> None:
        try:
            if await self._sender.send_text(group, text, message_id):
                return
        except (aiohttp.ClientError, TimeoutError, OSError, RuntimeError):
            pass
        _logger.warning("stage=keyword_notice status=failed")

    async def _play(self, group: str, message_id: str, entry: KeywordEntry) -> None:
        media_id = entry.media_id
        self._playing[media_id] = self._playing.get(media_id, 0) + 1
        attempt = 0
        try:
            for attempt, part in enumerate(_replies(entry.parts), 1):
                if part.kind == "image":
                    path = await asyncio.to_thread(self._media.resolve, part, media_id)
                    result = await self._sender.send_local_image(str(path), part.text, [group], message_id)
                    sent = result.get(group, False)
                else:
                    sent = await self._sender.send_text(group, part.text, message_id)
                if not sent:
                    raise KeywordMediaError("关键词回复发送失败。")
            _logger.info("stage=keyword_reply status=sent replies=%d", attempt)
        except (KeywordMediaError, aiohttp.ClientError, TimeoutError, OSError, RuntimeError) as error:
            _logger.warning("stage=keyword_reply status=failed part=%d reason=%s", attempt, type(error).__name__)
            if attempt < MAX_REPLIES:
                await self._notice(group, message_id, "关键词回复发送失败，请稍后重试。")
        finally:
            self._playing[media_id] -= 1
            if not self._playing[media_id]:
                del self._playing[media_id]
                if media_id in self._retired:
                    self._retired.remove(media_id)
                    self._discard(media_id)

    def _cancel(self, draft: KeywordDraft) -> None:
        if self._store.cancel(draft):
            self._retire(draft.draft_id)

    def _retire(self, media_id: str) -> None:
        if media_id in self._playing:
            self._retired.add(media_id)
        else:
            self._discard(media_id)

    def _discard(self, media_id: str, message_id: str = "") -> None:
        try:
            if message_id:
                self._media.discard_message(media_id, message_id)
            else:
                self._media.discard(media_id)
        except KeywordMediaError:
            _logger.warning("stage=keyword_cleanup status=failed")
