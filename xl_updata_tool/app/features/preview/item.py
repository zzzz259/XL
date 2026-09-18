"""图片预览列表项构造。"""

from __future__ import annotations

import os
from collections.abc import Mapping

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QListWidgetItem

from .catalog import find_skel_paths


def build_preview_item(
    image_path: str,
    thumbnail,
    skel_map: dict,
    resource: Mapping | object | None = None,
    *,
    is_new: bool | None = None,
    fingerprint: str | None = None,
) -> QListWidgetItem:
    """将缩略图和匹配到的 Spine 路径封装为列表项。"""
    skel_path, atlas_path = find_skel_paths(image_path, skel_map)
    item = QListWidgetItem(QPixmap(thumbnail), "")
    data = {"png": image_path, "skel": skel_path, "atlas": atlas_path}
    if isinstance(resource, Mapping):
        for key in ("role_id", "skin_key", "fingerprint", "is_new", "display_name", "status"):
            if key in resource:
                data[key] = resource[key]
    elif resource is not None:
        for key in ("role_id", "skin_key", "display_name", "status"):
            value = getattr(resource, key, None)
            if value is not None:
                data[key] = value
        resource_fingerprint = getattr(resource, "fingerprint", None)
        if resource_fingerprint is not None:
            data["fingerprint"] = resource_fingerprint
        resource_is_new = getattr(resource, "is_new", None)
        if resource_is_new is not None:
            data["is_new"] = resource_is_new
    if fingerprint is not None:
        data["fingerprint"] = fingerprint
    if is_new is not None:
        data["is_new"] = is_new
    item.setData(Qt.UserRole, data)
    filename = os.path.basename(image_path)
    item.setText(filename if len(filename) <= 22 else filename[:19] + "...")
    item.setToolTip(filename)
    return item
