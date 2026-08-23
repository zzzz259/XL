"""顶层和线程异常的诊断报告。"""

from __future__ import annotations

import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from .logger import logger


class CrashReporter:
    def __init__(self, directory: str | os.PathLike[str]):
        self.directory = Path(directory)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError):
            # 崩溃报告不能反过来阻止应用启动；后续写入失败会进入 logger。
            pass

    def install(self) -> None:
        sys.excepthook = self.handle
        threading.excepthook = self.handle_thread

    def handle(self, exc_type, exc_value, exc_traceback) -> Path:
        return self.write(exc_type, exc_value, exc_traceback, source="main")

    def handle_thread(self, args) -> Path:
        return self.write(args.exc_type, args.exc_value, args.exc_traceback, source=f"thread:{args.thread.name}")

    def write(self, exc_type, exc_value, exc_traceback, *, source: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self.directory / f"crash_{timestamp}.log"
        trace = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        content = f"XL Crash Report\nsource: {source}\ntime: {timestamp}\n\n{trace}"
        try:
            path.write_text(content, encoding="utf-8")
        except (OSError, PermissionError):
            logger.error("crash.report_unavailable path=%s source=%s", path, source, exc_info=True)
            return path
        logger.error("crash.report path=%s source=%s", path, source, exc_info=(exc_type, exc_value, exc_traceback))
        return path


def install_crash_reporter(directory: str | os.PathLike[str]) -> CrashReporter:
    reporter = CrashReporter(directory)
    reporter.install()
    return reporter
