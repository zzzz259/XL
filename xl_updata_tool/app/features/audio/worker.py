"""Audio Feature 的后台任务入口。

Worker 只负责 QThread 生命周期、取消和信号映射；文件处理、分类、缓存和
debank 调用全部由 Qt-free ``AudioDecryptProcessor`` 执行。
"""

from PySide6.QtCore import QThread, Signal

from app.features.audio.processing import AudioDecryptProcessor


class AudioCatalogWorker(QThread):
    """在后台扫描音频目录并建立 Qt-free 索引。"""

    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, service, force: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.force = force

    def run(self) -> None:
        try:
            self.loaded.emit(self.service.load_catalog(force=self.force))
        except Exception as error:  # pragma: no cover - filesystem-specific
            self.failed.emit(str(error))


class AudioDecryptWorker(QThread):
    """将 AudioDecryptProcessor 映射到现有 AudioController 信号契约。"""

    progress = Signal(str)
    progress_value = Signal(int, int, str)
    finished_decrypt = Signal()
    cancelled_decrypt = Signal()
    error = Signal(str)

    def __init__(self, material_dir, audio_output_dir, debank_dir, force=False,
                 lua_output_dir=None, parent=None):
        super().__init__(parent)
        self.processor = AudioDecryptProcessor(
            material_dir,
            audio_output_dir,
            debank_dir,
            force=force,
            lua_output_dir=lua_output_dir,
            progress_callback=self.progress.emit,
            progress_value_callback=self.progress_value.emit,
            cancel_check=lambda: self.isInterruptionRequested(),
        )

    def cancel(self):
        self.requestInterruption()
        self.processor.cancel()

    def run(self):
        try:
            result = self.processor.process()
        except Exception as error:
            self.error.emit(str(error))
            return
        if result and result.get("cancelled"):
            self.cancelled_decrypt.emit()
        else:
            self.finished_decrypt.emit()


__all__ = ["AudioCatalogWorker", "AudioDecryptWorker"]
