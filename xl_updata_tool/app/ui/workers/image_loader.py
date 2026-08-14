# -*- coding: utf-8 -*-
"""异步加载图片缩略图的工作线程"""

import os

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QPixmap, Qt

from app.core.logger import logger
from app.ui.theme import THUMB_SIZE


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

        png_files = sorted([f for f in os.listdir(self.image_dir) if f.lower().endswith(".png")])
        total = len(png_files)
        if total == 0:
            self.finished_loading.emit([])
            return

        loaded_paths = []
        for i, fname in enumerate(png_files):
            if self._cancelled:
                break
            fpath = os.path.join(self.image_dir, fname)
            try:
                pixmap = QPixmap(fpath)
                if not pixmap.isNull():
                    thumb = self._create_thumbnail(pixmap, self.thumb_size)
                    self.image_loaded.emit(fpath, thumb)
                    loaded_paths.append(fpath)
            except Exception as e:
                logger.error(f"加载图片失败 {fname}: {e}")
            self.progress.emit(i + 1, total)

        self.finished_loading.emit(loaded_paths)

    def _create_thumbnail(self, pixmap, size):
        """缩放并居中裁剪为正方形缩略图"""
        scaled = pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (scaled.width() - size) // 2
        y = (scaled.height() - size) // 2
        return scaled.copy(x, y, size, size)