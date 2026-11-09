"""服务器版入口。"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
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
    if getattr(config, "portrait_enabled", False):
        _portrait_healthcheck(config)
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


def _portrait_healthcheck(config) -> None:
    """Validate the portrait staging configuration without rendering.

    Checks are strictly read-only: configured tool paths must be readable
    files, the Tool source tree must contain the headless entry, and the
    configured render interpreter must be able to import the headless entry
    and its basic third-party dependencies. No render, CDN access or data
    write happens here.
    """
    tool_dir = config.portrait_tool_dir or (Path(__file__).resolve().parents[1] / "xl_updata_tool")
    if not tool_dir.is_dir():
        raise RuntimeError("configured portrait tool directory is missing")
    if not (tool_dir / "app" / "features" / "preview" / "portrait_headless.py").is_file():
        raise RuntimeError("portrait tool directory is missing the headless entry")
    required = (
        ("portraits.dotnet_path", config.portrait_dotnet_path),
        ("portraits.scene_cli_path", config.portrait_scene_cli_path),
        ("portraits.assetstudio_cli", config.portrait_assetstudio_cli),
    )
    optional = (
        ("portraits.query_cli_path", config.portrait_query_cli_path),
        ("portraits.xvfb_run_path", config.portrait_xvfb_run_path),
        ("portraits.assetstudio_dotnet", config.portrait_assetstudio_dotnet),
    )
    for label, path in required:
        if path is None:
            raise RuntimeError(f"{label} is required when portraits.enabled is true")
        _require_readable_file(label, path)
    for label, path in optional:
        if path is not None:
            _require_readable_file(label, path)
    for label, path in (("portraits.work_dir", config.portrait_work_dir),
                        ("portraits.outbox_dir", config.portrait_outbox_dir)):
        if path is not None and path.exists() and not path.is_dir():
            raise RuntimeError(f"configured {label} is not a directory: {path}")
    _portrait_dependency_probe(config, tool_dir)


def _require_readable_file(label: str, path: Path) -> None:
    if not path.is_file() or not os.access(path, os.R_OK):
        raise RuntimeError(f"configured {label} is not a readable file: {path}")


def _portrait_dependency_probe(config, tool_dir: Path) -> None:
    python_bin = config.portrait_python_bin or sys.executable
    env = dict(os.environ)
    python_path = str(tool_dir)
    if env.get("PYTHONPATH"):
        python_path = python_path + os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = python_path
    try:
        probe = subprocess.run(
            [python_bin, "-B", "-c",
             "import app.features.preview.portrait_headless, PIL, UnityPy"],
            check=False, cwd=str(tool_dir), env=env, capture_output=True,
            text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"portrait render python is not usable: {python_bin}: {exc}") from exc
    if probe.returncode != 0:
        tail = (probe.stderr or "").strip()[-300:]
        raise RuntimeError(
            "portrait render python cannot import the headless entry or basic "
            f"dependencies (PIL, UnityPy): {tail}"
        )


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
