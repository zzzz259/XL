# -*- coding: utf-8 -*-
"""导出参数设置对话框模块"""

import os
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox, QFormLayout,
    QListWidget, QListWidgetItem, QRadioButton, QScrollArea, QToolButton, QWidget,
)
from PySide6.QtCore import Qt

from ..export_plan import ExportSettings
from ..export_presets import preset_for_family

class ExportSettingsDialog(QDialog):
    """导出参数设置对话框"""

    Rejected = QDialog.DialogCode.Rejected

    def __init__(
        self,
        skel_path,
        atlas_path,
        default_format="MP4",
        parent=None,
        animation_names=None,
        animation_durations=None,
        skin_names=None,
        resource_family=None,
        selected_group_count=1,
        family_summary=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("导出设置")
        self.setObjectName("exportSettingsDialog")
        self.setMinimumWidth(420)
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setMaximumHeight(max(360, int(screen.availableGeometry().height() * 0.9)))

        self.skel_path = skel_path
        self.atlas_path = atlas_path
        self.resource_family = str(resource_family or "").strip().casefold()
        self._preset = preset_for_family(self.resource_family)
        self._allow_video = self._preset.allow_video if self._preset else True
        self.selected_group_count = max(0, int(selected_group_count or 0))
        self.family_summary = tuple(
            str(value).strip().casefold()
            for value in (family_summary or (self.resource_family,))
            if str(value).strip()
        )
        self._custom_allowed = self.selected_group_count == 1
        self._advanced_expanded = False
        self._build_ui(default_format, animation_names, animation_durations, skin_names)

    def _build_ui(self, default_format, animation_names=None, animation_durations=None, skin_names=None):
        skel_base = os.path.splitext(os.path.basename(self.skel_path))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 标题
        title = QLabel("导出 Spine 资源")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("导出配置:"))
        self.builtin_radio = QRadioButton("使用内置默认配置")
        self.builtin_radio.setObjectName("builtinExportMode")
        self.builtin_radio.setAccessibleName("使用内置默认配置")
        self.builtin_radio.setChecked(True)
        mode_layout.addWidget(self.builtin_radio)
        self.custom_radio = QRadioButton("自定义导出")
        self.custom_radio.setObjectName("customExportMode")
        self.custom_radio.setAccessibleName("自定义导出")
        self.custom_radio.setEnabled(self._custom_allowed)
        if not self._custom_allowed:
            self.custom_radio.setToolTip("自定义导出仅支持恰好一个可见皮肤")
        mode_layout.addWidget(self.custom_radio)
        mode_layout.addStretch()
        layout.addLayout(mode_layout)

        self.mode_summary = QLabel(self._builtin_summary())
        self.mode_summary.setObjectName("exportModeSummary")
        self.mode_summary.setWordWrap(True)
        layout.addWidget(self.mode_summary)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("exportSettingsScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        scroll_content.setObjectName("exportSettingsScrollContent")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)
        scroll_area.setWidget(scroll_content)
        self.scroll_area = scroll_area
        layout.addWidget(scroll_area, 1)

        # 表单
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)
        self._form = form

        # 输出格式
        self.format_combo = QComboBox()
        normalized_format = str(default_format).upper()
        self.format_combo.currentIndexChanged.connect(self._update_file_label)
        form.addRow("输出格式:", self.format_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["静态图", "动画"])
        if not self._allow_video:
            self.mode_combo.removeItem(1)
        self.mode_combo.setCurrentIndex(0 if normalized_format == "PNG" else 1)
        form.addRow("导出模式:", self.mode_combo)

        # 动画名称
        self.anim_combo = QComboBox()
        names = tuple(dict.fromkeys(str(name).strip() for name in (animation_names or ()) if str(name).strip()))
        self.anim_combo.addItems(list(names or ("idle", "walk", "run")))
        default_animation = self._preset.default_animation if self._preset else "idle"
        self.anim_combo.setCurrentText(default_animation if default_animation in names else "idle")
        form.addRow("动画名称:", self.anim_combo)
        self._animation_durations = {
            str(name): float(duration)
            for name, duration in (animation_durations or {}).items()
            if str(name).strip()
        }
        self.anim_combo.currentTextChanged.connect(self._apply_animation_duration)
        self.mode_combo.currentIndexChanged.connect(self._update_animation_controls)

        self.skin_list = QListWidget()
        self.skin_list.setObjectName("spineSkinSelection")
        self.skin_list.setMinimumHeight(88)
        available_skins = tuple(dict.fromkeys(str(name).strip() for name in (skin_names or ("default",)) if str(name).strip()))
        default_skins = set(self._preset.default_skins if self._preset else ("default",))
        if not default_skins.intersection(available_skins):
            default_skins = {"default"}
        for name in available_skins or ("default",):
            item = QListWidgetItem(name, self.skin_list)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in default_skins else Qt.Unchecked)
        form.addRow("Spine 皮肤:", self.skin_list)

        # 时长
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.01, 3600)
        self.duration_spin.setDecimals(3)
        self.duration_spin.setValue(2.0)
        self.duration_spin.setSuffix(" 秒")
        form.addRow("时长:", self.duration_spin)

        self.physics_combo = QComboBox()
        self.physics_combo.addItems(["None", "Pose", "Reset", "Update"])
        self.physics_combo.setCurrentText("Update")
        form.addRow("物理效果:", self.physics_combo)

        self.warm_up_spin = QDoubleSpinBox()
        self.warm_up_spin.setRange(0, 60)
        self.warm_up_spin.setSingleStep(0.1)
        self.warm_up_spin.setDecimals(1)
        self.warm_up_spin.setSuffix(" 秒")
        form.addRow("物理预热:", self.warm_up_spin)

        self.time_spin = QDoubleSpinBox()
        self.time_spin.setRange(0, 3600)
        self.time_spin.setSingleStep(0.1)
        self.time_spin.setDecimals(1)
        self.time_spin.setSuffix(" 秒")
        form.addRow("起始时间:", self.time_spin)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.1, 8)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setDecimals(1)
        self.speed_spin.setSuffix("x")
        self.speed_spin.setValue(1.0)
        form.addRow("播放速度:", self.speed_spin)

        # 帧率
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(
            1 if normalized_format == "PNG" else (self._preset.video_fps if self._preset else 15)
        )
        self.fps_spin.setSuffix(" fps")
        form.addRow("帧率:", self.fps_spin)

        # 缩放
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(1, 8)
        self.scale_spin.setValue(
            self._preset.static_scale
            if self._preset and normalized_format == "PNG"
            else self._preset.video_scale
            if self._preset
            else 4 if normalized_format == "PNG" else 2
        )
        self.scale_spin.setSuffix("x")
        form.addRow("缩放:", self.scale_spin)

        scroll_layout.addLayout(form)

        self.max_resolution_spin = QSpinBox()
        self.max_resolution_spin.setRange(256, 16384)
        self.max_resolution_spin.setSingleStep(256)
        self.max_resolution_spin.setValue(self._preset.max_resolution if self._preset else 8192)
        self.max_resolution_spin.setSuffix(" px")
        form.addRow("最大分辨率:", self.max_resolution_spin)

        self.margin_spin = QSpinBox()
        self.margin_spin.setRange(0, 512)
        self.margin_spin.setValue(self._preset.margin if self._preset else 0)
        self.margin_spin.setSuffix(" px")
        form.addRow("边距:", self.margin_spin)

        self.background_combo = QComboBox()
        self.background_combo.addItem("透明", "#00000000")
        self.background_combo.addItem("灰色 #7f7f7f", "#7f7f7f")
        self.background_combo.addItem("黑色", "#000000")
        self.background_combo.addItem("白色", "#ffffff")
        form.addRow("背景颜色:", self.background_combo)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setObjectName("advancedExportSettingsToggle")
        self.advanced_toggle.setText("高级设置")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.RightArrow)
        self.advanced_toggle.toggled.connect(self._set_advanced_visible)
        scroll_layout.addWidget(self.advanced_toggle)

        # 输出文件路径
        file_header = QLabel("输出文件:")
        file_header.setObjectName("dialogFieldLabel")
        scroll_layout.addWidget(file_header)

        self.file_label = QLabel()
        self.file_label.setObjectName("fileLabel")
        self.file_label.setWordWrap(True)
        scroll_layout.addWidget(self.file_label)

        # 预乘 Alpha
        self.pma_checkbox = QCheckBox("启用预乘 Alpha (--pma)")
        self.pma_checkbox.setChecked(True)
        scroll_layout.addWidget(self.pma_checkbox)

        self.loop_checkbox = QCheckBox("动画循环 (--loop)")
        self.loop_checkbox.setChecked(False)
        scroll_layout.addWidget(self.loop_checkbox)

        self.disable_track_loop_checkbox = QCheckBox("禁用轨道循环 (--disable-track-loop)")
        self.disable_track_loop_checkbox.setChecked(False)
        scroll_layout.addWidget(self.disable_track_loop_checkbox)

        self.transparent_checkbox = QCheckBox("透明背景")
        self.transparent_checkbox.setChecked(True)
        scroll_layout.addWidget(self.transparent_checkbox)

        self.auto_border_checkbox = QCheckBox("自动边框")
        self.auto_border_checkbox.setChecked(True)
        self.auto_border_checkbox.setEnabled(False)
        self.auto_border_checkbox.setToolTip("未指定 fixed-view 时，SpineViewerCLI 会按内容自动计算边界")
        scroll_layout.addWidget(self.auto_border_checkbox)

        # 自动打开
        self.auto_open_cb = QCheckBox("导出完成后自动打开文件")
        self.auto_open_cb.setChecked(True)
        scroll_layout.addWidget(self.auto_open_cb)

        self._advanced_fields = [
            self.duration_spin,
            self.physics_combo,
            self.warm_up_spin,
            self.time_spin,
            self.speed_spin,
            self.fps_spin,
            self.scale_spin,
            self.max_resolution_spin,
            self.margin_spin,
            self.background_combo,
        ]
        self._advanced_checkboxes = [
            self.pma_checkbox,
            self.loop_checkbox,
            self.disable_track_loop_checkbox,
            self.transparent_checkbox,
            self.auto_border_checkbox,
            self.auto_open_cb,
        ]
        self._advanced_labels = [form.labelForField(field) for field in self._advanced_fields]

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
        if normalized_format == "PNG" or not self._allow_video:
            self.mode_combo.setCurrentIndex(0)
        else:
            self.mode_combo.setCurrentIndex(1)
        self._set_format_options(normalized_format)
        self._apply_animation_duration()
        self._set_advanced_visible(False)
        self._update_animation_controls()
        self._update_file_label()
        self.builtin_radio.toggled.connect(self._on_mode_changed)
        self.custom_radio.toggled.connect(self._on_mode_changed)
        self._on_mode_changed()

    def _builtin_summary(self):
        families = set(self.family_summary)
        parts = []
        if "cardspine" in families:
            parts.append("角色立绘：静态 PNG + MP4")
        if "battlespine" in families:
            parts.append("战斗小人：带 motion_stander 的静态 PNG")
        if "eventcovers" in families:
            parts.append("活动封面：静态 PNG")
        if not parts:
            parts.append("按资源类型使用内置静态导出配置")
        return "内置配置：" + "；".join(parts)

    def export_mode(self):
        return "custom" if self.custom_radio.isChecked() else "builtin"

    def _on_mode_changed(self):
        custom = self.export_mode() == "custom"
        self.mode_summary.setText(
            "自定义配置：仅对当前一个可见皮肤导出，手动选择图片或视频及参数。"
            if custom
            else self._builtin_summary()
        )
        manual_widgets = [
            self.format_combo,
            self.mode_combo,
            self.anim_combo,
            self.skin_list,
            self.advanced_toggle,
            *self._advanced_fields,
            *self._advanced_checkboxes,
        ]
        if not custom:
            self._set_advanced_visible(False)
        for widget in manual_widgets:
            widget.setEnabled(custom)
        for label in self._advanced_labels:
            if label is not None:
                label.setEnabled(custom)
        if custom:
            self._update_animation_controls()

    def _set_format_options(self, normalized_format=None):
        normalized_format = str(normalized_format or self.format_combo.currentText()).upper()
        is_static = self.mode_combo.currentText() == "静态图"
        wanted = ["PNG"] if is_static or not self._allow_video else ["MP4", "GIF"]
        current = normalized_format if normalized_format in wanted else wanted[0]
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        self.format_combo.addItems(wanted)
        self.format_combo.setCurrentText(current)
        self.format_combo.setEnabled(not is_static)
        if not self._allow_video:
            self.format_combo.setEnabled(False)
        self.format_combo.blockSignals(False)

    def _apply_animation_duration(self):
        duration = self._animation_durations.get(self.anim_combo.currentText())
        if duration is not None and duration > 0:
            self.duration_spin.setValue(duration)

    def _update_file_label(self):
        fmt = self.format_combo.currentText().lower()
        ext = ".png" if fmt == "png" else (".mp4" if fmt == "mp4" else ".gif")
        fname = f"{self._skel_base}_{self._timestamp}{ext}"
        self.file_label.setText(fname)

    def get_settings(self):
        return {
            "format": self.format_combo.currentText().lower(),
            "resource_family": self.resource_family,
            "animation": self.anim_combo.currentText(),
            "duration": self.duration_spin.value(),
            "fps": self.fps_spin.value(),
            "scale": self.scale_spin.value(),
            "max_resolution": self.max_resolution_spin.value(),
            "margin": self.margin_spin.value(),
            "time_offset": self.time_spin.value(),
            "transparent": self.transparent_checkbox.isChecked(),
            "pma": self.pma_checkbox.isChecked(),
            "disable_track_loop": self.disable_track_loop_checkbox.isChecked(),
            "auto_open": self.auto_open_cb.isChecked(),
            "file_label": self.file_label.text(),
            "skins": self.selected_skins(),
            "background_color": self.background_combo.currentData(),
        }

    def selected_skins(self):
        return tuple(
            self.skin_list.item(index).text()
            for index in range(self.skin_list.count())
            if self.skin_list.item(index).checkState() == Qt.Checked
        ) or ("default",)

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
            format=self.format_combo.currentText().title(),
            fps=self.fps_spin.value(),
            physics=self.physics_combo.currentText(),
            warm_up=self.warm_up_spin.value(),
            time_offset=self.time_spin.value(),
            duration=float(self.duration_spin.value()),
            speed=self.speed_spin.value(),
            loop=self.loop_checkbox.isChecked(),
            disable_track_loop=self.disable_track_loop_checkbox.isChecked(),
            skins=self.selected_skins(),
            background_color=str(self.background_combo.currentData() or "#00000000"),
            auto_border=self.auto_border_checkbox.isChecked(),
        )

    def _update_animation_controls(self):
        is_static = self.mode_combo.currentText() == "静态图"
        if not self._allow_video:
            is_static = True
            self.mode_combo.setCurrentIndex(0)
        self.mode_combo.setEnabled(self._allow_video)
        self.anim_combo.setEnabled(True)
        self._set_format_options()
        advanced = self._advanced_expanded
        for widget in (self.duration_spin, self.time_spin, self.fps_spin, self.speed_spin):
            label = self._form.labelForField(widget)
            if label is not None:
                label.setVisible(advanced and not is_static)
            widget.setVisible(advanced and not is_static)
        if self._preset:
            self.scale_spin.setValue(self._preset.static_scale if is_static else self._preset.video_scale)
            self.fps_spin.setValue(1 if is_static else self._preset.video_fps)
            wanted_background = "#00000000" if is_static else self._preset.video_background
            background_index = self.background_combo.findData(wanted_background)
            if background_index >= 0:
                self.background_combo.setCurrentIndex(background_index)
            self.transparent_checkbox.setChecked(is_static)
        for widget in (self.loop_checkbox, self.disable_track_loop_checkbox):
            widget.setVisible(advanced and not is_static)
        if is_static:
            self.fps_spin.setValue(1)
        self._update_file_label()

    def _set_advanced_visible(self, expanded):
        self._advanced_expanded = bool(expanded)
        self.advanced_toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        for field, label in zip(self._advanced_fields, self._advanced_labels):
            field.setVisible(bool(expanded))
            if label is not None:
                label.setVisible(bool(expanded))
        for widget in self._advanced_checkboxes:
            widget.setVisible(bool(expanded))
        self._update_animation_controls()
