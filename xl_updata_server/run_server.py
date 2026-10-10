"""服务器版入口。"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from server_app.config import load_config
from server_app.control_api import build_control_server, read_api_token
from server_app.daemon import UpdateDaemon
from server_app.pipeline import UpdatePipeline
from server_app.processor import ProductionUpdateProcessor
from server_app.state import StateStore
from server_app.update_coordinator import UpdateCoordinator


def healthcheck(config_path: str, expected_environment: str | None = None) -> int:
    """Read-only local application check; never polls CDN or initializes state."""
    config = load_config(config_path)
    if expected_environment is not None and config.environment != expected_environment:
        raise RuntimeError(
            f"configured environment is {config.environment!r}, expected {expected_environment!r}"
        )
    first_test_start = expected_environment == "test" and (
        not config.data_dir.exists()
        or not (config.data_dir / "state.sqlite").exists()
    )
    if config.data_dir.exists() and not config.data_dir.is_dir():
        raise RuntimeError("configured data path is not a directory")
    if not config.data_dir.is_dir() and not first_test_start:
        raise RuntimeError("configured data directory is missing")
    if not config.unluac_jar.is_file() or not config.unluac_opmap.exists():
        raise RuntimeError("configured unluac decoder files are missing")
    if not _executable_available(config.java_bin):
        raise RuntimeError("configured Java executable is unavailable")
    if config.render_cards:
        renderer_modules = config.config_path.parent / "renderer" / "node_modules"
        if not renderer_modules.is_dir() or not _executable_available(config.node_bin):
            raise RuntimeError("configured card renderer dependencies are unavailable")
    database = config.data_dir / "state.sqlite"
    if database.is_file():
        try:
            with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=3) as connection:
                connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        except sqlite3.Error as exc:
            raise RuntimeError("state database cannot be opened read-only") from exc
    elif not first_test_start:
        raise RuntimeError("state database is missing")
    return 0


def _executable_available(value: str) -> bool:
    path = Path(value).expanduser()
    if path.is_absolute() or path.parent != Path("."):
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(value) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="XL Update Server")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--healthcheck", action="store_true", help="只读应用自检，不连接 CDN")
    parser.add_argument(
        "--expect-environment", choices=("test", "main"),
        help="健康检查时要求配置声明指定运行环境",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只执行一次更新处理后退出（受控运行/运维调试用，不进入常驻循环）",
    )
    args = parser.parse_args()
    if args.healthcheck:
        try:
            return healthcheck(args.config, args.expect_environment)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            parser.error(f"health check failed: {exc}")
    config = load_config(args.config)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.once:
        pipeline = UpdatePipeline(config.data_dir, processor=ProductionUpdateProcessor(config))
        result = pipeline.run_once()
        logging.getLogger(__name__).info("run_once result=%s", result)
        return 1 if result.error else 0
    stop_event = threading.Event()
    timezone = ZoneInfo(config.timezone_name)
    pipeline = UpdatePipeline(config.data_dir, processor=ProductionUpdateProcessor(config))
    state_store = StateStore(config.data_dir / "state.sqlite")
    coordinator = UpdateCoordinator(
        config,
        pipeline,
        state_store,
        clock=lambda: datetime.now(timezone),
    )

    def request_shutdown(_signum, _frame) -> None:
        coordinator.stop_accepting()
        stop_event.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    daemon = UpdateDaemon(
        config,
        pipeline,
        state_store,
        clock=lambda: datetime.now(timezone),
        coordinator=coordinator,
    )
    api_server = None
    api_thread = None
    if config.api_enabled:
        token = read_api_token(config.api_token_env)
        api_server = build_control_server(config, coordinator, token)
        api_thread = threading.Thread(
            target=api_server.serve_forever,
            kwargs={"poll_interval": 0.5},
            name="update-control-api",
            daemon=True,
        )
        api_thread.start()
        logging.getLogger(__name__).info(
            "control API ready environment=%s listen=%s:%s cdn_poll_enabled=%s",
            config.environment,
            config.api_host,
            config.api_port,
            config.cdn_poll_enabled,
        )
    try:
        daemon.run(stop_event)
    finally:
        coordinator.stop_accepting()
        if api_server is not None:
            api_server.shutdown()
            api_server.server_close()
            if api_thread is not None:
                api_thread.join(timeout=3)
        if not coordinator.wait_idle(timeout=240):
            logging.getLogger(__name__).error(
                "update run did not finish before graceful shutdown timeout"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
