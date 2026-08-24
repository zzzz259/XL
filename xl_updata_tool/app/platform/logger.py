"""XL 日志配置。

日志对象本身不在模块导入时初始化文件处理器。启动入口先解析运行模式，
再调用 :func:`configure_logging`，从根源上避免普通模式和 Debug 模式混用
同一套日志策略。
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .logging_context import get_log_context
from .paths import get_logs_dir


LOGGER_NAME = "xl_updata_tool"
logger = logging.getLogger(LOGGER_NAME)
logger.propagate = False


@dataclass(frozen=True)
class LogSession:
    session_id: str
    directory: Path
    app_log: Path
    error_log: Path
    debug_log: Path | None = None


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = get_log_context()
        record.task_id = context.task_id
        record.parent_task = context.parent_task
        record.component = context.component
        record.stage = context.stage
        return True


def configure_logging(runtime=None, *, debug_mode: bool | None = None, logs_dir: str | os.PathLike[str] | None = None) -> LogSession:
    """根据运行模式配置日志处理器并创建本次诊断会话目录。"""
    if debug_mode is None:
        debug_mode = bool(getattr(runtime, "debug", False))

    _remove_handlers()
    logger.setLevel(logging.DEBUG if debug_mode else logging.INFO)

    root = Path(logs_dir or os.environ.get("XL_LOG_DIR") or get_logs_dir())
    log_dir_error = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        _cleanup_old_logs(root, keep=20)
    except (OSError, PermissionError) as exc:
        log_dir_error = exc

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    session_dir = root / session_id
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as exc:
        log_dir_error = log_dir_error or exc
    formatter = _formatter()

    # 先挂控制台，保证日志目录或文件权限异常时应用仍能启动并报告原因。
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug_mode else logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_ContextFilter())
    logger.addHandler(console_handler)

    app_log = session_dir / "app.log"
    error_log = session_dir / "error.log"
    _add_file_handler(app_log, logging.INFO, formatter)
    _add_file_handler(error_log, logging.WARNING, formatter)

    debug_log = None
    if debug_mode:
        debug_log = session_dir / "debug.log"
        _add_file_handler(debug_log, logging.DEBUG, formatter)

    if log_dir_error:
        logger.warning("logging.directory_unavailable directory=%s error=%s", root, log_dir_error)

    session = LogSession(session_id, session_dir, app_log, error_log, debug_log)
    logger.info("logging.configured mode=%s session=%s directory=%s", "DEBUG" if debug_mode else "NORMAL", session_id, session_dir)
    return session


def setup_logger(debug_mode: bool = False, logs_dir: str | os.PathLike[str] | None = None):
    """兼容旧调用方：配置日志并返回原有 logger 对象。"""
    configure_logging(debug_mode=debug_mode, logs_dir=logs_dir)
    return logger


def _formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-8s [%(component)s] "
        "[task=%(task_id)s] [stage=%(stage)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _remove_handlers() -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass


def _add_file_handler(path: Path, level: int, formatter: logging.Formatter) -> bool:
    try:
        handler = logging.FileHandler(path, encoding="utf-8")
    except (OSError, PermissionError) as exc:
        logger.warning("logging.file_unavailable path=%s error=%s", path, exc)
        return False
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(_ContextFilter())
    logger.addHandler(handler)
    return True


def _cleanup_old_logs(logs_dir: Path, keep: int = 20) -> None:
    """保留最近会话目录，同时兼容清理旧版固定名日志。"""
    try:
        old_app_log = logs_dir / "app.log"
        if old_app_log.is_file():
            old_app_log.unlink(missing_ok=True)
        sessions = sorted(
            (entry for entry in logs_dir.iterdir() if entry.is_dir() and entry.name[:8].isdigit()),
            key=lambda entry: entry.name,
            reverse=True,
        )
        for directory in sessions[keep:]:
            shutil.rmtree(directory, ignore_errors=True)

        legacy = sorted(
            (entry for entry in logs_dir.iterdir() if entry.is_file() and entry.name.startswith("app_") and entry.suffix == ".log"),
            key=lambda entry: entry.name,
            reverse=True,
        )
        for entry in legacy[keep:]:
            entry.unlink(missing_ok=True)
    except OSError:
        pass


def timed(name=None):
    """装饰器：记录函数耗时（性能优化依据）。"""
    from functools import wraps

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                logger.info("perf.complete name=%s elapsed_ms=%.1f", name or func.__name__, (time.perf_counter() - started) * 1000)

        return wrapper

    return decorator
