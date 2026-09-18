"""AS 导入完成后执行的图片资源预处理线程。"""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from app.platform.diagnostics import logger


class PreviewPostprocessWorker(QThread):
    progress_value = Signal(int, int, str)
    detail_progress = Signal(int, int, str)
    finished_processing = Signal(object)
    cancelled_processing = Signal()
    error = Signal(str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._cancelled = False
        self._active = False
        self._lock = threading.Lock()

    def start(self, priority=None):
        with self._lock:
            if self._active:
                return False
            self._active = True
        try:
            if priority is None:
                super().start()
            else:
                super().start(priority)
        except Exception:
            with self._lock:
                self._active = False
            raise
        return True

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            summary = self.service.preprocess_preview_resources(
                progress_callback=self.progress_value.emit,
                cancel_check=lambda: self._cancelled,
                detail_progress_callback=self.detail_progress.emit,
            )
            if self._cancelled:
                self.cancelled_processing.emit()
            else:
                self.finished_processing.emit(summary)
        except Exception as error:
            logger.error("图片资源预处理失败: %s", error, exc_info=True)
            self.error.emit(str(error))
        finally:
            with self._lock:
                self._active = False
