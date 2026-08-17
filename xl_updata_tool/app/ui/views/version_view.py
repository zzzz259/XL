"""版本列表视图构建器。"""

from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget

from app.ui.theme import get_color


def create_version_table(parent=None):
    """创建版本列表表格，并连接主窗口的交互回调。"""
    table = QTableWidget()
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
    table.setStyleSheet(f"""
        QTableWidget {{ background-color:{get_color('BG_DARK')}; border:none; gridline-color:{get_color('BORDER')}; }}
        QTableWidget::item {{ padding:14px 12px; font-size:14px; }}
        QHeaderView::section {{ background-color:{get_color('BG_SURFACE')}; padding:12px 14px;
            border:none; border-right:1px solid {get_color('BORDER')}; border-bottom:2px solid {get_color('BORDER')}; font-size:13px;
            font-weight:600; color:{get_color('TEXT_SECONDARY')}; }}
    """)
    if parent is not None:
        table.viewport().installEventFilter(parent)
        table.currentItemChanged.connect(parent._on_row_select)
        table.cellClicked.connect(parent._on_cell_clicked)
    return table
