"""Audio Feature 的后台任务入口。

P1 先把 Worker 的归属和调用入口固定在功能域；具体 bank/debank 业务仍复用
迁移期实现，待后续拆成 Service/Adapter 后再移除这个兼容包装。
"""

from PySide6.QtCore import QThread, Signal

from app.ui.workers.audio_decrypt import AudioDecryptWorker as _LegacyAudioDecryptWorker


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


class AudioDecryptWorker(_LegacyAudioDecryptWorker):
    """功能域侧的音频解密 Worker 类型。"""


__all__ = ["AudioCatalogWorker", "AudioDecryptWorker"]
