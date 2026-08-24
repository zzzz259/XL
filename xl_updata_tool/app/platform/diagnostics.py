"""日志、计时、任务上下文和崩溃诊断的平台契约。"""

from app.platform.crash_reporter import CrashReporter, install_crash_reporter
from app.platform.environment import collect_environment, write_environment_report
from app.platform.logger import configure_logging, logger, setup_logger, timed
from app.platform.task_context import stage_operation, task_operation

__all__ = [
    "CrashReporter",
    "collect_environment",
    "configure_logging",
    "install_crash_reporter",
    "logger",
    "setup_logger",
    "stage_operation",
    "task_operation",
    "timed",
    "write_environment_report",
]
