"""服务器版入口。"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from server_app.config import load_config
from server_app.daemon import UpdateDaemon
from server_app.pipeline import UpdatePipeline
from server_app.processor import ProductionUpdateProcessor
from server_app.state import StateStore


def main() -> int:
    parser = argparse.ArgumentParser(description="XL Update Server")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument(
        "--once",
        action="store_true",
        help="只执行一次更新处理后退出（受控运行/运维调试用，不进入常驻循环）",
    )
    args = parser.parse_args()
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
