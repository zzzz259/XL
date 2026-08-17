"""音频管理器的 Qt 树构造逻辑。"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem


def populate_audio_tree(table, audio_files: list[dict], format_size) -> list[QTreeWidgetItem]:
    """将扫描后的音频元数据构造成 voice/album 树，返回所有叶节点。"""
    table.clear()
    file_items = []
    roots = {}
    mid_nodes = {}
    leaf_nodes = {}

    for info in audio_files:
        leaf = QTreeWidgetItem([
            os.path.basename(info["name"]),
            info["dir"],
            "-",
            info["ext"],
            format_size(info["size"]),
        ])
        leaf.setData(0, Qt.UserRole, info)
        leaf.setCheckState(0, Qt.Unchecked)
        file_items.append(leaf)

        parts = info["dir"].replace("\\", "/").split("/")
        top = parts[0] if parts and parts[0] else "其他"
        if top not in roots:
            roots[top] = QTreeWidgetItem([top])
            table.addTopLevelItem(roots[top])

        if top == "voice" and len(parts) >= 2:
            character_id = parts[1]
            language = parts[2] if len(parts) > 2 else "?"
            mid_key = (top, character_id)
            if mid_key not in mid_nodes:
                mid_nodes[mid_key] = QTreeWidgetItem([character_id])
                roots[top].addChild(mid_nodes[mid_key])
            leaf_key = (top, character_id, language)
            if leaf_key not in leaf_nodes:
                leaf_nodes[leaf_key] = QTreeWidgetItem([language])
                mid_nodes[mid_key].addChild(leaf_nodes[leaf_key])
            leaf_nodes[leaf_key].addChild(leaf)
        else:
            album_name = parts[1] if len(parts) > 1 else info["dir"]
            mid_key = (top, album_name)
            if mid_key not in mid_nodes:
                mid_nodes[mid_key] = QTreeWidgetItem([album_name])
                roots[top].addChild(mid_nodes[mid_key])
            mid_nodes[mid_key].addChild(leaf)

    return file_items
