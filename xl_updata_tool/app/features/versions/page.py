"""版本功能域页面。"""

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QVBoxLayout, QWidget

from app.ui.views.version_view import create_version_header


class VersionPage(QWidget):
    """版本工作区；控件归页面所有，行为由 VersionController 协调。"""

    cell_clicked = Signal(int, int)
    row_selected = Signal(object, object)
    hover_row_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("viewContainer")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.version_header, self.version_summary = create_version_header(self)
        layout.addWidget(self.version_header)

        self.table = QTableWidget(self)
        self.table.setObjectName("workspaceTable")
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["", "版本", "状态", "Bundle数", "备注", "", "", ""])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        header.setSectionResizeMode(7, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 140)
        self.table.setColumnWidth(5, 116)
        self.table.setColumnWidth(6, 116)
        self.table.setColumnWidth(7, 116)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        self.table.cellClicked.connect(self.cell_clicked.emit)
        self.table.currentItemChanged.connect(self.row_selected.emit)
        self.table.viewport().installEventFilter(self)
        layout.addWidget(self.table, 1)

    def set_visible(self, visible: bool) -> None:
        self.setVisible(visible)
        self.version_header.setVisible(visible)
        self.table.setVisible(visible)

    def eventFilter(self, obj, event):
        if obj is self.table.viewport():
            if event.type() == QEvent.MouseMove:
                index = self.table.indexAt(event.position().toPoint())
                self.hover_row_changed.emit(index.row() if index.isValid() else -1)
            elif event.type() == QEvent.Leave:
                self.hover_row_changed.emit(-1)
        return super().eventFilter(obj, event)
