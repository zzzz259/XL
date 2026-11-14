"""图鉴查询/候选选择处理链：路由器转发的级别服务与旧一体化入口共用。

QueryHandler 只吃原始事件 dict（路由器转发 payload 的 data 字段同款形态），
不依赖 botpy 对象；handle() 接收路由器 envelope（type/muted/data），
handle_event() 供旧 _EventClient 直接调用（muted=None 时不改动静音标志，
保持与 watcher 共享的更新静音语义）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .matcher import CharacterMatcher, render_candidates_message
from .portrait_querier import PortraitQuerier
from .querier import CharacterQuerier
from .selection import SelectionStore
from .sender import QQSender
from .updater import ServiceMute

_logger = logging.getLogger(__name__)

# 机器人的群内显示名，用于在全量群消息里识别"@我"
BOT_DISPLAY_NAME = "星落罗伯特"
# QQ 把 @ 嵌入消息文本为 <@openid> 标记，查询前必须剥掉
_MENTION_RE = re.compile(r"<@[A-Za-z0-9]+>")
# 角色名后的立绘序号：仅当整个查询以数字结尾、且序号前存在非数字名字时剥离；
# 纯数字消息（候选选择协议）、纯数字 ID、名字中间夹数字（如"觉醒2世"）都不剥离
_PORTRAIT_ORDINAL_RE = re.compile(r"^(?P<name>.*?[^0-9\s])[ \u3000]*(?P<ordinal>[0-9]+)$")
# 右端历史后缀：单独的 -（全部版本）或 -N（第 N 个历史，N>=1）；先识别它再剥立绘序号
_HISTORY_SUFFIX_RE = re.compile(r"^(?P<body>.+?)[ \u3000]*-(?:[ \u3000]*(?P<offset>[0-9]+))?$")


def _split_portrait_ordinal(query: str) -> tuple[str, int | None]:
    """剥离查询末尾的立绘序号；无序号返回 (query, None)。"""
    match = _PORTRAIT_ORDINAL_RE.match(query or "")
    if not match:
        return query, None
    return match.group("name").strip(), int(match.group("ordinal"))


@dataclass(frozen=True)
class QuerySpec:
    """统一右端语法解析结果：角色名 [立绘序号] [- [历史序号]]。"""

    name: str
    portrait_ordinal: int | None = None
    history_offset: int | None = None  # None 表示无历史后缀
    history_all: bool = False  # 末尾单独 -：含当前的全部版本
    valid: bool = True  # -0/残缺后缀为 False，handler 走正常查询错误，不静默改意图


def _parse_query_spec(query: str) -> QuerySpec:
    """先识别右端 - / -N，再按现有规则识别立绘序号；角色-1 进入图鉴历史而非立绘 1。"""
    query = (query or "").strip(" \u3000")
    match = _HISTORY_SUFFIX_RE.match(query)
    if match:
        body = match.group("body").strip(" \u3000")
        offset_text = match.group("offset")
        if offset_text is None:
            name, ordinal = _split_portrait_ordinal(body)
            return QuerySpec(name=name, portrait_ordinal=ordinal, history_all=True)
        if int(offset_text) < 1:
            return QuerySpec(name=query, valid=False)
        name, ordinal = _split_portrait_ordinal(body)
        return QuerySpec(
            name=name, portrait_ordinal=ordinal, history_offset=int(offset_text)
        )
    name, ordinal = _split_portrait_ordinal(query)
    return QuerySpec(name=name, portrait_ordinal=ordinal, valid=True)


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


_RERUN_EXACT_QUERIES = frozenset({
    "复刻", "复刻表", "复刻排期", "下次复刻", "卡池复刻", "复刻时间", "下一期谁复刻",
})
_RERUN_QUESTION_SUFFIXES = ("什么时候复刻", "何时复刻", "啥时候复刻", "什么时候会复刻")


def is_rerun_schedule_query(query: str) -> bool:
    """Match explicit commands or tightly-scoped character rerun questions."""
    normalized = str(query).strip().lower().strip(" \u3000\r\n\t?？!！。,.，")
    if normalized in _RERUN_EXACT_QUERIES:
        return True
    if len(normalized) > 48:
        return False
    return any(normalized.endswith(suffix) and len(normalized) > len(suffix) for suffix in _RERUN_QUESTION_SUFFIXES)


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
                 bot_openid: str = "", mute: ServiceMute | None = None,
                 rerun_querier=None, portrait_querier: PortraitQuerier | None = None):
        self._querier = querier
        self._matcher = matcher
        self._selection = selection
        self._sender = sender
        self._bot_openid = bot_openid
        self._rerun_querier = rerun_querier
        self._portrait_querier = portrait_querier
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
        event_type = payload.get("event_type") or payload.get("type") or ""
        await self.handle_event(
            str(event_type), data, muted=payload.get("muted")
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
        # Explicit schedule intent precedes character fuzzy matching.
        if is_rerun_schedule_query(query):
            if self._mute.muted:
                _logger.info("更新期间静音，忽略复刻表查询 group=%s", group_openid)
                return
            if member_openid:
                self._selection.clear(group_openid, member_openid)
            await self._answer_rerun_schedule(group_openid, reply_to)
            return
        # 版本更新期间暂停响应 @查询（用户要求更新中不响应）
        if self._mute.muted:
            _logger.info("更新期间静音，忽略查询 group=%s query=%s", group_openid, query)
            return
        if member_openid:
            # 同一用户发起新查询即覆盖旧待选择
            self._selection.clear(group_openid, member_openid)

        spec = _parse_query_spec(query)
        if not spec.valid:
            # -0/残缺后缀：正常查询错误，不静默改变意图（也不进模糊匹配）
            await self._sender.send_text(group_openid, f"未找到角色：{query}", reply_to)
            return
        if spec.history_offset is not None or spec.history_all:
            await self._answer_history_query(
                group_openid, spec, query, member_openid, reply_to
            )
            return

        name_query, portrait_ordinal = _split_portrait_ordinal(query)
        if portrait_ordinal is not None and self._portrait_querier is not None:
            await self._answer_portrait_query(
                group_openid, name_query, portrait_ordinal, query,
                member_openid, reply_to,
            )
            return

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

    async def _answer_rerun_schedule(self, group_openid: str, reply_to: str) -> None:
        if self._rerun_querier is None:
            await self._sender.send_text(group_openid, "复刻排期暂不可用，请稍后再试", reply_to)
            return
        result = self._rerun_querier.find()
        if result.status != "found" or result.image_path is None:
            _logger.warning("stage=rerun_query status=unavailable reason=%s", result.reason)
            await self._sender.send_text(group_openid, "复刻排期图暂不可用，请稍后再试", reply_to)
            return
        content = (
            f"复刻表数据截至游戏版本 {result.source_version}（缓存版本，尚未随当前版本更新）。"
            if result.stale else ""
        )
        send_schedule = getattr(self._sender, "send_schedule_image", None)
        if send_schedule is None:
            _logger.error("stage=rerun_send status=failed reason=safe_local_upload_unavailable")
            await self._sender.send_text(group_openid, "复刻表图片发送暂不可用", reply_to)
            return
        sent = await send_schedule(str(result.image_path), content, [group_openid], reply_to)
        succeeded = sent.get(group_openid, False) if isinstance(sent, dict) else bool(sent)
        _logger.info(
            "stage=rerun_send source_version=%s stale=%s status=%s",
            result.source_version, result.stale, "success" if succeeded else "failed",
        )
        if not succeeded:
            await self._sender.send_text(group_openid, "复刻表图片上传失败，请稍后再试", reply_to)

    async def _answer_portrait_query(self, group_openid: str, name_query: str,
                                     ordinal: int, original_query: str,
                                     member_openid: str = "", reply_to: str = "") -> None:
        """角色名+末尾数字：剥离序号后走现有角色匹配，按当前版本 manifest 查立绘。"""
        outcome = self._matcher.match(name_query)
        if outcome.kind == "candidates":
            await self._sender.send_text(group_openid, render_candidates_message(list(outcome.candidates)), reply_to)
            if member_openid:
                self._selection.set(
                    group_openid, member_openid,
                    self._matcher.clean(name_query), list(outcome.candidates),
                    intent="portrait", portrait_ordinal=ordinal,
                )
            _logger.info(
                "候选等待(立绘) group=%s user=%s options=%s ordinal=%s",
                group_openid, member_openid, [m.name for m in outcome.candidates], ordinal,
            )
            return
        if outcome.kind in ("direct", "guess"):
            if outcome.kind == "guess":
                await self._sender.send_text(group_openid, f"已为你匹配到：{outcome.match.name}", reply_to)
            await self._send_portrait(group_openid, outcome.match, ordinal, reply_to)
            return
        await self._sender.send_text(group_openid, f"未找到角色：{original_query}", reply_to)

    # ---------- 内容历史查询（-N / -，可带立绘序号） ----------

    async def _answer_history_query(self, group_openid: str, spec: QuerySpec,
                                    original_query: str, member_openid: str = "",
                                    reply_to: str = "") -> None:
        """角色名 [-N]/[-] 的内容历史；候选/guess 与图鉴查询同一套协议，只是意图带上历史。"""
        outcome = self._matcher.match(spec.name)
        if outcome.kind == "candidates":
            await self._sender.send_text(group_openid, render_candidates_message(list(outcome.candidates)), reply_to)
            if member_openid:
                self._selection.set(
                    group_openid, member_openid,
                    self._matcher.clean(spec.name), list(outcome.candidates),
                    intent="portrait" if spec.portrait_ordinal is not None else "card",
                    portrait_ordinal=spec.portrait_ordinal,
                    history_offset=spec.history_offset,
                    history_all=spec.history_all,
                )
            _logger.info(
                "候选等待(历史) group=%s user=%s options=%s ordinal=%s offset=%s all=%s",
                group_openid, member_openid, [m.name for m in outcome.candidates],
                spec.portrait_ordinal, spec.history_offset, spec.history_all,
            )
            return
        if outcome.kind in ("direct", "guess"):
            if outcome.kind == "guess":
                await self._sender.send_text(group_openid, f"已为你匹配到：{outcome.match.name}", reply_to)
            await self._send_history(group_openid, outcome.match, spec, reply_to)
            return
        await self._sender.send_text(group_openid, f"未找到角色：{original_query}", reply_to)

    async def _send_history(self, group_openid, match, spec: QuerySpec,
                            reply_to: str = "") -> None:
        """按内容历史结果发图；单历史和全部版本都逐张只发图片（content=""）。"""
        if spec.portrait_ordinal is not None:
            if self._portrait_querier is None:
                await self._sender.send_text(group_openid, "立绘历史数据暂不可用，请稍后再试", reply_to)
                return
            if spec.history_all:
                entries = self._portrait_querier.find_all_versions(match.character_id, spec.portrait_ordinal)
            else:
                entries = [self._portrait_querier.find_history(
                    match.character_id, spec.portrait_ordinal,
                    offset=spec.history_offset or 0,
                )]
        else:
            if spec.history_all:
                entries = self._querier.find_all_versions(match.character_id)
            else:
                entries = [self._querier.find_history(
                    match.character_id, offset=spec.history_offset or 0
                )]
        await self._dispatch_history_entries(group_openid, match, entries, spec, reply_to)

    async def _dispatch_history_entries(self, group_openid, match, entries,
                                        spec: QuerySpec, reply_to: str = "") -> None:
        """新到旧逐张 await 本地 multipart；任一张失败/不可用即停止，只给一条错误提示。"""
        first = entries[0]
        if first.status == "not_found":
            if spec.portrait_ordinal is not None:
                await self._sender.send_text(group_openid, f"《{match.name}》暂无立绘历史图片", reply_to)
            else:
                await self._sender.send_text(
                    group_openid, f"角色《{match.name}》的图鉴图片暂未生成", reply_to
                )
            return
        send_schedule = getattr(self._sender, "send_schedule_image", None)
        if send_schedule is None:
            _logger.error("stage=history_send status=failed reason=local_upload_unavailable")
            await self._sender.send_text(group_openid, "历史图片发送暂不可用，请稍后再试", reply_to)
            return
        is_portrait = spec.portrait_ordinal is not None
        for entry in entries:
            if entry.status == "out_of_range":
                if is_portrait and entry.total == 0 and entry.offset == 0 and entry.available_range:
                    low, high = entry.available_range
                    await self._sender.send_text(
                        group_openid,
                        f"《{match.name}》的立绘序号 {spec.portrait_ordinal} 超出范围，"
                        f"可用序号为 {low}-{high}",
                        reply_to,
                    )
                    return
                scope = f"第 {spec.portrait_ordinal} 套立绘" if is_portrait else "图鉴"
                await self._sender.send_text(
                    group_openid,
                    f"《{match.name}》的{scope}只有 {entry.total} 个内容版本，"
                    f"没有第 {entry.offset} 个历史",
                    reply_to,
                )
                return
            if entry.status != "found" or entry.image_path is None:
                reason = entry.error or entry.status
                _logger.warning("stage=history_send status=invalid reason=%s", reason)
                await self._sender.send_text(
                    group_openid, f"历史图片暂不可用（{reason}），已停止发送", reply_to
                )
                return
            sent = await send_schedule(str(entry.image_path), "", [group_openid], reply_to)
            succeeded = sent.get(group_openid, False) if isinstance(sent, dict) else bool(sent)
            _logger.info(
                "角色历史命中 query=%s -> %s 版本[%s,%s] offset=%s/%s pixel=%s 发图=%s",
                match.name, entry.artifact_sha256[:8] if entry.artifact_sha256 else "",
                entry.first_seen_version, entry.last_seen_version,
                entry.offset, entry.total,
                entry.pixel_sha256[:8] if entry.pixel_sha256 else "", succeeded,
            )
            if not succeeded:
                await self._sender.send_text(group_openid, "历史图片发送失败，已停止发送", reply_to)
                return

    async def _send_pending_history(self, group_openid, match, pending,
                                    reply_to: str = "") -> None:
        """候选选择恢复完整历史意图（含立绘序号、历史序号或 all 标记）。"""
        spec = QuerySpec(
            name=pending.query,
            portrait_ordinal=pending.portrait_ordinal,
            history_offset=pending.history_offset,
            history_all=pending.history_all,
        )
        await self._send_history(group_openid, match, spec, reply_to)

    async def _send_portrait(self, group_openid: str, match, ordinal: int,
                             reply_to: str = "") -> None:
        """按当前版本 manifest 发立绘；未生成/失败/不适用只提示，不触发渲染。"""
        result = self._portrait_querier.find(match.character_id, ordinal)
        if result.status == "found":
            content = ""
            if result.stale:
                content = (
                    f"该立绘来自游戏版本 {result.source_version}"
                    "（旧版本图片，本轮未更新）。"
                )
            send_schedule = getattr(self._sender, "send_schedule_image", None)
            if send_schedule is None:
                _logger.error("stage=portrait_send status=failed reason=local_upload_unavailable")
                await self._sender.send_text(group_openid, "立绘图片发送暂不可用", reply_to)
                return
            sent = await send_schedule(str(result.image_path), content, [group_openid], reply_to)
            succeeded = sent.get(group_openid, False) if isinstance(sent, dict) else bool(sent)
            _logger.info(
                "角色立绘命中 query=%s -> %s#%s (via=%s) 发图=%s",
                match.name, result.name, ordinal, match.via, succeeded,
            )
            if not succeeded:
                await self._sender.send_text(group_openid, "立绘图片发送失败，请稍后再试", reply_to)
            return
        if result.status == "out_of_range":
            low, high = result.available_range or (1, 1)
            await self._sender.send_text(
                group_openid,
                f"《{match.name}》的立绘序号 {ordinal} 超出范围，可用序号为 {low}-{high}",
                reply_to,
            )
        elif result.status == "not_generated":
            await self._sender.send_text(
                group_openid, f"《{match.name}》的第 {ordinal} 套立绘尚未生成，请稍后再试", reply_to
            )
        elif result.status == "not_applicable":
            await self._sender.send_text(
                group_openid, f"《{match.name}》的第 {ordinal} 套立绘当前版本不适用", reply_to
            )
        elif result.status == "failed":
            await self._sender.send_text(
                group_openid, f"《{match.name}》的第 {ordinal} 套立绘生成失败，请稍后再试", reply_to
            )
        elif result.status == "not_found":
            await self._sender.send_text(group_openid, f"《{match.name}》暂无立绘数据", reply_to)
        else:  # unavailable：指针/manifest/产物校验不过
            await self._sender.send_text(group_openid, "立绘数据暂不可用，请稍后再试", reply_to)

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
            has_history = pending.history_offset is not None or pending.history_all
            if pending.intent == "portrait" and self._portrait_querier is not None:
                if has_history:
                    await self._send_pending_history(group_openid, match, pending, reply_to)
                else:
                    await self._send_portrait(
                        group_openid, match, pending.portrait_ordinal or 1, reply_to
                    )
            elif has_history:
                await self._send_pending_history(group_openid, match, pending, reply_to)
            else:
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
