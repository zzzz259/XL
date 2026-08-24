"""导入功能域 Qt 控制器。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.shared.contracts import ImportResult

from .service import ImporterService
from .worker import ImportWorker


class ImportController(QObject):
    """协调导入 Worker，并将旧信号收口为稳定结果。"""

    progress_stage = Signal(str, int, int)
    stage_finished = Signal(str)
    category_finished = Signal(str)
    all_finished = Signal(bool, str)
    result_ready = Signal(object)

    def __init__(self, service: ImporterService, parent=None):
        super().__init__(parent)
        self.service = service
        self.worker = None
        self.last_result: ImportResult | None = None
        self._completed_categories: set[str] = set()

    def start(self, bundle_paths, bundle_dir, as_cli, **kwargs):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        categories = self.service.categories(kwargs.get("export_categories"))
        self._completed_categories = set()
        self.last_result = None
        self.worker = ImportWorker(
            bundle_paths,
            bundle_dir,
            str(self.service.material_dir),
            as_cli,
            self,
            **kwargs,
        )
        self.worker.progress_stage.connect(self.progress_stage)
        self.worker.stage_finished.connect(self.stage_finished)
        self.worker.category_finished.connect(self._on_category_finished)
        self.worker.all_finished.connect(self._on_all_finished)
        self._requested_categories = categories
        self.worker.start()
        return self.worker

    def cancel(self):
        if self.worker is not None:
            self.worker.cancel()

    def wait(self, timeout=2000):
        return self.worker.wait(timeout) if self.worker is not None else True

    def _on_category_finished(self, label: str):
        category = str(label).removeprefix("导出 ")
        if category in self._requested_categories:
            self._completed_categories.add(category)
        self.category_finished.emit(label)

    def _on_all_finished(self, success: bool, message: str):
        requested = self._requested_categories
        if requested:
            completed = frozenset(self._completed_categories)
            failed = requested - completed if not success else frozenset()
        else:
            completed = frozenset({"all"}) if success else frozenset()
            failed = frozenset({"all"}) if not success else frozenset()
        cancelled = str(message) == "已取消"
        outputs = []
        lua_result = getattr(self.worker, "lua_export_result", None)
        if isinstance(lua_result, dict) and lua_result.get("directory"):
            outputs.append(lua_result["directory"])
        self.last_result = self.service.result(
            requested,
            completed,
            failed,
            outputs,
            cancelled=cancelled,
            message=message,
            lua_export_result=lua_result,
        )
        self.result_ready.emit(self.last_result)
        self.all_finished.emit(success, message)

