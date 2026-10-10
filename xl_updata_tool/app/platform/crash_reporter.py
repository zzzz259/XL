"""顶层和线程异常的诊断报告。"""

from __future__ import annotations

import os
import faulthandler
import sys
import threading
import traceback
from collections import deque
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
        self._fatal_stream = None
        self._qt_handler = None
        self._previous_qt_handler = None

    def install(self) -> None:
        sys.excepthook = self.handle
        sys.unraisablehook = self.handle_unraisable
        threading.excepthook = self.handle_thread
        try:
            fatal_path = self.directory / "fatal.log"
            self._fatal_stream = fatal_path.open("a", encoding="utf-8", buffering=1)
            faulthandler.enable(file=self._fatal_stream, all_threads=True)
        except (OSError, RuntimeError, ValueError):
            logger.exception("crash.faulthandler_unavailable directory=%s", self.directory)

    def install_qt_handler(self):
        """Route Qt diagnostics, including qFatal messages, into app logs."""
        from PySide6.QtCore import qInstallMessageHandler

        self._qt_handler = self.handle_qt_message
        self._previous_qt_handler = qInstallMessageHandler(self._qt_handler)
        return self._previous_qt_handler

    def report_previous_unclean_exit(self, current_session_dir: str | os.PathLike[str]) -> Path | None:
        """Record a prior app session that disappeared without a clean-exit marker."""
        current = Path(current_session_dir).resolve()
        sessions_root = current.parent
        try:
            candidates = sorted(
                (path for path in sessions_root.iterdir() if path.is_dir() and path.resolve() != current),
                key=lambda path: path.name,
                reverse=True,
            )
        except OSError:
            logger.warning("crash.previous_session_scan_failed directory=%s", sessions_root, exc_info=True)
            return None
        for previous in candidates:
            app_log = previous / "app.log"
            if not app_log.is_file():
                continue
            tail = deque(maxlen=20)
            has_start = False
            try:
                with app_log.open("r", encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        has_start = has_start or "application.start " in line
                        tail.append(line.rstrip())
            except OSError:
                logger.warning("crash.previous_session_log_unreadable path=%s", app_log, exc_info=True)
                continue
            if not has_start:
                continue
            if "application.shutdown" in "\n".join(tail) or any(previous.glob("crash_*.log")):
                return None

            detected_at = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            report = previous / "crash_detected.log"
            if report.exists():
                report = previous / f"crash_detected_{detected_at}.log"
            content = [
                "XL Unclean Exit Detection",
                f"previous_session: {previous.name}",
                f"detected_at: {detected_at}",
                "reason: application.start was recorded but application.shutdown was not.",
                "This may indicate a crash, forced termination, or system shutdown; logs alone cannot distinguish them.",
                "last_log_lines:",
                *tail,
                "",
            ]
            try:
                report.write_text("\n".join(content), encoding="utf-8")
            except OSError:
                logger.exception("crash.previous_session_report_unavailable path=%s", report)
                report = None
            logger.error(
                "application.previous_session_unclean_exit session=%s report=%s last_lines=%r",
                previous.name,
                report,
                list(tail),
                extra={
                    "event": "application.previous_session_unclean_exit",
                    "error_code": "UNCLEAN_PREVIOUS_EXIT",
                    "details": {"previous_session": previous.name, "report": str(report) if report else None},
                },
            )
            self._flush_diagnostics()
            return report
        return None

    def handle_qt_message(self, message_type, context, message) -> None:
        from PySide6.QtCore import QtMsgType

        level_map = {
            QtMsgType.QtDebugMsg: ("debug", logger.debug),
            QtMsgType.QtInfoMsg: ("info", logger.info),
            QtMsgType.QtWarningMsg: ("warning", logger.warning),
            QtMsgType.QtCriticalMsg: ("critical", logger.error),
            QtMsgType.QtFatalMsg: ("fatal", logger.critical),
        }
        level_name, log = level_map.get(message_type, (str(message_type), logger.error))
        details = {
            "qt_category": getattr(context, "category", None),
            "qt_file": getattr(context, "file", None),
            "qt_line": getattr(context, "line", None),
            "qt_function": getattr(context, "function", None),
        }
        log(
            "qt.message type=%s category=%s location=%s:%s function=%s message=%s",
            level_name,
            details["qt_category"],
            details["qt_file"],
            details["qt_line"],
            details["qt_function"],
            message,
            extra={
                "event": "qt.fatal" if message_type == QtMsgType.QtFatalMsg else "qt.message",
                "error_code": "QT_FATAL" if message_type == QtMsgType.QtFatalMsg else None,
                "details": details,
            },
        )
        if message_type == QtMsgType.QtFatalMsg:
            self._write_fatal_report("Qt fatal message", str(message), details)
            self._flush_diagnostics()

    def _write_fatal_report(self, source: str, message: str, details: dict) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self.directory / f"crash_{timestamp}.log"
        lines = ["XL Crash Report", f"source: {source}", f"time: {timestamp}", f"message: {message}"]
        lines.extend(f"{key}: {value}" for key, value in details.items() if value is not None)
        try:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            logger.exception("crash.report_unavailable path=%s source=%s", path, source)

    @staticmethod
    def _flush_diagnostics() -> None:
        for handler in logger.handlers:
            try:
                handler.flush()
            except Exception:
                pass

    def handle(self, exc_type, exc_value, exc_traceback) -> Path:
        return self.write(exc_type, exc_value, exc_traceback, source="main")

    def handle_thread(self, args) -> Path:
        return self.write(args.exc_type, args.exc_value, args.exc_traceback, source=f"thread:{args.thread.name}")

    def handle_unraisable(self, args) -> Path:
        object_type = type(args.object).__name__ if args.object is not None else "unknown"
        return self.write(
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
            source=f"unraisable:{object_type}",
        )

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
        self._flush_diagnostics()
        return path


def install_crash_reporter(directory: str | os.PathLike[str]) -> CrashReporter:
    reporter = CrashReporter(directory)
    reporter.install()
    return reporter
