# -*- coding: utf-8 -*-
"""模块职责：图片预览视图构建器

创建图片预览视图容器（标题栏 + 工具栏 + 缩略图网格 + 状态栏 + 空状态提示），
返回 (container, controls_dict)，其中 controls_dict 提供外部操作所需的控件引用。
该函数不持有 MainWindow 引用之外的永久状态，信号通过 parent 回调连接到主窗口。
"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame, QAbstractItemView, QListWidget,
)

from app.ui.theme import (
    BG_DARK, BG_SURFACE, BG_ELEVATED, BORDER,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, DANGER, SUCCESS, INFO,
)
from app.ui.widgets.drag_list import DragListWidget


def create_preview_view(parent=None):
    """创建图片预览视图容器，返回 (container, controls_dict)。

    controls_dict 包含外部需要操作的控件：
      preview_title, preview_progress, image_list, empty_label,
      preview_status, btn_reload
    """
    container = QWidget()
    container.setStyleSheet(f"background-color:{BG_DARK};")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # 顶部栏：标题 + 关闭按钮
    top_bar = QFrame()
    top_bar.setFixedHeight(50)
    top_bar.setStyleSheet(f"QFrame {{ background-color:{BG_SURFACE}; border-bottom:1px solid {BORDER}; }}")
    top_layout = QHBoxLayout(top_bar)
    top_layout.setContentsMargins(16, 0, 16, 0)

    preview_title = QLabel("🖼️ 角色预览器  共 0 张图片")
    preview_title.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:16px; font-weight:bold; background:transparent; border:none;")
    top_layout.addWidget(preview_title)
    top_layout.addStretch()

    btn_close_preview = QPushButton("✕ 关闭预览")
    btn_close_preview.setFixedSize(100, 32)
    btn_close_preview.setStyleSheet(f"""
        QPushButton {{ background-color:{DANGER}; border:none; border-radius:6px;
                      color:#fff; font-size:12px; font-weight:600; }}
        QPushButton:hover {{ opacity:0.85; }}
    """)
    if parent is not None:
        btn_close_preview.clicked.connect(lambda: parent._toggle_preview_mode(False))
    top_layout.addWidget(btn_close_preview)
    layout.addWidget(top_bar)

    # 工具栏：重新加载 + 进度条
    ctrl_bar = QFrame()
    ctrl_bar.setFixedHeight(50)
    ctrl_bar.setStyleSheet(f"QFrame {{ background-color:{BG_ELEVATED}; border-bottom:1px solid {BORDER}; }}")
    ctrl_layout = QHBoxLayout(ctrl_bar)
    ctrl_layout.setContentsMargins(16, 0, 16, 0)

    btn_reload = QPushButton("🔄 重新加载图片")
    btn_reload.setFixedSize(140, 32)
    btn_reload.setStyleSheet(f"""
        QPushButton {{ background-color:{INFO}; border:none; border-radius:6px;
                      color:#fff; font-size:12px; font-weight:600; }}
        QPushButton:hover {{ opacity:0.85; }}
    """)
    if parent is not None:
        btn_reload.clicked.connect(parent._force_reload_preview)
    ctrl_layout.addWidget(btn_reload)

    preview_progress = QProgressBar()
    preview_progress.setFixedHeight(24)
    preview_progress.setFixedWidth(250)
    preview_progress.setVisible(False)
    preview_progress.setStyleSheet(f"""
        QProgressBar {{ background-color:{BG_DARK}; border:none; border-radius:4px;
                       text-align:center; color:{TEXT_PRIMARY}; font-size:12px; }}
        QProgressBar::chunk {{ background-color:{SUCCESS}; border-radius:4px; }}
    """)
    ctrl_layout.addWidget(preview_progress)
    ctrl_layout.addStretch()
    layout.addWidget(ctrl_bar)

    # 图片列表（IconMode，自适应列数，支持多选和拖放）
    image_list = DragListWidget()
    image_list.setViewMode(QListWidget.IconMode)
    image_list.setIconSize(QSize(150, 150))
    image_list.setGridSize(QSize(180, 210))
    image_list.setResizeMode(QListWidget.Adjust)
    image_list.setMovement(QListWidget.Static)
    image_list.setSpacing(10)
    image_list.setContextMenuPolicy(Qt.CustomContextMenu)
    image_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
    image_list.setDragEnabled(True)
    image_list.setStyleSheet(f"""
        QListWidget {{ border:none; background-color:{BG_DARK}; padding:10px; }}
        QListWidget::item {{ border-radius:6px; }}
        QListWidget::item:hover {{ background-color:{BG_ELEVATED}; }}
        QListWidget::item:selected {{ background-color:{BG_ELEVATED}; }}
    """)
    if parent is not None:
        image_list.customContextMenuRequested.connect(parent._show_context_menu)
        image_list.itemClicked.connect(parent._on_item_clicked)
        image_list.itemDoubleClicked.connect(parent._on_item_double_clicked)
    layout.addWidget(image_list, 1)

    # 底部状态
    preview_status = QLabel("共 0 张图片")
    preview_status.setFixedHeight(28)
    preview_status.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; padding:4px 16px; background-color:{BG_SURFACE}; border-top:1px solid {BORDER};")
    layout.addWidget(preview_status)

    # 空状态提示
    empty_label = QLabel("暂无图片，请先导出角色立绘")
    empty_label.setAlignment(Qt.AlignCenter)
    empty_label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:18px; background:transparent; border:none;")
    empty_label.setVisible(False)
    layout.addWidget(empty_label)

    controls = {
        "preview_title": preview_title,
        "preview_progress": preview_progress,
        "image_list": image_list,
        "empty_label": empty_label,
        "preview_status": preview_status,
        "btn_reload": btn_reload,
    }
    return container, controls