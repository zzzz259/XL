"""图鉴查询/候选选择处理链：路由器转发的级别服务与旧一体化入口共用。

QueryHandler 只吃原始事件 dict（路由器转发 payload 的 data 字段同款形态），
不依赖 botpy 对象；handle() 接收路由器 envelope（type/muted/data），
handle_event() 供旧 _EventClient 直接调用（muted=None 时不改动静音标志，
保持与 watcher 共享的更新静音语义）。
"""

from __future__ import annotations

import logging
import re

from .matcher import CharacterMatcher, render_candidates_message
from .querier import CharacterQuerier
from .selection import SelectionStore
from .sender import QQSender
from .tiers import GroupTier
from .updater import ServiceMute

_logger = logging.getLogger(__name__)

# 机器人的群内显示名，用于在全量群消息里识别"@我"
BOT_DISPLAY_NAME = "星落罗伯特"
# QQ 把 @ 嵌入消息文本为 <@openid> 标记，查询前必须剥掉
_MENTION_RE = re.compile(r"<@[A-Za-z0-9]+>")


def _is_mentioned(content: str, mentions, bot_openid: str) -> bool:
    """只认@本机器人：优先按 bot_openid 精确匹配，防止把@别人的消息当查询。"""
    if bot_openid:
        if f"<@{bot_openid}>" in content:
            return True
        for item in mentions or []:
            if bot_openid in str(item):
                return True
        return BOT_DISPLAY_NAME in content
    return ("<@" in content) or BOT_DISPLAY_NAME in content or bool(mentions)


def _extract_query(content: str, mentions, bot_openid: str = "") -> tuple[bool, str]:
    """从群消息内容判断是否为图鉴查询，并提取干净的角色名。"""
    content = content or ""
    if not _is_mentioned(content, mentions, bot_openid):
        return False, ""
    query = _MENTION_RE.sub("", content).replace(BOT_DISPLAY_NAME, "").replace("@", "")
    return True, query.strip(" \u3000\r\n\t")


def _extract_member_openid(message) -> str:
    """事件 author 字段提取提问者 member_openid（候选选择按用户区分）。"""
    author = message.get("author") if isinstance(message, dict) else getattr(message, "author", None)
    if isinstance(author, dict):
        return str(author.get("member_openid") or "")
    return str(getattr(author, "member_openid", "") or "")


def _extract_message_id(message) -> str:
    """触发消息的 id，用于被动回复（引用原消息后任何群都有发送权限）。"""
    if isinstance(message, dict):
        return str(message.get("id") or "")
    return str(getattr(message, "id", "") or "")


def event_to_dict(message) -> dict:
    """botpy 消息对象 → 原始 dict 形态（best effort，供旧一体化入口复用）。"""
    if isinstance(message, dict):
        return message
    data = {}
    for key in ("id", "group_openid", "content", "mentions", "author"):
        value = getattr(message, key, None)
        if value is not None:
            data[key] = value
    return data


