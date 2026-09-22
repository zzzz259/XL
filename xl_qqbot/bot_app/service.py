"""级别服务进程（python -m bot_app.service --tier debug|test|production --port N）。

监听 127.0.0.1:<port>，接收路由器转发的事件 envelope，处理图鉴查询与候选选择。
不自连 QQ 网关、不跑 watcher；QQSender 仅走 HTTP API（登录一次）。
候选选择状态在本进程内存（重启即丢，可接受）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from aiohttp import web

from .config import Config, load_config
from .matcher import CharacterMatcher
from .query_handler import QueryHandler
from .querier import CharacterQuerier
from .selection import SelectionStore
from .sender import QQSender
from .tiers import GroupTier

_logger = logging.getLogger(__name__)

TIERS = ("debug", "test", "production")

QUERY_HANDLER_KEY = web.AppKey("query_handler", QueryHandler)


def build_app(config: Config) -> web.Application:
    """组装 aiohttp 应用（on_startup 登录 sender，on_cleanup 关闭）。"""
    sender = QQSender(config)
    handler = QueryHandler(
        CharacterQuerier(config.watch.character_data, config.watch.versions_dir),
        CharacterMatcher(config.watch.character_data, config.watch.data_dir),
        SelectionStore(),
        sender,
        tiers=GroupTier(config.groups),
        bot_openid=config.bot.openid,
    )

    async def on_startup(app: web.Application) -> None:
        await sender.start()
        _logger.info("级别服务就绪")

    async def on_cleanup(app: web.Application) -> None:
        _logger.info("关闭 sender HTTP 会话")
        await sender.close()

    app = web.Application()
    app[QUERY_HANDLER_KEY] = handler
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_post("/event", _handle_event)
    app.router.add_get("/health", _handle_health)
    return app


async def _handle_event(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)
    handler: QueryHandler = request.app[QUERY_HANDLER_KEY]
    try:
        await handler.handle(payload)
    except Exception:
        _logger.exception("处理事件失败: %r", payload)
        return web.json_response({"ok": False, "error": "handler error"}, status=500)
    return web.json_response({"ok": True})


async def _handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="xl_qqbot 级别服务进程")
    parser.add_argument("--tier", required=True, choices=TIERS, help="本进程服务的群级别")
    parser.add_argument("--port", required=True, type=int, help="监听端口（127.0.0.1）")
    return parser.parse_args(argv)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def _amain_service(config: Config, tier: str, port: int) -> None:
    app = build_app(config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    _logger.info("级别服务启动 tier=%s listen=127.0.0.1:%s", tier, port)
    try:
        await asyncio.Event().wait()  # 常驻
    finally:
        await runner.cleanup()


def main(argv=None) -> None:
    _setup_logging()
    args = parse_args(argv)
    config = load_config("config.toml")
    asyncio.run(_amain_service(config, args.tier, args.port))


if __name__ == "__main__":
    main()
