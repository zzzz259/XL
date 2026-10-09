"""XL 日志配置。

日志对象本身不在模块导入时初始化文件处理器。启动入口先解析运行模式，
再调用 :func:`configure_logging`，从根源上避免普通模式和 Debug 模式混用
同一套日志策略。
"""

from __future__ import annotations

import json
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
_current_session: LogSession | None = None


@dataclass(frozen=True)
class LogSession:
    session_id: str
    directory: Path
    app_log: Path
    error_log: Path
    debug_log: Path | None = None
    external_dir: Path | None = None
    events_log: Path | None = None


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = get_log_context()
        record.task_id = context.task_id
        record.parent_task = context.parent_task
        record.component = context.component
        record.stage = context.stage
        record.session_id = _current_session.session_id if _current_session else "-"
        if not hasattr(record, "event"):
            record.event = "log.record"
        return True


class _SummaryFilter(logging.Filter):
    """Keep app.log readable while detailed task records live in task logs."""

    _SUMMARY_EVENTS = {
        "task.start", "task.finish", "task.failed", "task.outcome",
        "stage.start", "stage.complete", "stage.failed",
        "process.exit", "process.failed", "process.timeout",
    }

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "event", "log.record") in self._SUMMARY_EVENTS:
            return True
        if getattr(record, "task_id", "-") == "-":
            return record.levelno >= logging.INFO
        return False


class _TaskRoutingFilter(logging.Filter):
    def __init__(self, task_id: str):
        super().__init__()
        self.task_id = task_id
        self.context_filter = _ContextFilter()

    def filter(self, record: logging.LogRecord) -> bool:
        self.context_filter.filter(record)
        return record.task_id == self.task_id


class _JsonlHandler(logging.Handler):
    """Route detailed events to task files and retain only summaries globally."""

    def __init__(self, path: Path, session_dir: Path):
        super().__init__(logging.DEBUG)
        self.path = path
        self.session_dir = session_dir
        self._streams = {}

    def emit(self, record: logging.LogRecord) -> None:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": getattr(record, "event", "log.record"),
            "message": record.getMessage(),
            "session_id": getattr(record, "session_id", "-"),
            "task_id": getattr(record, "task_id", "-"),
            "parent_task_id": getattr(record, "parent_task", "-"),
            "component": getattr(record, "component", "app"),
            "stage": getattr(record, "stage", "-"),
            "process_id": record.process,
            "thread_id": record.thread,
        }
        for name in ("error_code", "outcome", "details"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            formatter = self.formatter or logging.Formatter()
            payload["exception"] = formatter.formatException(record.exc_info)
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        task_id = payload["task_id"]
        target = self.session_dir / "tasks" / task_id / "events.jsonl" if task_id != "-" else self.path
        try:
            self._write(target, line)
            if task_id != "-" and _SummaryFilter().filter(record):
                self._write(self.path, line)
        except OSError:
            # Structured diagnostics are best-effort and must never break app work.
            pass

    def _write(self, path: Path, line: str) -> None:
        stream = self._streams.get(path)
        if stream is None or stream.closed:
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open("a", encoding="utf-8")
            self._streams[path] = stream
        stream.write(line)
        stream.flush()

    def close(self) -> None:
        for stream in self._streams.values():
            try:
                stream.close()
            except OSError:
                pass
        self._streams.clear()
        super().close()


def configure_logging(runtime=None, *, debug_mode: bool | None = None, logs_dir: str | os.PathLike[str] | None = None) -> LogSession:
    """根据运行模式配置日志处理器并创建本次诊断会话目录。"""
    if debug_mode is None:
        debug_mode = bool(getattr(runtime, "debug", False))

    global _current_session

    _remove_handlers()
    # 正式版也必须保留足够的故障诊断细节；控制台仍由自己的 handler
    # 保持简洁，文件 handler 则始终接收 DEBUG。
    logger.setLevel(logging.DEBUG)

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
    _add_file_handler(app_log, logging.DEBUG, formatter, summary_only=True)
    _add_file_handler(error_log, logging.WARNING, formatter, summary_only=True)

    debug_log = None
    if debug_mode:
        debug_log = session_dir / "debug.log"
        _add_file_handler(debug_log, logging.DEBUG, formatter)

    if log_dir_error:
        logger.warning("logging.directory_unavailable directory=%s error=%s", root, log_dir_error)

    external_dir = session_dir / "external"
    events_log = session_dir / "events.jsonl"
    session = LogSession(session_id, session_dir, app_log, error_log, debug_log, external_dir, events_log)
    _current_session = session
    try:
        json_handler = _JsonlHandler(events_log, session_dir)
        json_handler.addFilter(_ContextFilter())
        logger.addHandler(json_handler)
    except OSError as exc:
        logger.warning("logging.events_unavailable path=%s error=%s", events_log, exc)
    logger.info("logging.configured mode=%s session=%s directory=%s", "DEBUG" if debug_mode else "NORMAL", session_id, session_dir)
    return session


def get_log_session() -> LogSession | None:
    """Return the active session so subprocess diagnostics share its directory."""
    return _current_session


def begin_task_log(task_id: str):
    """Attach a detailed text log for one task; caller must close it at task end."""
    session = get_log_session()
    if session is None or not task_id or task_id == "-":
        return None
    path = session.directory / "tasks" / task_id / "task.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        logger.warning("logging.task_file_unavailable task_id=%s path=%s error=%s", task_id, path, exc)
        return None
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_formatter())
    handler.addFilter(_TaskRoutingFilter(task_id))
    logger.addHandler(handler)
    return handler


def end_task_log(handler) -> None:
    if handler is None:
        return
    logger.removeHandler(handler)
    handler.close()


def setup_logger(debug_mode: bool = False, logs_dir: str | os.PathLike[str] | None = None):
    """兼容旧调用方：配置日志并返回原有 logger 对象。"""
    configure_logging(debug_mode=debug_mode, logs_dir=logs_dir)
    return logger


def _formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-8s [%(component)s] "
        "[task=%(task_id)s] [parent=%(parent_task)s] [stage=%(stage)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _remove_handlers() -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass


def _add_file_handler(path: Path, level: int, formatter: logging.Formatter, *, summary_only: bool = False) -> bool:
    try:
        handler = logging.FileHandler(path, encoding="utf-8")
    except (OSError, PermissionError) as exc:
        logger.warning("logging.file_unavailable path=%s error=%s", path, exc)
        return False
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(_ContextFilter())
    if summary_only:
        handler.addFilter(_SummaryFilter())
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
