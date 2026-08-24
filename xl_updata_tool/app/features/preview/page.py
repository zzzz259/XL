"""Preview 页面：只构造控件并发布语义信号。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QSize, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QLabel,
    QListWidgetItem,
    QListWidget,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from app.shared.qt.chrome import (
    create_action_button,
    create_command_bar,
    create_empty_state,
    create_page_header,
    create_status_label,
)
from .drag_list import DragListWidget


class PreviewPage(QWidget):
    """图片预览页面，直接拥有控件并只发布页面级语义信号。"""

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_bar, self.preview_title, self.btn_close_preview = create_page_header(
            "角色预览器 · 共 0 张图片", "关闭预览", None, self
        )
        layout.addWidget(top_bar)

        command_bar, command_layout = create_command_bar(self)
        self.btn_reload = create_action_button("重新加载图片", "secondary", None, self)
        command_layout.addWidget(self.btn_reload)
        filter_label = QLabel("角色")
        filter_label.setAccessibleName("角色筛选")
        command_layout.addWidget(filter_label)
        self.character_filter = QComboBox()
        self.character_filter.setMinimumWidth(140)
        self.character_filter.setToolTip("按角色筛选图片")
        self.character_filter.setAccessibleName("角色筛选")
        command_layout.addWidget(self.character_filter)
        self.preview_progress = QProgressBar()
        self.preview_progress.setObjectName("previewProgress")
        self.preview_progress.setFixedHeight(24)
        self.preview_progress.setFixedWidth(250)
        self.preview_progress.setVisible(False)
        command_layout.addWidget(self.preview_progress)
        command_layout.addStretch()
        layout.addWidget(command_bar)

        content = QWidget(self)
        content.setObjectName("viewContent")
        content_layout = QGridLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.image_list = DragListWidget()
        self.image_list.setObjectName("previewImageList")
        self.image_list.setViewMode(QListWidget.IconMode)
        self.image_list.setIconSize(QSize(150, 150))
        self.image_list.setGridSize(QSize(180, 210))
        self.image_list.setResizeMode(QListWidget.Adjust)
        self.image_list.setMovement(QListWidget.Static)
        self.image_list.setSpacing(10)
        self.image_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.image_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.image_list.setDragEnabled(True)
        content_layout.addWidget(self.image_list, 0, 0)
        self.empty_label = create_empty_state("暂无图片，请先导出角色立绘", content)
        self.empty_label.setVisible(False)
        self.empty_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        content_layout.addWidget(self.empty_label, 0, 0)
        layout.addWidget(content, 1)

        self.preview_status = create_status_label("共 0 张图片", self)
        self.preview_status.setFixedHeight(28)
        layout.addWidget(self.preview_status)

        self.btn_close_preview.clicked.connect(self.close_requested)
        self.btn_reload.clicked.connect(self.reload_requested)
        self.character_filter.currentIndexChanged.connect(self.filter_changed)
        self.image_list.customContextMenuRequested.connect(self.context_menu_requested)
        self.image_list.itemClicked.connect(self.item_clicked)
        self.image_list.itemDoubleClicked.connect(self.item_double_clicked)
        self.image_list.itemSelectionChanged.connect(self.selection_changed)
