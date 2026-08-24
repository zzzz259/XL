"""Importer Feature 的 Qt Worker。

Worker 只负责线程生命周期、取消、进度和结果信号；AssetStudio 导入处理
由 Qt-free ``ImportProcessor`` 执行。
"""

from PySide6.QtCore import QThread, Signal

from app.features.importer.processing import ImportProcessor


class ImportWorker(QThread):
    """将 ImportProcessor 映射到 ImportController 的现有信号契约。"""

    progress_stage = Signal(str, int, int)
    stage_finished = Signal(str)
    category_finished = Signal(str)
    all_finished = Signal(bool, str)

    def __init__(self, bundle_paths, bundle_dir, material_dir, as_cli, parent=None,
                 export_types=None, export_categories=None, version_timestamp=None,
                 lua_output_dir=None, isolate_bundle_dir=False):
        super().__init__(parent)
        self.processor = ImportProcessor(
            bundle_paths,
            bundle_dir,
            material_dir,
            as_cli,
            export_types=export_types,
            export_categories=export_categories,
            version_timestamp=version_timestamp,
            lua_output_dir=lua_output_dir,
            isolate_bundle_dir=isolate_bundle_dir,
            progress_stage_callback=self.progress_stage.emit,
            stage_finished_callback=self.stage_finished.emit,
            category_finished_callback=self.category_finished.emit,
            all_finished_callback=self.all_finished.emit,
            cancel_check=lambda: self.isInterruptionRequested(),
        )

    @property
    def lua_export_result(self):
        return self.processor.lua_export_result

    def cancel(self):
        self.requestInterruption()
        self.processor.cancel()

    def run(self):
        self.processor.process()


__all__ = ["ImportWorker"]
