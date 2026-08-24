# -*- coding: utf-8 -*-
"""自定义控件：支持拖拽文件到桌面的 QListWidget 子类"""

import os
from PySide6.QtWidgets import QListWidget
from PySide6.QtCore import Qt, QMimeData, QUrl


class DragListWidget(QListWidget):
    """支持拖拽文件到桌面的 QListWidget 子类"""

    def __init__(self, parent=None):
        super().__init__(parent)

    def mimeData(self, items):
        """重写 mimeData，将选中的文件路径作为 urls 传递"""
        urls = []
        for item in items:
            data = item.data(Qt.UserRole)
            if data and data.get("png"):
                file_path = data["png"]
                if os.path.exists(file_path):
                    urls.append(QUrl.fromLocalFile(os.path.abspath(file_path)))
        mime = QMimeData()
        mime.setUrls(urls)
        return mime