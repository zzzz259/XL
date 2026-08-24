"""Importer Feature 的 Qt Worker 兼容入口。

具体的 AssetStudio 进程执行暂时仍由旧 Worker 承担；控制器和结果契约已经
先迁出，后续可在不改 MainWindow 的情况下替换这里的实现。
"""

from app.ui.workers.import_as import ImportASWorker


class ImportWorker(ImportASWorker):
    """导入线程的功能域入口，保留旧构造参数和信号以兼容现有调用方。"""


__all__ = ["ImportWorker"]
