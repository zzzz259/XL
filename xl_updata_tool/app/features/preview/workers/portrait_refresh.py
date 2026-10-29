"""Cancellable exact-version scene rebuild, separate from directory reload."""
from PySide6.QtCore import QThread, Signal

from app.platform.diagnostics import logger, set_task_outcome, task_operation


class PortraitRefreshWorker(QThread):
    progress = Signal(object)
    result_ready = Signal(object)
    error = Signal(str)

    def __init__(self, service, scenes, parent=None):
        super().__init__(parent)
        self.service = service
        self.scenes = tuple(scenes)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @task_operation('PORTRAIT_REFRESH', 'preview')
    def run(self):
        try:
            result = self.service.refresh_portrait_scenes(
                self.scenes, cancel_check=lambda: self._cancelled,
                progress_callback=self.progress.emit,
            )
            outcome = 'cancelled' if result.cancelled else 'partial' if result.failed else 'success'
            set_task_outcome(outcome, details={'published': result.published, 'failed': result.failed})
            self.result_ready.emit(result)
        except Exception as error:
            logger.exception('场景工程更新失败')
            set_task_outcome('failed', error_code='PORTRAIT_REFRESH_FAILED', message=str(error))
            self.error.emit(str(error))
