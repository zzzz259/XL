# -*- coding: utf-8 -*-
"""导出参数设置对话框模块"""

import os
from datetime import datetime

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QComboBox, QSpinBox, QFormLayout,
)
from PySide6.QtCore import Qt

from app.ui.theme import BG_DARK, BG_SURFACE, BG_ELEVATED, BORDER, TEXT_PRIMARY, TEXT_SECONDARY, ACCENT


class ExportSettingsDialog(QDialog):
    """导出参数设置对话框"""

    def __init__(self, skel_path, atlas_path, default_format="MP4", parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出设置")
        self.setMinimumWidth(420)
        self.setStyleSheet(f"""
            QDialog {{ background-color:{BG_SURFACE}; color:{TEXT_PRIMARY}; }}
            QLabel {{ color:{TEXT_PRIMARY}; font-size:13px; background:transparent; border:none; }}
            QLabel#fileLabel {{ color:{TEXT_SECONDARY}; font-size:11px; padding:6px;
                               background-color:{BG_DARK}; border:1px solid {BORDER}; border-radius:4px; }}
            QComboBox, QSpinBox {{
                background-color:{BG_DARK}; border:1px solid {BORDER}; border-radius:4px;
                padding:5px 8px; color:{TEXT_PRIMARY}; font-size:13px; min-height:24px;
            }}
            QComboBox::drop-down {{ border:none; width:20px; }}
            QCheckBox {{ color:{TEXT_PRIMARY}; font-size:13px; }}
            QPushButton {{ padding:8px 20px; border-radius:6px; border:none; color:#fff; font-size:13px; font-weight:600; }}
            QPushButton#okBtn {{ background-color:{ACCENT}; }}
            QPushButton#okBtn:hover {{ opacity:0.85; }}
            QPushButton#cancelBtn {{ background-color:{BG_ELEVATED}; border:1px solid {BORDER}; color:{TEXT_PRIMARY}; }}
            QPushButton#cancelBtn:hover {{ border-color:{ACCENT}; }}
        """)

        self.skel_path = skel_path
        self.atlas_path = atlas_path
        self._build_ui(default_format)

    def _build_ui(self, default_format):
        skel_base = os.path.splitext(os.path.basename(self.skel_path))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 标题
        title = QLabel("导出 Spine 动画")
        title.setStyleSheet(f"font-size:16px; font-weight:bold; color:{ACCENT};")
        layout.addWidget(title)

        # 表单
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)

        # 输出格式
        self.format_combo = QComboBox()
        self.format_combo.addItems(["MP4", "GIF"])
        self.format_combo.setCurrentText(default_format)
        self.format_combo.currentIndexChanged.connect(self._update_file_label)
        form.addRow("输出格式:", self.format_combo)

        # 动画名称
        self.anim_combo = QComboBox()
        self.anim_combo.addItems(["idle"])
        self.anim_combo.setCurrentText("idle")
        form.addRow("动画名称:", self.anim_combo)

        # 时长
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 10)
        self.duration_spin.setValue(2)
        self.duration_spin.setSuffix(" 秒")
        form.addRow("时长:", self.duration_spin)

        # 帧率
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(5, 60)
        self.fps_spin.setValue(15)
        self.fps_spin.setSuffix(" fps")
        form.addRow("帧率:", self.fps_spin)

        # 缩放
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(1, 8)
        self.scale_spin.setValue(2)
        self.scale_spin.setSuffix("x")
        form.addRow("缩放:", self.scale_spin)

        layout.addLayout(form)

        # 输出文件路径
        file_header = QLabel("输出文件:")
        file_header.setStyleSheet(f"font-weight:bold; color:{TEXT_SECONDARY};")
        layout.addWidget(file_header)

        self.file_label = QLabel()
        self.file_label.setObjectName("fileLabel")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        # 预乘 Alpha
        self.pma_checkbox = QCheckBox("启用预乘 Alpha (--pma)")
        self.pma_checkbox.setChecked(True)
        layout.addWidget(self.pma_checkbox)

        # 自动打开
        self.auto_open_cb = QCheckBox("导出完成后自动打开文件")
        self.auto_open_cb.setChecked(True)
        layout.addWidget(self.auto_open_cb)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("导出")
        ok_btn.setObjectName("okBtn")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)

        self._skel_base = skel_base
        self._timestamp = timestamp
        self._update_file_label()

    def _update_file_label(self):
        fmt = self.format_combo.currentText().lower()
        ext = ".mp4" if fmt == "mp4" else ".gif"
        fname = f"{self._skel_base}_{self._timestamp}{ext}"
        self.file_label.setText(fname)

    def get_settings(self):
        return {
            "format": self.format_combo.currentText().lower(),
            "animation": self.anim_combo.currentText(),
            "duration": self.duration_spin.value(),
            "fps": self.fps_spin.value(),
            "scale": self.scale_spin.value(),
            "pma": self.pma_checkbox.isChecked(),
            "auto_open": self.auto_open_cb.isChecked(),
            "file_label": self.file_label.text(),
        }