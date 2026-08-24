"""音频解密 Worker 的迁移期兼容入口。

正式生产路径使用 ``app.features.audio.worker.AudioDecryptWorker``。保留这个
模块是为了兼容旧测试、脚本和外部调用者；实现只有一份，位于 Audio Feature。
"""

from app.features.audio.processing import AudioDecryptProcessor
from app.features.audio.worker import AudioDecryptWorker

__all__ = ["AudioDecryptProcessor", "AudioDecryptWorker"]
