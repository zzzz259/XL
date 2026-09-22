import asyncio
import logging
import re
import signal

import botpy
from botpy.connection import ConnectionState
from botpy.message import GroupMessage

from .bilibili import BilibiliWatcher
from .config import load_config
from .groups import GroupStore
from .matcher import CharacterMatcher, render_candidates_message
from .querier import CharacterQuerier
from .selection import SelectionStore
from .sender import QQSender
from .updater import ServiceMute
from .watcher import Watcher

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


def _install_group_message_parser() -> None:
    """botpy 1.2 不认识 group_message_create（全量群消息事件），手动注册解析器。

    开放平台开启「获取全部信息」后，平台对群内消息（含 @机器人）一律下发
    GROUP_MESSAGE_CREATE，botpy 默认只会报 unknown event 并丢弃。
    """

    def parse_group_message_create(self, payload):
        message = GroupMessage(self.api, payload.get("id", None), payload.get("d", {}))
        self._dispatch("group_message_create", message)

    ConnectionState.parse_group_message_create = parse_group_message_create


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _learn_group(event_obj, group_store: GroupStore) -> None:
    # botpy 的事件载荷是 dict，不是带属性的对象
    group_openid = None
    if isinstance(event_obj, dict):
        group_openid = event_obj.get("group_openid")
    else:
        group_openid = getattr(event_obj, "group_openid", None)
    if not group_openid:
        return
    # 当前官方事件（GROUP_AT_MESSAGE_CREATE / GROUP_ADD_ROBOT）均不携带群名，
    # name 字段预留，便于后续事件扩展或人工补录。
    group_store.add(openid=group_openid, name="")
    _logger.info("从群事件学到群 openid: %s", group_openid)


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


class _EventClient(botpy.Client):
    """botpy 用子类方法接收事件，没有 client.on 装饰器。"""

    def __init__(self, group_store: GroupStore, querier: CharacterQuerier,
                 matcher: CharacterMatcher, selection: SelectionStore,
                 sender: QQSender, mute: ServiceMute, bot_openid: str = ""):
        super().__init__(intents=botpy.Intents(public_messages=True))
        self._group_store = group_store
        self._querier = querier
        self._matcher = matcher
        self._selection = selection
        self._sender = sender
        self._mute = mute
        self._bot_openid = bot_openid

    async def on_ready(self):
        _logger.info("QQ 机器人网关连接就绪（机器人上线）")

    async def on_group_at_message_create(self, message):
        _learn_group(message, self._group_store)
        # botpy 的 GroupMessage 对象与原始 dict 两种形态都兼容
        group_openid = message.get("group_openid") if isinstance(message, dict) else getattr(message, "group_openid", None)
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        mentions = message.get("mentions") if isinstance(message, dict) else getattr(message, "mentions", None)
        member_openid = _extract_member_openid(message)
        _mentioned, query = _extract_query(content or "", mentions, self._bot_openid)
        if not query and content:
            query = _MENTION_RE.sub("", content).strip(" \u3000\r\n\t")
        _logger.info("收到@消息 group=%s user=%s query=%r", group_openid, member_openid, query)
        if not group_openid or not query:
            return
        try:
            await self._answer_query(group_openid, query, member_openid, _extract_message_id(message))
        except Exception:
            _logger.exception("处理角色查询失败 group=%s query=%s", group_openid, query)

    async def on_group_message_create(self, message):
        """全量群消息事件：数字回复优先路由候选选择；@本机器人的当图鉴查询。"""
        _learn_group(message, self._group_store)
        content = getattr(message, "content", "") or ""
        mentions = getattr(message, "mentions", None)
        group_openid = getattr(message, "group_openid", None)
        member_openid = _extract_member_openid(message)
        # 收到什么消息必须可见（截断到 80 字符），否则线上问题无法排查
        _logger.info("收到群消息 group=%s user=%s content=%r mentions=%r", group_openid, member_openid, content[:80], mentions)

        # 纯数字回复：只认有待选择状态的用户，不与 @查询 冲突
        stripped = content.strip()
        if group_openid and member_openid and stripped and stripped.isdigit():
            if await self._handle_selection(group_openid, member_openid, stripped, _extract_message_id(message)):
                return

        mentioned, query = _extract_query(content, mentions, self._bot_openid)
        if not mentioned or not group_openid or not query:
            return
        _logger.info("识别为查询 group=%s user=%s query=%s", group_openid, member_openid, query)
        try:
            await self._answer_query(group_openid, query, member_openid, _extract_message_id(message))
        except Exception:
            _logger.exception("处理角色查询失败 group=%s query=%s", group_openid, query)

    async def _answer_query(self, group_openid: str, query: str, member_openid: str = "", reply_to: str = "") -> None:
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

    async def on_group_add_robot(self, event):
        _learn_group(event, self._group_store)


async def _run_event_client(config, sender: QQSender, stop_event: asyncio.Event, mute: ServiceMute) -> None:
    _install_group_message_parser()
    querier = CharacterQuerier(config.watch.character_data, config.watch.versions_dir)
    # matcher/selection 在重连循环外创建：网关断线重建 _EventClient 时候选状态与别名缓存不丢
    matcher = CharacterMatcher(config.watch.character_data, config.watch.data_dir)
    selection = SelectionStore()
    backoff = 5
    while not stop_event.is_set():
        client = _EventClient(
            GroupStore(config.watch.data_dir), querier, matcher, selection, sender, mute, config.bot.openid
        )
        try:
            coro = await client.start(
                appid=config.bot.appid,
                secret=config.bot.secret,
                ret_coro=True,
            )
            if coro:
                await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("事件客户端连接异常")
        # QQ 网关会定期以 4009(Session timed out) 等理由断开，botpy 不会自动重连，
        # 必须整体重建连接，否则机器人永久离线
        if stop_event.is_set():
            break
        _logger.warning("事件客户端连接结束，%d 秒后重连", backoff)
        backoff = min(backoff * 2, 300)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass


async def _amain(config) -> None:
    sender = QQSender(config)
    mute = ServiceMute()
    watcher = Watcher(config, sender, mute)
    bili_watcher = BilibiliWatcher(config, sender)
    await sender.start()
    stop_event = asyncio.Event()

    def _shutdown() -> None:
        _logger.info("收到退出信号，准备关闭...")
        stop_event.set()
        watcher.stop()
        bili_watcher.stop()
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)

    try:
        await asyncio.gather(
            watcher.run(),
            bili_watcher.run(),
            _run_event_client(config, sender, stop_event, mute),
        )
    except asyncio.CancelledError:
        pass
    finally:
        _logger.info("关闭 sender HTTP 会话")
        await sender.close()


def main() -> None:
    _setup_logging()
    config = load_config("config.toml")
    # 必须让 asyncio.run 统一管理事件循环：手工 new_event_loop 会让 botpy/aiohttp
    # 绑定到导入期的旧 loop 上，网络调用在新 loop 里永久挂起且不报错。
    asyncio.run(_amain(config))


if __name__ == "__main__":
    main()
