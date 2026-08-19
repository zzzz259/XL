"""版本工作区视图构建器。"""

from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

def create_version_header(parent=None):
    """创建版本工作区的说明和统计摘要，返回 ``(frame, summary_label)``。"""
    frame = QFrame(parent)
    frame.setObjectName("workspaceHeader")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(16, 12, 16, 10)
    layout.setSpacing(12)

    text_box = QWidget(frame)
    text_layout = QVBoxLayout(text_box)
    text_layout.setContentsMargins(0, 0, 0, 0)
    text_layout.setSpacing(2)
    title = QLabel("版本工作区", text_box)
    title.setObjectName("workspaceTitle")
    title.setAccessibleName("版本工作区")
    text_layout.addWidget(title)
    description = QLabel("管理版本、下载状态与增量关系", text_box)
    description.setObjectName("workspaceDescription")
    text_layout.addWidget(description)
    layout.addWidget(text_box)
    layout.addStretch()

    summary = QLabel("0 个版本 · 尚未选择", frame)
    summary.setObjectName("workspaceSummary")
    summary.setAccessibleName("版本工作区统计")
    layout.addWidget(summary)
    return frame, summary


def create_version_table(parent=None):
    """创建版本列表表格，并连接主窗口的交互回调。"""
    table = QTableWidget()
    table.setObjectName("workspaceTable")
    table.setColumnCount(8)
    table.setHorizontalHeaderLabels(["", "版本", "状态", "Bundle数", "备注", "", "", ""])
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.Fixed)
    header.setSectionResizeMode(1, QHeaderView.Interactive)
    header.setSectionResizeMode(2, QHeaderView.Interactive)
    header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
    header.setSectionResizeMode(4, QHeaderView.Stretch)
    header.setSectionResizeMode(5, QHeaderView.Fixed)
    header.setSectionResizeMode(6, QHeaderView.Fixed)
    header.setSectionResizeMode(7, QHeaderView.Fixed)
    table.setColumnWidth(0, 60)
    table.setColumnWidth(1, 180)
    table.setColumnWidth(2, 140)
    table.setColumnWidth(5, 116)
    table.setColumnWidth(6, 116)
    table.setColumnWidth(7, 116)
    table.setAlternatingRowColors(True)
    table.setSelectionMode(QAbstractItemView.NoSelection)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(True)
    table.verticalHeader().setDefaultSectionSize(52)
    table.setMouseTracking(True)
    table.viewport().setMouseTracking(True)
    if parent is not None:
        table.viewport().installEventFilter(parent)
        table.currentItemChanged.connect(parent._on_row_select)
        table.cellClicked.connect(parent._on_cell_clicked)
    return table
