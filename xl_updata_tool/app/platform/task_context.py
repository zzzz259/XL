"""大操作和阶段的可追踪日志上下文。"""

from __future__ import annotations

import secrets
import time
from contextlib import contextmanager
from functools import wraps
from typing import Callable, Iterator

from .logging_context import LogContext, get_log_context, reset_log_context, set_log_context
from .logger import logger


def new_task_id(prefix: str) -> str:
    """生成适合在日志中搜索的短任务标识。"""
    clean = "".join(ch for ch in prefix.upper() if ch.isalnum()) or "TASK"
    return f"{clean}-{secrets.token_hex(2).upper()}"


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
    started = time.perf_counter()
    details = _format_fields(fields or {})
    logger.info("task.start name=%s%s", prefix.lower(), details)
    try:
        yield current_task
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception("task.failed name=%s elapsed_ms=%.1f", prefix.lower(), elapsed_ms)
        raise
    else:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("task.complete name=%s elapsed_ms=%.1f", prefix.lower(), elapsed_ms)
    finally:
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
    logger.debug("stage.start")
    try:
        yield
    except Exception:
        logger.exception("stage.failed elapsed_ms=%.1f", (time.perf_counter() - started) * 1000)
        raise
    else:
        logger.debug("stage.complete elapsed_ms=%.1f", (time.perf_counter() - started) * 1000)
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
