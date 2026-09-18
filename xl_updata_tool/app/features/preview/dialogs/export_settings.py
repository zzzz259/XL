# -*- coding: utf-8 -*-
"""导出参数设置对话框模块"""

import os
from datetime import datetime

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QComboBox, QSpinBox, QFormLayout,
)
from PySide6.QtCore import Qt

from ..export_plan import ExportSettings

class ExportSettingsDialog(QDialog):
    """导出参数设置对话框"""

    Rejected = QDialog.DialogCode.Rejected

    def __init__(self, skel_path, atlas_path, default_format="MP4", parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出设置")
        self.setObjectName("exportSettingsDialog")
        self.setMinimumWidth(420)

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
        title = QLabel("导出 Spine 资源")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        # 表单
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)

        # 输出格式
        self.format_combo = QComboBox()
        normalized_format = str(default_format).upper()
        self.format_combo.addItems(["PNG"] if normalized_format == "PNG" else ["MP4", "GIF"])
        self.format_combo.setCurrentText(normalized_format if normalized_format in {"MP4", "GIF", "PNG"} else "MP4")
        self.format_combo.setEnabled(normalized_format != "PNG")
        self.format_combo.currentIndexChanged.connect(self._update_file_label)
        form.addRow("输出格式:", self.format_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["静态图", "动画"])
        self.mode_combo.setCurrentIndex(0 if normalized_format == "PNG" else 1)
        form.addRow("导出模式:", self.mode_combo)

        # 动画名称
        self.anim_combo = QComboBox()
        self.anim_combo.addItems(["idle", "walk", "run"])
        self.anim_combo.setCurrentText("idle")
        form.addRow("动画名称:", self.anim_combo)
        self.mode_combo.currentIndexChanged.connect(self._update_animation_controls)

        # 时长
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 10)
        self.duration_spin.setValue(2)
        self.duration_spin.setSuffix(" 秒")
        form.addRow("时长:", self.duration_spin)

        # 帧率
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(1 if normalized_format == "PNG" else 15)
        self.fps_spin.setSuffix(" fps")
        form.addRow("帧率:", self.fps_spin)

        # 缩放
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(1, 8)
        self.scale_spin.setValue(4 if normalized_format == "PNG" else 2)
        self.scale_spin.setSuffix("x")
        form.addRow("缩放:", self.scale_spin)

        layout.addLayout(form)

        self.max_resolution_spin = QSpinBox()
        self.max_resolution_spin.setRange(256, 16384)
        self.max_resolution_spin.setSingleStep(256)
        self.max_resolution_spin.setValue(8192)
        self.max_resolution_spin.setSuffix(" px")
        form.addRow("最大分辨率:", self.max_resolution_spin)

        self.margin_spin = QSpinBox()
        self.margin_spin.setRange(0, 512)
        self.margin_spin.setValue(0)
        self.margin_spin.setSuffix(" px")
        form.addRow("边距:", self.margin_spin)

        # 输出文件路径
        file_header = QLabel("输出文件:")
        file_header.setObjectName("dialogFieldLabel")
        layout.addWidget(file_header)

        self.file_label = QLabel()
        self.file_label.setObjectName("fileLabel")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        # 预乘 Alpha
        self.pma_checkbox = QCheckBox("启用预乘 Alpha (--pma)")
        self.pma_checkbox.setChecked(True)
        layout.addWidget(self.pma_checkbox)

        self.transparent_checkbox = QCheckBox("透明背景")
        self.transparent_checkbox.setChecked(True)
        layout.addWidget(self.transparent_checkbox)

        # 自动打开
        self.auto_open_cb = QCheckBox("导出完成后自动打开文件")
        self.auto_open_cb.setChecked(True)
        layout.addWidget(self.auto_open_cb)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelButton")
        cancel_btn.setAccessibleName("取消导出设置")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("导出")
        ok_btn.setObjectName("dialogPrimaryButton")
        ok_btn.setProperty("fluentAppearance", "primary")
        ok_btn.setAccessibleName("确认导出")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)

        self._skel_base = skel_base
        self._timestamp = timestamp
        self._update_animation_controls()
        self._update_file_label()

    def _update_file_label(self):
        fmt = self.format_combo.currentText().lower()
        ext = ".png" if fmt == "png" else (".mp4" if fmt == "mp4" else ".gif")
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

    def settings(self) -> ExportSettings:
        """Return the typed settings used by identity-based PNG export."""
        return ExportSettings(
            animation=self.anim_combo.currentText().strip() or "idle",
            static=self.mode_combo.currentText() == "静态图",
            scale=self.scale_spin.value(),
            max_resolution=self.max_resolution_spin.value(),
            margin=self.margin_spin.value(),
            transparent=self.transparent_checkbox.isChecked(),
            pma=self.pma_checkbox.isChecked(),
            format="Png",
            fps=self.fps_spin.value(),
        )

    def _update_animation_controls(self):
        is_static = self.mode_combo.currentText() == "静态图"
        self.anim_combo.setEnabled(not is_static)
        self.fps_spin.setEnabled(not is_static)
        if is_static:
            self.fps_spin.setValue(1)
