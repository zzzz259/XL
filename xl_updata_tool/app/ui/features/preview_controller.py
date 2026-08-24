"""图片预览器的 Qt 项目构造逻辑。"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QListWidgetItem

from app.features.preview.catalog import find_skel_paths


def build_preview_item(image_path: str, thumbnail, skel_map: dict) -> QListWidgetItem:
    """将缩略图和匹配到的 Spine 路径封装为列表项。"""
    skel_path, atlas_path = find_skel_paths(image_path, skel_map)
    item = QListWidgetItem(QPixmap(thumbnail), "")
    item.setData(Qt.UserRole, {
        "png": image_path,
        "skel": skel_path,
        "atlas": atlas_path,
    })
    filename = os.path.basename(image_path)
    item.setText(filename if len(filename) <= 22 else filename[:19] + "...")
    item.setToolTip(filename)
    return item
