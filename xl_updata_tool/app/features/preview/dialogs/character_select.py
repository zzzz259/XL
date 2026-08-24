# -*- coding: utf-8 -*-
"""角色选择对话框：勾选要导出的角色立绘"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QCheckBox,
)
from PySide6.QtCore import Qt

class CharacterSelectDialog(QDialog):
    """列出角色，勾选要导出的（默认全选）。selected_roles() 返回勾选的角色名集合。"""

    def __init__(self, characters, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择要导出的角色")
        self.setObjectName("characterSelectDialog")
        self.resize(440, 560)
        self._characters = list(characters)
        self._checkboxes = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel(f"共 {len(self._characters)} 个角色，勾选要导出的（默认全选）")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        # 全选 / 全不选
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        for txt, checked, object_name in (("全选", True, "selectAllButton"), ("全不选", False, "clearAllButton")):
            b = QPushButton(txt)
            b.setObjectName(object_name)
            b.setFixedSize(72, 30)
            b.setAccessibleName(txt)
            b.clicked.connect(lambda _, c=checked: self._set_all(c))
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 滚动勾选列表
        scroll = QScrollArea()
        scroll.setObjectName("characterSelectScroll")
        scroll.setWidgetResizable(True)
        container = QWidget()
        container.setObjectName("characterSelectContainer")
        grid = QVBoxLayout(container)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setSpacing(3)
        for name in self._characters:
            cb = QCheckBox(name)
            cb.setObjectName("characterOption")
            cb.setChecked(True)
            grid.addWidget(cb)
            self._checkboxes.append(cb)
        if not self._characters:
            empty = QLabel("暂无可导出的角色")
            empty.setObjectName("dialogEmptyState")
            empty.setAlignment(Qt.AlignCenter)
            grid.addWidget(empty)
        grid.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # 确认 / 取消
        confirm_row = QHBoxLayout()
        confirm_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("dialogCancelButton")
        btn_cancel.setFixedSize(88, 34)
        btn_cancel.setAccessibleName("取消导出角色选择")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("导出选中")
        btn_ok.setObjectName("dialogPrimaryButton")
        btn_ok.setProperty("fluentAppearance", "primary")
        btn_ok.setFixedSize(100, 34)
        btn_ok.setAccessibleName("导出选中角色")
        btn_ok.clicked.connect(self.accept)
        confirm_row.addWidget(btn_cancel)
        confirm_row.addWidget(btn_ok)
        layout.addLayout(confirm_row)

    def _set_all(self, checked):
        for cb in self._checkboxes:
            cb.setChecked(checked)

    def selected_roles(self):
        return {self._characters[i] for i, cb in enumerate(self._checkboxes) if cb.isChecked()}
