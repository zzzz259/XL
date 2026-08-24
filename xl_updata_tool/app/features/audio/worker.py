"""Audio Feature 的 Worker 兼容入口。

P1 先把 Worker 的归属和调用入口固定在功能域；具体 bank/debank 业务仍复用
迁移期实现，待后续拆成 Service/Adapter 后再移除这个兼容包装。
"""

from app.ui.workers.audio_decrypt import AudioDecryptWorker as _LegacyAudioDecryptWorker


class AudioDecryptWorker(_LegacyAudioDecryptWorker):
    """功能域侧的音频解密 Worker 类型。"""


__all__ = ["AudioDecryptWorker"]
