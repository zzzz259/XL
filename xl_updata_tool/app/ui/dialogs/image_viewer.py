# -*- coding: utf-8 -*-
"""大图预览对话框模块，使用 QGraphicsView 实现无滚动条的缩放/拖拽预览"""

import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGraphicsScene, QGraphicsView,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QPixmap, QPainter

from app.ui.theme import BG_DARK, BG_SURFACE, BG_ELEVATED, BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, ACCENT, DANGER


class ImageViewerDialog(QDialog):
    """大图预览窗口，使用 QGraphicsView 实现无滚动条的缩放/拖拽预览"""

    def __init__(self, image_paths, current_index=0, parent=None):
        super().__init__(parent)
        self.image_paths = image_paths
        self.current_index = current_index
        self.scale_factor = 1.0
        self.original_pixmap = QPixmap()

        self.setWindowTitle("图片预览")
        self.resize(900, 700)
        self.setStyleSheet(f"background-color:{BG_DARK};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部栏：文件名 + 关闭按钮
        top_bar = QFrame()
        top_bar.setFixedHeight(44)
        top_bar.setStyleSheet(f"QFrame {{ background-color:{BG_SURFACE}; border-bottom:1px solid {BORDER}; }}")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 0, 12, 0)

        self.fname_label = QLabel()
        self.fname_label.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:13px; font-weight:600; background:transparent;")
        top_layout.addWidget(self.fname_label)
        top_layout.addStretch()

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(32, 28)
        btn_close.setStyleSheet(f"""
            QPushButton {{ background-color:transparent; border:none; border-radius:4px;
                          color:{TEXT_SECONDARY}; font-size:16px; font-weight:600; }}
            QPushButton:hover {{ background-color:{DANGER}; color:#fff; }}
        """)
        btn_close.clicked.connect(self.close)
        top_layout.addWidget(btn_close)
        layout.addWidget(top_bar)

        # 图片区域：QGraphicsView + QGraphicsScene
        self.scene = QGraphicsScene(self)
        self.pixmap_item = self.scene.addPixmap(QPixmap())

        self.view = QGraphicsView(self.scene)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.view.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.view.setStyleSheet(f"QGraphicsView {{ border:none; background-color:{BG_DARK}; }}")
        self.view.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        layout.addWidget(self.view, 1)

        # 底部栏：上一张 / 信息 / 下一张
        bottom_bar = QFrame()
        bottom_bar.setFixedHeight(50)
        bottom_bar.setStyleSheet(f"QFrame {{ background-color:{BG_SURFACE}; border-top:1px solid {BORDER}; }}")
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(12, 0, 12, 0)

        self.btn_prev = QPushButton("◀ 上一张")
        self.btn_prev.setFixedSize(100, 32)
        self.btn_prev.setStyleSheet(f"""
            QPushButton {{ background-color:{BG_ELEVATED}; border:1px solid {BORDER}; border-radius:6px;
                          color:{TEXT_PRIMARY}; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ border-color:{ACCENT}; }}
            QPushButton:disabled {{ color:{TEXT_MUTED}; opacity:0.5; }}
        """)
        self.btn_prev.clicked.connect(self._prev_image)
        bottom_layout.addWidget(self.btn_prev)

        self.info_label = QLabel()
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; background:transparent;")
        bottom_layout.addWidget(self.info_label, 1)

        self.btn_next = QPushButton("下一张 ▶")
        self.btn_next.setFixedSize(100, 32)
        self.btn_next.setStyleSheet(f"""
            QPushButton {{ background-color:{BG_ELEVATED}; border:1px solid {BORDER}; border-radius:6px;
                          color:{TEXT_PRIMARY}; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ border-color:{ACCENT}; }}
            QPushButton:disabled {{ color:{TEXT_MUTED}; opacity:0.5; }}
        """)
        self.btn_next.clicked.connect(self._next_image)
        bottom_layout.addWidget(self.btn_next)
        layout.addWidget(bottom_bar)

        # 快捷键
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._esc_shortcut.activated.connect(self.close)

        self._load_current_image()

    def _load_current_image(self):
        if not self.image_paths:
            self.info_label.setText("0 / 0")
            return

        path = self.image_paths[self.current_index]
        self.original_pixmap = QPixmap(path)

        fname = os.path.basename(path)
        self.fname_label.setText(fname)
        self.setWindowTitle(f"图片预览 - {fname}")

        self.pixmap_item.setPixmap(self.original_pixmap)
        self.scene.setSceneRect(self.original_pixmap.rect())

        self._fit_to_view()
        self._update_nav_info()

    def _fit_to_view(self):
        """自适应缩放适应视口"""
        self.view.fitInView(self.pixmap_item, Qt.KeepAspectRatio)
        self.scale_factor = self.view.transform().m11()

    def _update_nav_info(self):
        total = len(self.image_paths)
        idx = self.current_index + 1
        if not self.original_pixmap.isNull():
            w = self.original_pixmap.width()
            h = self.original_pixmap.height()
            self.info_label.setText(f"{idx} / {total}  |  尺寸: {w} × {h}")
        else:
            self.info_label.setText(f"{idx} / {total}")

        self.btn_prev.setEnabled(self.current_index > 0)
        self.btn_next.setEnabled(self.current_index < total - 1)

    def _prev_image(self):
        if self.current_index > 0:
            self.current_index -= 1
            self._load_current_image()

    def _next_image(self):
        if self.current_index < len(self.image_paths) - 1:
            self.current_index += 1
            self._load_current_image()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1.0 / 1.15
        self.view.scale(factor, factor)
        self.scale_factor = self.view.transform().m11()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_Left:
            self._prev_image()
        elif event.key() == Qt.Key_Right:
            self._next_image()
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.original_pixmap.isNull():
            self._fit_to_view()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.original_pixmap.isNull():
            self._fit_to_view()