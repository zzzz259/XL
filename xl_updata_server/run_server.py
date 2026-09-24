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
from server_app.daemon import UpdateDaemon
from server_app.pipeline import UpdatePipeline
from server_app.processor import ProductionUpdateProcessor
from server_app.state import StateStore


def healthcheck(config_path: str) -> int:
    """Read-only local application check; never polls CDN or initializes state."""
    config = load_config(config_path)
    if not config.data_dir.is_dir():
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
    if not database.is_file():
        raise RuntimeError("state database is missing")
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=3) as connection:
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError("state database cannot be opened read-only") from exc
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
        "--once",
        action="store_true",
        help="只执行一次更新处理后退出（受控运行/运维调试用，不进入常驻循环）",
    )
    args = parser.parse_args()
    if args.healthcheck:
        try:
            return healthcheck(args.config)
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
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop_event.set())
    timezone = ZoneInfo(config.timezone_name)
    daemon = UpdateDaemon(
        config,
        UpdatePipeline(config.data_dir, processor=ProductionUpdateProcessor(config)),
        StateStore(config.data_dir / "state.sqlite"),
        clock=lambda: datetime.now(timezone),
    )
    daemon.run(stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
