"""大操作和阶段的可追踪日志上下文。"""

from __future__ import annotations

import secrets
import json
import os
import time
from contextlib import contextmanager
from functools import wraps
from typing import Callable, Iterator
from contextvars import ContextVar
from datetime import datetime, timezone

from .logging_context import LogContext, get_log_context, reset_log_context, set_log_context
from .logger import begin_task_log, end_task_log, get_log_session, logger


_task_outcome: ContextVar[dict | None] = ContextVar("xl_task_outcome", default=None)


def new_task_id(prefix: str) -> str:
    """生成适合在日志中搜索的短任务标识。"""
    clean = "".join(ch for ch in prefix.upper() if ch.isalnum()) or "TASK"
    return f"{clean}-{secrets.token_hex(4).upper()}"


def set_task_outcome(
    outcome: str,
    *,
    error_code: str | None = None,
    message: str | None = None,
    details: dict | None = None,
) -> None:
    """Set the business result for the active task; unset tasks remain unknown."""
    allowed = {"success", "partial", "failed", "cancelled", "unknown"}
    if outcome not in allowed:
        raise ValueError(f"unsupported task outcome: {outcome}")
    _task_outcome.set(
        {"outcome": outcome, "error_code": error_code, "message": message, "details": details or {}}
    )


@contextmanager
def task_context(
    prefix: str,
    *,
    task_id: str | None = None,
    parent_task: str | None = None,
    component: str | None = None,
    fields: dict | None = None,
) -> Iterator[str]:
    """为一次大操作绑定 task/parent/component，并记录开始与结束。"""
    previous = get_log_context()
    current_task = task_id or new_task_id(prefix)
    context = LogContext(
        task_id=current_task,
        parent_task=parent_task or previous.task_id if previous.task_id != "-" else (parent_task or "-"),
        component=component or previous.component,
        stage=previous.stage,
    )
    token = set_log_context(context)
    task_log_handler = begin_task_log(current_task)
    outcome_token = _task_outcome.set(None)
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    details = _format_fields(fields or {})
    logger.info(
        "task.start name=%s%s",
        prefix.lower(),
        details,
        extra={"event": "task.start", "details": fields or {}},
    )
    error = None
    try:
        yield current_task
    except Exception as exc:
        error = exc
        set_task_outcome("failed", error_code="UNHANDLED_EXCEPTION", message=str(exc))
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception("task.failed name=%s elapsed_ms=%.1f", prefix.lower(), elapsed_ms, extra={"event": "task.failed"})
        raise
    else:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("task.finish name=%s elapsed_ms=%.1f", prefix.lower(), elapsed_ms, extra={"event": "task.finish"})
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        result = _task_outcome.get() or {"outcome": "unknown", "details": {}}
        if error is not None:
            result = {**result, "outcome": "failed"}
        _write_task_summary(
            current_task,
            prefix,
            context.parent_task,
            started_at,
            elapsed_ms,
            result,
        )
        reset_task_outcome(outcome_token)
        end_task_log(task_log_handler)
        reset_log_context(token)


@contextmanager
def stage_context(component: str, stage: str) -> Iterator[None]:
    """在当前任务内切换组件/阶段，结束后恢复上层上下文。"""
    previous = get_log_context()
    token = set_log_context(
        LogContext(
            task_id=previous.task_id,
            parent_task=previous.parent_task,
            component=component,
            stage=stage,
        )
    )
    started = time.perf_counter()
    logger.debug("stage.start", extra={"event": "stage.start"})
    try:
        yield
    except Exception:
        logger.exception("stage.failed elapsed_ms=%.1f", (time.perf_counter() - started) * 1000, extra={"event": "stage.failed"})
        raise
    else:
        logger.debug("stage.complete elapsed_ms=%.1f", (time.perf_counter() - started) * 1000, extra={"event": "stage.complete"})
    finally:
        reset_log_context(token)


def task_operation(prefix: str, component: str, fields: Callable | dict | None = None):
    """给 QThread 的 run 方法增加 task 上下文，不改变原有返回值和异常语义。"""

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            values = fields(self) if callable(fields) else (fields or {})
            with task_context(prefix, component=component, fields=values):
                return func(self, *args, **kwargs)

        return wrapper

    return decorator


def stage_operation(component: str, stage: str):
    """给阶段方法增加 component/stage 上下文和开始/完成日志。"""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with stage_context(component, stage):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def _format_fields(fields: dict) -> str:
    if not fields:
        return ""
    return " " + " ".join(f"{key}={value!r}" for key, value in fields.items())


def reset_task_outcome(token) -> None:
    _task_outcome.reset(token)


def _write_task_summary(task_id: str, name: str, parent_task: str, started_at, elapsed_ms: float, result: dict) -> None:
    session = get_log_session()
    if session is None:
        return
    directory = session.directory / "tasks" / task_id
    summary = {
        "schema_version": 1,
        "session_id": session.session_id,
        "task_id": task_id,
        "parent_task_id": parent_task,
        "name": name.lower(),
        "started_at": started_at.isoformat(timespec="milliseconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "duration_ms": round(elapsed_ms, 1),
        **result,
    }
    path = directory / "summary.json"
    temporary = directory / "summary.json.tmp"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        logger.info(
            "task.outcome outcome=%s error_code=%s",
            summary.get("outcome"),
            summary.get("error_code"),
            extra={"event": "task.outcome", "outcome": summary.get("outcome"), "error_code": summary.get("error_code"), "details": summary.get("details", {})},
        )
    except OSError:
        logger.warning("task.summary_write_failed path=%s", path, exc_info=True)