class QueryHandler:
    """查询与候选选择处理。候选选择状态在 SelectionStore（服务进程内存）。"""

    def __init__(self, querier: CharacterQuerier, matcher: CharacterMatcher,
                 selection: SelectionStore, sender: QQSender,
                 tiers: GroupTier | None = None, bot_openid: str = "",
                 mute: ServiceMute | None = None):
        self._querier = querier
        self._matcher = matcher
        self._selection = selection
        self._sender = sender
        self._tiers = tiers or GroupTier()
        self._bot_openid = bot_openid
        # 不传 mute 时自持（路由器转发模式按事件 muted 标志驱动）；
        # 旧一体化入口传入与 watcher 共享的实例（更新静音）
        self._mute = mute or ServiceMute()

    # ---------- 入口 ----------

    async def handle(self, payload: dict) -> None:
        """路由器 envelope：{"type", "muted", "data"}。"""
        if not isinstance(payload, dict):
            _logger.warning("事件 payload 不是对象，已忽略: %r", payload)
            return
        data = payload.get("data")
        if not isinstance(data, dict):
            _logger.warning("事件 data 不是对象，已忽略: %r", data)
            return
        await self.handle_event(
            str(payload.get("type") or ""), data,
            muted=payload.get("muted"),
        )

    async def handle_event(self, etype: str, data: dict, muted=None) -> None:
        if muted is not None:
            self._mute.muted = bool(muted)
        if etype == "group_message":
            await self._on_group_message(data)
        elif etype == "group_at":
            await self._on_group_at(data)
        elif etype == "group_add_robot":
            pass  # 群学习归路由器，级别服务无动作
        else:
            _logger.warning("未知事件类型 type=%s", etype)

    # ---------- 事件处理（data 为原始事件 dict） ----------

    async def _on_group_at(self, data: dict) -> None:
        content = str(data.get("content") or "")
        mentions = data.get("mentions")
        group_openid = data.get("group_openid")
        member_openid = _extract_member_openid(data)
        _mentioned, query = _extract_query(content, mentions, self._bot_openid)
        if not query and content:
            query = _MENTION_RE.sub("", content).strip(" \u3000\r\n\t")
        _logger.info("收到@消息 group=%s user=%s query=%r", group_openid, member_openid, query)
        if not group_openid or not query:
            return
        try:
            await self._answer_query(group_openid, query, member_openid, _extract_message_id(data))
        except Exception:
            _logger.exception("处理角色查询失败 group=%s query=%s", group_openid, query)

    async def _on_group_message(self, data: dict) -> None:
        """全量群消息：数字回复优先路由候选选择；@本机器人的当图鉴查询。"""
        content = str(data.get("content") or "")
        mentions = data.get("mentions")
        group_openid = data.get("group_openid")
        member_openid = _extract_member_openid(data)
        # 收到什么消息必须可见（截断到 80 字符），否则线上问题无法排查
        _logger.info("收到群消息 group=%s user=%s content=%r mentions=%r",
                     group_openid, member_openid, content[:80], mentions)

        # 纯数字回复：只认有待选择状态的用户，不与 @查询 冲突
        stripped = content.strip()
        if group_openid and member_openid and stripped and stripped.isdigit():
            if await self._handle_selection(group_openid, member_openid, stripped, _extract_message_id(data)):
                return

        mentioned, query = _extract_query(content, mentions, self._bot_openid)
        if not mentioned or not group_openid or not query:
            return
        _logger.info("识别为查询 group=%s user=%s query=%s", group_openid, member_openid, query)
        try:
            await self._answer_query(group_openid, query, member_openid, _extract_message_id(data))
        except Exception:
            _logger.exception("处理角色查询失败 group=%s query=%s", group_openid, query)

    # ---------- 查询与选择 ----------

    async def _answer_query(self, group_openid: str, query: str, member_openid: str = "", reply_to: str = "") -> None:
        # 功能门禁：character_query 在该群未开放则静默忽略
        if not self._tiers.available("character_query", group_openid):
            _logger.debug("功能 character_query 在群 %s 未开放，忽略查询", group_openid)
            return
        # 版本更新期间暂停响应 @查询（用户要求更新中不响应）
        if self._mute.muted:
            _logger.info("更新期间静音，忽略查询 group=%s query=%s", group_openid, query)
            return
        if member_openid:
            # 同一用户发起新查询即覆盖旧待选择
            self._selection.clear(group_openid, member_openid)

        outcome = self._matcher.match(query)
        if outcome.kind in ("direct", "guess"):
            if outcome.kind == "guess":
                await self._sender.send_text(group_openid, f"已为你匹配到：{outcome.match.name}", reply_to)
            await self._send_character(group_openid, outcome.match, query, reply_to)
        elif outcome.kind == "candidates":
            await self._sender.send_text(group_openid, render_candidates_message(list(outcome.candidates)), reply_to)
            if member_openid:
                self._selection.set(
                    group_openid, member_openid,
                    self._matcher.clean(query), list(outcome.candidates),
                )
            _logger.info(
                "候选等待 group=%s user=%s options=%s",
                group_openid, member_openid, [m.name for m in outcome.candidates],
            )
        else:
            await self._sender.send_text(group_openid, f"未找到角色：{query}", reply_to)

    async def _send_character(self, group_openid: str, match, query: str, reply_to: str = "") -> None:
        """按匹配结果发图鉴（图片定位仍归 querier）。"""
        result = self._querier.find(match.character_id)
        if result.status == "found":
            ok = await self._sender.send_image(str(result.image_path), "", [group_openid], reply_to)
            _logger.info(
                "角色查询命中 query=%s -> %s (score=%.1f, via=%s) 发图=%s",
                query, match.name, match.score, match.via, ok,
            )
        elif result.status == "no_image":
            await self._sender.send_text(group_openid, f"角色《{result.name}》的图鉴图片暂未生成", reply_to)
        else:  # 理论上 ID 必中，防数据变动兜底
            await self._sender.send_text(group_openid, f"未找到角色：{query}", reply_to)

    async def _handle_selection(self, group_openid: str, member_openid: str, digit: str, reply_to: str = "") -> bool:
        """处理数字选择；返回 True 表示消息已消费（含静音抑制）。"""
        # 功能门禁：character_query 未开放的群不应存在待选择，兜底静默消费
        if not self._tiers.available("character_query", group_openid):
            _logger.debug("功能 character_query 在群 %s 未开放，忽略候选选择", group_openid)
            return True
        pending = self._selection.get(group_openid, member_openid)
        if pending is None:
            return False
        if self._mute.muted:
            _logger.info("更新期间静音，忽略候选选择 group=%s user=%s", group_openid, member_openid)
            return True
        status, payload = self._selection.choose(group_openid, member_openid, digit)
        if status == "picked":
            match = payload
            _logger.info("候选选择 group=%s user=%s -> %s", group_openid, member_openid, match.name)
            await self._send_character(group_openid, match, pending.query, reply_to)
            record = self._matcher.record_selection(pending.query, match.character_id, member_openid)
            if record:
                _logger.info(
                    "别名学习: %s -> %s (第%d次/共%d用户)",
                    record["query"], record["name"], record["count"], record["users"],
                )
        elif status == "none":
            await self._sender.send_text(group_openid, "好的，已取消本次选择", reply_to)
        elif status == "invalid":
            await self._sender.send_text(
                group_openid, f"请输入 1-{len(pending.options) + 1} 之间的数字", reply_to
            )
        return True
