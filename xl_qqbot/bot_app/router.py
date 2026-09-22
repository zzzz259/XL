"""事件路由器进程（python -m bot_app.router）：独占 QQ 网关，按群级别转发。

拓扑：
- 本进程：botpy websocket 网关 + 群学习 + GroupTier 分级 + 事件转发（HTTP）
  + 全部主动推送管道（Watcher outbox/播报、BilibiliWatcher， tiers 门禁保留）
- 级别服务进程（bot_app.service）：debug/test/production 各一个，只处理查询/候选选择；
  调试级服务崩溃/改动不影响正式级。

事件 envelope：`{"type": "group_message"|"group_at"|"group_add_robot",
              "muted": bool, "data": {<事件原始 dict>}}`
muted 由路由器现读 update_status.json（updating=true 即静音，与 watcher 同一来源）。
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

import aiohttp
import botpy
from botpy.connection import ConnectionState

from .bilibili import BilibiliWatcher
from .config import Config, load_config
from .groups import GroupStore, learn_group_from_event
from .tiers import GroupTier
from .updater import ServiceMute, read_update_status
from .sender import QQSender
from .watcher import Watcher

_logger = logging.getLogger(__name__)


def _install_raw_parsers() -> None:
    """覆写 botpy 解析器，直接派发原始事件 dict（统一形态，便于 JSON 转发）。"""

    def parse_group_message_create(self, payload):
        self._dispatch("group_message_create", payload.get("d", {}))

    def parse_group_at_message_create(self, payload):
        self._dispatch("group_at_message_create", payload.get("d", {}))

    def parse_group_add_robot(self, payload):
        self._dispatch("group_add_robot", payload.get("d", {}))

    ConnectionState.parse_group_message_create = parse_group_message_create
    ConnectionState.parse_group_at_message_create = parse_group_at_message_create
    ConnectionState.parse_group_add_robot = parse_group_add_robot


async def run_gateway_client(make_client, appid: str, secret: str,
                             stop_event: asyncio.Event, client_name: str = "网关客户端") -> None:
    """botpy 网关重连循环（连接被平台断开后整体重建，否则永久离线）。"""
    backoff = 5
    while not stop_event.is_set():
        client = make_client()
        try:
            coro = await client.start(appid=appid, secret=secret, ret_coro=True)
            if coro:
                await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("%s连接异常", client_name)
        if stop_event.is_set():
            break
        _logger.warning("%s连接结束，%d 秒后重连", client_name, backoff)
        backoff = min(backoff * 2, 300)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass


class TierForwarder:
    """按群级别把事件 envelope POST 到对应级别服务；失败记 error 日志不崩溃。"""

    def __init__(self, ports: dict[str, int], timeout: float = 8.0):
        self._ports = dict(ports)
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )

    def port_of(self, tier: str) -> int | None:
        return self._ports.get(tier)

    async def forward(self, tier: str, payload: dict) -> bool:
        port = self.port_of(tier)
        if port is None:
            _logger.error("级别 %s 没有配置服务端口，事件丢弃: %r", tier, payload.get("type"))
            return False
        url = f"http://127.0.0.1:{port}/event"
        try:
            await self._ensure_session()
            async with self._session.post(url, json=payload) as resp:
                if resp.status != 200:
                    _logger.error("转发 %s 服务返回 HTTP %s: type=%s",
                                  tier, resp.status, payload.get("type"))
                    return False
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            # 服务进程挂了/未启动：路由器不受影响，只记日志
            _logger.error("转发 %s 服务失败（服务可能未启动）: %s", tier, error)
            return False

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


class _RouterClient(botpy.Client):
    """网关薄壳：群学习 → 分级 → 注入 muted → HTTP 转发。"""

    def __init__(self, group_store: GroupStore, tiers: GroupTier,
                 forwarder: TierForwarder, update_status_path: Path):
        super().__init__(intents=botpy.Intents(public_messages=True))
        self._group_store = group_store
        self._tiers = tiers
        self._forwarder = forwarder
        self._update_status_path = update_status_path

    async def on_ready(self):
        _logger.info("路由器网关连接就绪（机器人上线）")

    async def on_group_message_create(self, data: dict):
        await self._route("group_message", data)

    async def on_group_at_message_create(self, data: dict):
        await self._route("group_at", data)

    async def on_group_add_robot(self, data: dict):
        learn_group_from_event(data, self._group_store)
        await self._route("group_add_robot", data)

    async def _route(self, etype: str, data: dict) -> None:
        if not isinstance(data, dict):
            _logger.warning("事件载荷不是 dict，已忽略: %r", data)
            return
        group_openid = data.get("group_openid")
        if not group_openid:
            _logger.warning("事件缺少 group_openid，已忽略: type=%s", etype)
            return
        learn_group_from_event(data, self._group_store)
        tier = self._tiers.tier_of(group_openid)
        payload = {
            "type": etype,
            "muted": self._muted_now(),
            "data": data,
        }
        _logger.info("转发事件 type=%s tier=%s group=%s", etype, tier, group_openid)
        await self._forwarder.forward(tier, payload)

    def _muted_now(self) -> bool:
        status = read_update_status(self._update_status_path)
        return bool(status and status.updating)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def _amain_router(config: Config) -> None:
    sender = QQSender(config)
    await sender.start()
    mute = ServiceMute()
    tiers = GroupTier(config.groups)
    watcher = Watcher(config, sender, mute, tiers)
    bili_watcher = BilibiliWatcher(config, sender, tiers)
    forwarder = TierForwarder(
        ports={
            "debug": config.router.debug_port,
            "test": config.router.test_port,
            "production": config.router.production_port,
        },
        timeout=config.router.forward_timeout,
    )
    # 与 watcher 的 update_events.jsonl 同一目录约定（上游 xl_updata_server 数据目录）
    update_status_path = Path(config.watch.outbox_dir).parent / "update_status.json"
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
            run_gateway_client(
                lambda: _RouterClient(
                    GroupStore(config.watch.data_dir), tiers, forwarder, update_status_path
                ),
                config.bot.appid, config.bot.secret, stop_event,
                client_name="路由器网关",
            ),
        )
    except asyncio.CancelledError:
        pass
    finally:
        await forwarder.close()
        _logger.info("关闭 sender HTTP 会话")
        await sender.close()


def main() -> None:
    _setup_logging()
    config = load_config("config.toml")
    asyncio.run(_amain_router(config))


if __name__ == "__main__":
    main()
