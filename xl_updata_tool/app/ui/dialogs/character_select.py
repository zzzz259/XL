# -*- coding: utf-8 -*-
"""角色选择对话框：勾选要导出的角色立绘"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QCheckBox,
)

from app.ui.theme import BG_DARK, BG_SURFACE, TEXT_PRIMARY, TEXT_SECONDARY, ACCENT, BORDER


class CharacterSelectDialog(QDialog):
    """列出角色，勾选要导出的（默认全选）。selected_roles() 返回勾选的角色名集合。"""

    def __init__(self, characters, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择要导出的角色")
        self.resize(440, 560)
        self.setStyleSheet(f"QDialog {{ background-color:{BG_DARK}; }}")
        self._characters = list(characters)
        self._checkboxes = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel(f"共 {len(self._characters)} 个角色，勾选要导出的（默认全选）")
        title.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:14px; font-weight:600; background:transparent;")
        layout.addWidget(title)

        # 全选 / 全不选
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        for txt, checked in (("全选", True), ("全不选", False)):
            b = QPushButton(txt)
            b.setFixedSize(72, 30)
            b.setStyleSheet(f"""
                QPushButton {{ background-color:{BG_SURFACE}; border:1px solid {BORDER};
                              border-radius:6px; color:{TEXT_PRIMARY}; font-size:12px; }}
                QPushButton:hover {{ border-color:{ACCENT}; }}
            """)
            b.clicked.connect(lambda _, c=checked: self._set_all(c))
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 滚动勾选列表
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border:1px solid {BORDER}; border-radius:8px; background:{BG_SURFACE}; }}")
        container = QWidget()
        container.setStyleSheet(f"background:{BG_SURFACE};")
        grid = QVBoxLayout(container)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setSpacing(3)
        for name in self._characters:
            cb = QCheckBox(name)
            cb.setChecked(True)
            cb.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:13px; spacing:6px;")
            grid.addWidget(cb)
            self._checkboxes.append(cb)
        grid.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # 确认 / 取消
        confirm_row = QHBoxLayout()
        confirm_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedSize(88, 34)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{ background-color:{BG_SURFACE}; border:1px solid {BORDER};
                          border-radius:6px; color:{TEXT_SECONDARY}; font-size:13px; }}
            QPushButton:hover {{ border-color:{ACCENT}; }}
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("导出选中")
        btn_ok.setFixedSize(100, 34)
        btn_ok.setStyleSheet(f"""
            QPushButton {{ background-color:{ACCENT}; border:none; border-radius:6px;
                          color:#fff; font-size:13px; font-weight:600; }}
            QPushButton:hover {{ opacity:0.85; }}
        """)
        btn_ok.clicked.connect(self.accept)
        confirm_row.addWidget(btn_cancel)
        confirm_row.addWidget(btn_ok)
        layout.addLayout(confirm_row)

    def _set_all(self, checked):
        for cb in self._checkboxes:
            cb.setChecked(checked)

    def selected_roles(self):
        return {self._characters[i] for i, cb in enumerate(self._checkboxes) if cb.isChecked()}
