"""AssetStudio 导入 Worker 的迁移期兼容入口。

正式生产路径使用 ``app.features.importer.worker.ImportWorker``；本模块只
保留旧类名，避免旧脚本和测试在迁移期间失效，不复制导入实现。
"""

from app.features.importer.processing import ImportProcessor
from app.features.importer.worker import ImportWorker


class ImportASWorker(ImportWorker):
    """旧类名兼容门面。"""


__all__ = ["ImportASWorker", "ImportProcessor", "ImportWorker"]
