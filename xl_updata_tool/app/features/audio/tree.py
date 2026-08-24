"""音频功能域的 Qt 树构造与状态传播。"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTreeWidgetItem

from app.ui.theme import DANGER, TEXT_MUTED


def _set_unread_marker(item, unread):
    item.setText(5, "新" if unread else "")
    item.setForeground(5, QColor(DANGER if unread else TEXT_MUTED))
    if unread:
        item.setToolTip(5, "新导出或内容已变化")
    else:
        item.setToolTip(5, "")


def refresh_audio_tree_unread(table):
    """根据叶节点状态刷新整棵音频树的“新”标记。"""

    def update_item(item):
        info = item.data(0, Qt.UserRole)
        if info is not None:
            unread = bool(info.get("unread"))
        else:
            unread = False
            for index in range(item.childCount()):
                unread = update_item(item.child(index)) or unread
        _set_unread_marker(item, unread)
        return unread

    for index in range(table.topLevelItemCount()):
        update_item(table.topLevelItem(index))


def iter_audio_leaves(item):
    """返回目录节点下的所有音频叶节点。"""
    if item.data(0, Qt.UserRole) is not None:
        return [item]
    leaves = []
    for index in range(item.childCount()):
        leaves.extend(iter_audio_leaves(item.child(index)))
    return leaves


def refresh_audio_tree_checks(table):
    """根据叶节点状态刷新目录节点的全选/半选状态。"""

    def update_item(item):
        leaves = iter_audio_leaves(item)
        if item.data(0, Qt.UserRole) is not None:
            return item.checkState(0)
        checked = sum(leaf.checkState(0) == Qt.Checked for leaf in leaves)
        if not leaves or checked == 0:
            state = Qt.Unchecked
        elif checked == len(leaves):
            state = Qt.Checked
        else:
            state = Qt.PartiallyChecked
        item.setCheckState(0, state)
        for index in range(item.childCount()):
            update_item(item.child(index))
        return state

    for index in range(table.topLevelItemCount()):
        update_item(table.topLevelItem(index))


def populate_audio_tree(table, audio_files: list[dict], format_size) -> list[QTreeWidgetItem]:
    """将扫描后的音频元数据构造成 voice/album 树，返回所有叶节点。"""
    table.clear()
    file_items = []
    roots = {}
    mid_nodes = {}
    leaf_nodes = {}

    for info in audio_files:
        unread = bool(info.get("unread"))
        leaf = QTreeWidgetItem([
            os.path.basename(info["name"]),
            info["dir"],
            "-",
            info["ext"],
            format_size(info["size"]),
            "新" if unread else "",
        ])
        leaf.setData(0, Qt.UserRole, info)
        leaf.setFlags(leaf.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        leaf.setCheckState(0, Qt.Unchecked)
        file_items.append(leaf)

        parts = info["dir"].replace("\\", "/").split("/")
        top = parts[0] if parts and parts[0] else "其他"
        if top not in roots:
            roots[top] = QTreeWidgetItem([top])
            roots[top].setFlags(roots[top].flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            roots[top].setCheckState(0, Qt.Unchecked)
            table.addTopLevelItem(roots[top])

        if top == "voice" and len(parts) >= 2:
            character_id = parts[1]
            language = parts[2] if len(parts) > 2 else "?"
            mid_key = (top, character_id)
            if mid_key not in mid_nodes:
                mid_nodes[mid_key] = QTreeWidgetItem([character_id])
                mid_nodes[mid_key].setFlags(mid_nodes[mid_key].flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                mid_nodes[mid_key].setCheckState(0, Qt.Unchecked)
                roots[top].addChild(mid_nodes[mid_key])
            leaf_key = (top, character_id, language)
            if leaf_key not in leaf_nodes:
                leaf_nodes[leaf_key] = QTreeWidgetItem([language])
                leaf_nodes[leaf_key].setFlags(leaf_nodes[leaf_key].flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                leaf_nodes[leaf_key].setCheckState(0, Qt.Unchecked)
                mid_nodes[mid_key].addChild(leaf_nodes[leaf_key])
            leaf_nodes[leaf_key].addChild(leaf)
        else:
            album_name = parts[1] if len(parts) > 1 else info["dir"]
            mid_key = (top, album_name)
            if mid_key not in mid_nodes:
                mid_nodes[mid_key] = QTreeWidgetItem([album_name])
                mid_nodes[mid_key].setFlags(mid_nodes[mid_key].flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                mid_nodes[mid_key].setCheckState(0, Qt.Unchecked)
                roots[top].addChild(mid_nodes[mid_key])
            mid_nodes[mid_key].addChild(leaf)

    refresh_audio_tree_unread(table)
    refresh_audio_tree_checks(table)
    return file_items
