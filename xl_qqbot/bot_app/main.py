"""旧一体化入口（保留兼容，部署上已由 router + 各级别服务取代）。

进程拓扑见 bot_app/router.py：路由器独占 QQ 网关并转发事件，
本模块的 _EventClient 仅为"单进程跑全部"的旧模式保留，
事件处理本体已下沉到 bot_app/query_handler.py（与级别服务共用）。
"""

import asyncio
import logging
import signal

import botpy
from botpy.connection import ConnectionState
from botpy.message import GroupMessage

from .bilibili import BilibiliWatcher
from .config import load_config
from .groups import GroupStore, learn_group_from_event
from .matcher import CharacterMatcher
from .query_handler import (
    BOT_DISPLAY_NAME,
    QueryHandler,
    _extract_query,  # noqa: F401  旧测试/外部引用兼容
    event_to_dict,
)
from .querier import CharacterQuerier
from .router import run_gateway_client
from .selection import SelectionStore
from .sender import QQSender
from .tiers import GroupTier
from .updater import ServiceMute
from .watcher import Watcher

_logger = logging.getLogger(__name__)


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


class _EventClient(botpy.Client):
    """旧一体化模式的网关客户端：薄壳，事件处理全部委托 QueryHandler。

    botpy 对本群消息/at 消息分别派发 GroupMessage 对象（at 为原生解析），
    统一转成原始 dict 后交给 QueryHandler（与级别服务同一条链）。
    """

    def __init__(self, group_store: GroupStore, query_handler: QueryHandler):
        super().__init__(intents=botpy.Intents(public_messages=True))
        self._group_store = group_store
        self._query_handler = query_handler

    async def on_ready(self):
        _logger.info("QQ 机器人网关连接就绪（机器人上线）")

    async def on_group_at_message_create(self, message):
        learn_group_from_event(message, self._group_store)
        await self._query_handler.handle_event("group_at", event_to_dict(message))

    async def on_group_message_create(self, message):
        learn_group_from_event(message, self._group_store)
        await self._query_handler.handle_event("group_message", event_to_dict(message))

    async def on_group_add_robot(self, event):
        learn_group_from_event(event, self._group_store)


async def _run_event_client(config, sender: QQSender, stop_event: asyncio.Event, mute: ServiceMute) -> None:
    _install_group_message_parser()
    querier = CharacterQuerier(config.watch.character_data, config.watch.versions_dir)
    # matcher/selection 在重连循环外创建：网关断线重建 _EventClient 时候选状态与别名缓存不丢
    matcher = CharacterMatcher(config.watch.character_data, config.watch.data_dir)
    selection = SelectionStore()
    query_handler = QueryHandler(
        querier, matcher, selection, sender,
        tiers=GroupTier(config.groups), bot_openid=config.bot.openid, mute=mute,
    )

    def _make_client() -> _EventClient:
        return _EventClient(GroupStore(config.watch.data_dir), query_handler)

    await run_gateway_client(
        _make_client, config.bot.appid, config.bot.secret, stop_event,
        client_name="事件客户端",
    )


async def _amain(config) -> None:
    sender = QQSender(config)
    mute = ServiceMute()
    # 群分级 + 功能门禁：tiers 每次现算，配置改动立即生效
    tiers = GroupTier(config.groups)
    watcher = Watcher(config, sender, mute, tiers)
    bili_watcher = BilibiliWatcher(config, sender, tiers)
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
