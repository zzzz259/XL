"""Preview 页面：只构造控件并发布语义信号。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Signal
from PySide6.QtWidgets import QListWidgetItem, QWidget, QVBoxLayout

from app.ui.views.preview_view import create_preview_view


class PreviewPage(QWidget):
    """图片预览页面，保留旧页面布局但移除对 MainWindow 私有回调的依赖。"""

    close_requested = Signal()
    reload_requested = Signal()
    filter_changed = Signal()
    context_menu_requested = Signal(QPoint)
    item_clicked = Signal(QListWidgetItem)
    item_double_clicked = Signal(QListWidgetItem)
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewPage")
        self.container, self.controls = create_preview_view()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.container)

        self.preview_title = self.controls["preview_title"]
        self.btn_close_preview = self.controls["btn_close_preview"]
        self.preview_progress = self.controls["preview_progress"]
        self.image_list = self.controls["image_list"]
        self.empty_label = self.controls["empty_label"]
        self.preview_status = self.controls["preview_status"]
        self.btn_reload = self.controls["btn_reload"]
        self.character_filter = self.controls["character_filter"]

        self.btn_close_preview.clicked.connect(self.close_requested)
        self.btn_reload.clicked.connect(self.reload_requested)
        self.character_filter.currentIndexChanged.connect(self.filter_changed)
        self.image_list.customContextMenuRequested.connect(self.context_menu_requested)
        self.image_list.itemClicked.connect(self.item_clicked)
        self.image_list.itemDoubleClicked.connect(self.item_double_clicked)
        self.image_list.itemSelectionChanged.connect(self.selection_changed)
