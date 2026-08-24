"""日志上下文的线程/异步安全存储。"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class LogContext:
    task_id: str = "-"
    parent_task: str = "-"
    component: str = "app"
    stage: str = "-"


_context: ContextVar[LogContext] = ContextVar("xl_log_context", default=LogContext())


def get_log_context() -> LogContext:
    return _context.get()


def set_log_context(context: LogContext):
    return _context.set(context)


def reset_log_context(token) -> None:
    _context.reset(token)
