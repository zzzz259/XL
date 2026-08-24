"""版本功能域 Worker 入口，保留现有下载实现的兼容信号。"""

from app.ui.workers.download import CheckUpdateThread, DownloadWorker

__all__ = ["CheckUpdateThread", "DownloadWorker"]
