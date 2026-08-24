"""音频功能域的 Qt 树构造与状态传播。"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTreeWidgetItem

from app.shared.qt.tokens import DANGER, TEXT_MUTED


DIRECTORY_ROLE = Qt.UserRole + 1
PLACEHOLDER_ROLE = Qt.UserRole + 2
LOADED_ROLE = Qt.UserRole + 3


def _set_unread_marker(item, unread):
    item.setText(5, "新" if unread else "")
    item.setForeground(5, QColor(DANGER if unread else TEXT_MUTED))
    if unread:
        item.setToolTip(5, "新导出或内容已变化")
    else:
        item.setToolTip(5, "")


def refresh_audio_tree_unread(table, catalog_index=None):
    """根据叶节点状态刷新整棵音频树的“新”标记。"""

    def update_item(item):
        info = item.data(0, Qt.UserRole)
        if info is not None:
            unread = bool(info.get("unread"))
        elif catalog_index is not None and directory_path(item) is not None:
            unread = any(
                bool(info.get("unread"))
                for info in catalog_index.files_under(directory_path(item))
            )
        else:
            unread = False
            for child_index in range(item.childCount()):
                unread = update_item(item.child(child_index)) or unread
        _set_unread_marker(item, unread)
        return unread

    for root_index in range(table.topLevelItemCount()):
        update_item(table.topLevelItem(root_index))


def iter_audio_leaves(item):
    """返回目录节点下的所有音频叶节点。"""
    if item.data(0, Qt.UserRole) is not None:
        return [item]
    leaves = []
    for index in range(item.childCount()):
        leaves.extend(iter_audio_leaves(item.child(index)))
    return leaves


def directory_path(item) -> str | None:
    """返回目录节点路径；音频叶节点返回 ``None``。"""
    value = item.data(0, DIRECTORY_ROLE)
    return str(value) if value else None


def is_placeholder(item) -> bool:
    return bool(item.data(0, PLACEHOLDER_ROLE))


def _configure_directory_item(item, directory: str, index) -> None:
    item.setData(0, DIRECTORY_ROLE, directory)
    item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
    item.setCheckState(0, Qt.Unchecked)
    if index.has_children(directory):
        placeholder = QTreeWidgetItem([""])
        placeholder.setData(0, PLACEHOLDER_ROLE, True)
        item.addChild(placeholder)


def _make_audio_leaf(info: dict, format_size) -> QTreeWidgetItem:
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
    return leaf


def populate_audio_tree_roots(table, index, format_size) -> list[QTreeWidgetItem]:
    """只构造首层目录，后续节点由 ``populate_audio_directory`` 懒加载。"""
    table.clear()
    roots = []
    for directory in index.root_directories():
        item = QTreeWidgetItem([os.path.basename(directory)])
        _configure_directory_item(item, directory, index)
        table.addTopLevelItem(item)
        roots.append(item)
    refresh_audio_tree_unread(table, index)
    return roots


def populate_audio_directory(item, index, format_size) -> list[QTreeWidgetItem]:
    """构造一个已展开目录的直接子目录和直接音频文件。"""
    directory = directory_path(item)
    if directory is None:
        return []
    item.takeChildren()
    item.setData(0, LOADED_ROLE, True)
    children = []
    for child_directory in index.child_directories(directory):
        child = QTreeWidgetItem([os.path.basename(child_directory)])
        _configure_directory_item(child, child_directory, index)
        item.addChild(child)
        children.append(child)
    for info in index.files_in_directory(directory):
        leaf = _make_audio_leaf(info, format_size)
        item.addChild(leaf)
        children.append(leaf)
    return children


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
        leaf = _make_audio_leaf(info, format_size)
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
