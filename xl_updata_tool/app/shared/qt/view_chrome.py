"""共享页面壳层：页头、操作栏、状态提示和按钮语义。"""

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
)
from PySide6.QtCore import Qt


def create_page_header(title, close_text=None, close_callback=None, parent=None):
    """创建统一页头，返回 ``(frame, title_label, close_button)``。"""
    frame = QFrame(parent)
    frame.setObjectName("pageHeader")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(16, 0, 16, 0)
    layout.setSpacing(12)

    title_label = QLabel(title, frame)
    title_label.setObjectName("pageTitle")
    title_label.setProperty("heading", True)
    layout.addWidget(title_label)
    layout.addStretch()

    close_button = None
    if close_text:
        close_button = create_action_button(close_text, "subtle", close_callback, frame)
        close_button.setProperty("fluentAppearance", "danger")
        layout.addWidget(close_button)
    return frame, title_label, close_button


def create_command_bar(parent=None):
    """创建页面级操作栏，统一背景、间距和高度。"""
    frame = QFrame(parent)
    frame.setObjectName("pageCommandBar")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(16, 8, 16, 8)
    layout.setSpacing(8)
    return frame, layout


def create_action_button(text, appearance="secondary", callback=None, parent=None):
    """创建带语义外观和可访问信息的页面操作按钮。"""
    button = QPushButton(text, parent)
    button.setProperty("fluentAppearance", appearance)
    button.setMinimumHeight(32)
    button.setToolTip(text)
    button.setAccessibleName(text)
    if callback is not None:
        button.clicked.connect(callback)
    return button


def create_status_label(text="", parent=None):
    """创建页面底部状态文本，避免状态只依赖颜色表达。"""
    label = QLabel(text, parent)
    label.setObjectName("pageStatus")
    label.setAccessibleName("页面状态")
    return label


def create_empty_state(text, parent=None):
    """创建统一空状态文本。"""
    label = QLabel(text, parent)
    label.setObjectName("emptyState")
    label.setAlignment(Qt.AlignCenter)
    label.setAccessibleName(text)
    return label
