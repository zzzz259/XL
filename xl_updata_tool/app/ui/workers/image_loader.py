# -*- coding: utf-8 -*-
"""异步加载图片缩略图的工作线程"""

import os

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QPixmap, QPainter, Qt

from app.core.logger import logger


class ImageLoadWorker(QThread):
    """异步加载图片缩略图的工作线程"""
    progress = Signal(int, int)           # current, total
    image_loaded = Signal(str, object)    # path, QPixmap
    finished_loading = Signal(list)       # list of paths

    def __init__(self, image_dir, thumb_size=150):
        super().__init__()
        self.image_dir = image_dir
        self.thumb_size = thumb_size
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if not os.path.isdir(self.image_dir):
            self.finished_loading.emit([])
            return

        # 递归扫描（角色图现在按编号分子目录）
        png_files = []
        for root, _dirs, files in os.walk(self.image_dir):
            for f in files:
                if f.lower().endswith(".png"):
                    png_files.append(os.path.join(root, f))
        png_files.sort()
        total = len(png_files)
        if total == 0:
            logger.info(f"图片目录无 PNG: {self.image_dir}")
            self.finished_loading.emit([])
            return
        logger.info(f"加载图片缩略图：{self.image_dir} 共 {total} 张")

        loaded_paths = []
        for i, fpath in enumerate(png_files):
            if self._cancelled:
                break
            fname = os.path.basename(fpath)
            try:
                pixmap = QPixmap(fpath)
                if not pixmap.isNull():
                    thumb = self._create_thumbnail(pixmap, self.thumb_size)
                    self.image_loaded.emit(fpath, thumb)
                    loaded_paths.append(fpath)
            except Exception as e:
                logger.error(f"加载图片失败 {fname}: {e}")
            self.progress.emit(i + 1, total)
            # 每 20 张让主线程喘息（分批限流，避免一次性插入几千缩略图卡顿）
            if (i + 1) % 20 == 0:
                self.msleep(10)

        logger.info(f"加载图片完成：{len(loaded_paths)}/{total} 张")
        self.finished_loading.emit(loaded_paths)

    def _create_thumbnail(self, pixmap, size):
        """缩放为缩略图（保持宽高比，不裁剪，居中放在透明画布上）"""
        scaled = pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        result = QPixmap(size, size)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        x = (size - scaled.width()) // 2
        y = (size - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        return result