# -*- coding: utf-8 -*-
"""模块职责：图片预览视图构建器

创建图片预览视图容器（标题栏 + 工具栏 + 缩略图网格 + 状态栏 + 空状态提示），
返回 (container, controls_dict)，其中 controls_dict 提供外部操作所需的控件引用。
该函数不持有 MainWindow 引用之外的永久状态，信号通过 parent 回调连接到主窗口。
"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QProgressBar,
    QAbstractItemView, QListWidget, QComboBox,
)

from app.features.preview.drag_list import DragListWidget
from app.shared.qt.chrome import (
    create_action_button,
    create_command_bar,
    create_empty_state,
    create_page_header,
    create_status_label,
)


def create_preview_view(parent=None):
    """创建图片预览视图容器，返回 (container, controls_dict)。

    controls_dict 包含外部需要操作的控件：
      preview_title, preview_progress, image_list, empty_label,
      preview_status, btn_reload
    """
    container = QWidget()
    container.setObjectName("viewContainer")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # 顶部栏：标题 + 关闭按钮
    preview_title_text = "角色预览器 · 共 0 张图片"
    top_bar, preview_title, btn_close_preview = create_page_header(
        preview_title_text,
        "关闭预览",
        (lambda: parent._toggle_preview_mode(False)) if parent is not None else None,
        container,
    )
    layout.addWidget(top_bar)

    # 工具栏：重新加载 + 进度条
    ctrl_bar, ctrl_layout = create_command_bar(container)

    btn_reload = create_action_button(
        "重新加载图片",
        "secondary",
        parent._force_reload_preview if parent is not None else None,
        container,
    )
    ctrl_layout.addWidget(btn_reload)

    # 角色过滤下拉框（按角色分类显示）
    filter_label = QLabel("角色")
    filter_label.setAccessibleName("角色筛选")
    ctrl_layout.addWidget(filter_label)
    character_filter = QComboBox()
    character_filter.setMinimumWidth(140)
    character_filter.setToolTip("按角色筛选图片")
    character_filter.setAccessibleName("角色筛选")
    if parent is not None:
        character_filter.currentIndexChanged.connect(parent._on_character_filter_changed)
    ctrl_layout.addWidget(character_filter)

    preview_progress = QProgressBar()
    preview_progress.setObjectName("previewProgress")
    preview_progress.setFixedHeight(24)
    preview_progress.setFixedWidth(250)
    preview_progress.setVisible(False)
    ctrl_layout.addWidget(preview_progress)
    ctrl_layout.addStretch()
    layout.addWidget(ctrl_bar)

    # 图片列表（IconMode，自适应列数，支持多选和拖放）
    image_list = DragListWidget()
    image_list.setObjectName("previewImageList")
    image_list.setViewMode(QListWidget.IconMode)
    image_list.setIconSize(QSize(150, 150))
    image_list.setGridSize(QSize(180, 210))
    image_list.setResizeMode(QListWidget.Adjust)
    image_list.setMovement(QListWidget.Static)
    image_list.setSpacing(10)
    image_list.setContextMenuPolicy(Qt.CustomContextMenu)
    image_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
    image_list.setDragEnabled(True)
    if parent is not None:
        image_list.customContextMenuRequested.connect(parent._show_context_menu)
        image_list.itemClicked.connect(parent._on_item_clicked)
        image_list.itemDoubleClicked.connect(parent._on_item_double_clicked)
        image_list.itemSelectionChanged.connect(parent._on_preview_selection_changed)
    # 内容区：空状态叠加在图片列表中央，不再作为底部横条参与布局。
    content = QWidget(container)
    content.setObjectName("viewContent")
    content_layout = QGridLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(0)
    content_layout.addWidget(image_list, 0, 0)

    empty_label = create_empty_state("暂无图片，请先导出角色立绘", content)
    empty_label.setVisible(False)
    empty_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    content_layout.addWidget(empty_label, 0, 0)
    layout.addWidget(content, 1)

    # 底部状态
    preview_status = create_status_label("共 0 张图片", container)
    preview_status.setFixedHeight(28)
    layout.addWidget(preview_status)

    controls = {
        "preview_title": preview_title,
        "btn_close_preview": btn_close_preview,
        "preview_progress": preview_progress,
        "image_list": image_list,
        "empty_label": empty_label,
        "preview_status": preview_status,
        "btn_reload": btn_reload,
        "character_filter": character_filter,
    }
    return container, controls
