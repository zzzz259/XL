"""AS 导入完成后执行的图片资源预处理线程。"""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from app.platform.diagnostics import logger, set_task_outcome, task_operation


class PreviewPostprocessWorker(QThread):
    progress_value = Signal(int, int, str)
    detail_progress = Signal(int, int, str)
    finished_processing = Signal(object)
    cancelled_processing = Signal()
    error = Signal(str)

    def __init__(self, service, parent=None, *, import_result=None):
        super().__init__(parent)
        self.service = service
        self.import_result = import_result
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

    @task_operation("IMAGE_POSTPROCESS", "preview", lambda self: {"service": type(self.service).__name__})
    def run(self):
        try:
            summary = self.service.preprocess_preview_resources(
                progress_callback=self.progress_value.emit,
                cancel_check=lambda: self._cancelled,
                detail_progress_callback=self.detail_progress.emit,
                import_result=self.import_result,
            )
            if self._cancelled:
                set_task_outcome("cancelled", error_code="IMAGE_POSTPROCESS_CANCELLED", message="图片资源预处理已取消")
                self.cancelled_processing.emit()
            else:
                materials = summary.materials
                spine = summary.spine
                partial = bool(materials.failed or spine.diagnostics)
                partial = partial or (summary.portrait is not None and summary.portrait.status in {"partial", "skipped"})
                packages = getattr(summary, "packages", None)
                partial = partial or bool(packages and (packages.failed or packages.cancelled or packages.metadata_incomplete))
                set_task_outcome(
                    "partial" if partial else "success",
                    message="图片资源预处理部分完成" if partial else "图片资源预处理完成",
                    details={
                        "spine_published": spine.published,
                        "spine_copied_files": spine.copied_files,
                        "spine_skipped": spine.skipped,
                        "materials_exported": materials.exported,
                        "materials_failed": materials.failed,
                        "portrait_status": summary.portrait.status if summary.portrait else "not_requested",
                        "portrait_roots": summary.portrait.roots if summary.portrait else 0,
                        "portrait_complete": summary.portrait.complete if summary.portrait else 0,
                        "portrait_incomplete": summary.portrait.incomplete if summary.portrait else 0,
                        "portrait_cache_hits": summary.portrait.cache_hits if summary.portrait else 0,
                        "portrait_not_applicable": summary.portrait.not_applicable if summary.portrait else 0,
                        "portrait_packages_published": packages.published if packages else 0,
                        "portrait_packages_failed": packages.failed if packages else 0,
                        "portrait_packages_metadata_incomplete": packages.metadata_incomplete if packages else 0,
                    },
                )
                self.finished_processing.emit(summary)
        except Exception as error:
            logger.error("图片资源预处理失败: %s", error, exc_info=True)
            set_task_outcome("failed", error_code="IMAGE_POSTPROCESS_FAILED", message=str(error))
            self.error.emit(str(error))
        finally:
            with self._lock:
                self._active = False
