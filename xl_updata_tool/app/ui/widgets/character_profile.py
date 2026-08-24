# -*- coding: utf-8 -*-
"""角色 Wiki 详情视图。

这个组件只负责把 :class:`CharacterProfile` 展示为 Qt 原生区块，
不读取缓存、不解析 Lua，也不改变角色数据契约。这样详情页的布局可以
独立演进，解析和导出逻辑仍由 Characters Feature 负责。
"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.features.characters.profile import CharacterProfile, ProfileField, ProfileSkill, ProfileStat


NUMBER_PATTERN = re.compile(r"(?<![\w.])[+-]?\d+(?:,\d{3})*(?:\.\d+)?%?")


class CharacterProfileView(QWidget):
    """可滚动容器中的角色 Wiki 详情内容。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("characterProfileView")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        self._highlight_numbers = False
        self._skill_labels: list[tuple[QLabel, str]] = []

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)

        self.empty_state = QLabel("从左侧选择角色\n查看详细属性", self)
        self.empty_state.setObjectName("detailEmptyState")
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.empty_state.setMinimumHeight(260)
        self.empty_state.setWordWrap(True)
        self._layout.addWidget(self.empty_state)

        self.content = QWidget(self)
        self.content.setObjectName("detailBody")
        self.content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(12)
        self._layout.addWidget(self.content)
        self.content.setVisible(False)

        self.number_highlight_button = QPushButton("高亮技能数字", self)
        self.number_highlight_button.setObjectName("numberHighlightButton")
        self.number_highlight_button.setProperty("fluentAppearance", "danger")
        self.number_highlight_button.setCheckable(True)
        self.number_highlight_button.setToolTip("标出技能描述中的数值、百分比和小数")
        self.number_highlight_button.setAccessibleName("高亮技能数字")
        self.number_highlight_button.toggled.connect(self._set_number_highlight)
        self._layout.insertWidget(1, self.number_highlight_button, 0, Qt.AlignRight)
        self.number_highlight_button.setVisible(False)

        self._profile: CharacterProfile | None = None

    def clear_profile(self) -> None:
        """清空详情，回到选择提示。"""
        self._profile = None
        self._skill_labels.clear()
        self._clear_layout(self.content_layout)
        self.content.setVisible(False)
        self.empty_state.setVisible(True)
        self.number_highlight_button.setVisible(False)
        self.number_highlight_button.setChecked(False)

    def set_profile(self, profile: CharacterProfile) -> None:
        """设置角色资料并重建展示区块。"""
        self._profile = profile
        self._skill_labels.clear()
        self._clear_layout(self.content_layout)
        self.empty_state.setVisible(False)
        self.content.setVisible(True)
        self.number_highlight_button.setVisible(bool(profile.skills))
        self.number_highlight_button.setChecked(False)

        self.content_layout.addWidget(self._build_header(profile))
        if profile.summary:
            self.content_layout.addWidget(self._build_text_section("角色简介", profile.summary))
        self.content_layout.addWidget(self._build_primary_stats(profile.primary_stats))
        self.content_layout.addWidget(self._build_fields_section("战斗属性", profile.secondary_stats, columns=2))
        if profile.skills:
            self.content_layout.addWidget(self._build_skills_section(profile.skills))
        if profile.progression:
            self.content_layout.addWidget(self._build_fields_section("成长与消耗", profile.progression, columns=1))
        if profile.badge_info:
            self.content_layout.addWidget(self._build_text_section("徽章建议", profile.badge_info))
        if profile.story:
            self.content_layout.addWidget(self._build_fields_section("角色故事", profile.story, columns=1))
        if profile.voices:
            self.content_layout.addWidget(self._build_fields_section("语音档案", profile.voices, columns=1))
        self.content_layout.addStretch(1)

    def _set_number_highlight(self, enabled: bool) -> None:
        self._highlight_numbers = enabled
        self.number_highlight_button.setText("关闭数字高亮" if enabled else "高亮技能数字")
        for label, description in self._skill_labels:
            label.setText(self._format_skill_description(description))

    def _format_skill_description(self, description: str) -> str:
        escaped = html.escape(description).replace("\n", "<br/>")
        if not self._highlight_numbers:
            return escaped

        return NUMBER_PATTERN.sub(
            r'<span style="background-color:#d97777; color:#ffffff; font-weight:600;">\g<0></span>',
            escaped,
        )

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                CharacterProfileView._clear_nested_layout(child_layout)

    @staticmethod
    def _clear_nested_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                CharacterProfileView._clear_nested_layout(child_layout)

    def _build_header(self, profile: CharacterProfile) -> QFrame:
        frame = QFrame(self.content)
        frame.setObjectName("profileHeader")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel(profile.name or "未命名角色", frame)
        title.setObjectName("profileName")
        title_row.addWidget(title)
        title_row.addStretch(1)
        if profile.raw_id and profile.raw_id != "-":
            identifier = QLabel(f"ID {profile.raw_id}", frame)
            identifier.setObjectName("profileId")
            title_row.addWidget(identifier)
        layout.addLayout(title_row)

        tags = QHBoxLayout()
        tags.setSpacing(6)
        for field in profile.identity:
            if field.label == "ID":
                continue
            tag = QLabel(f"{field.label}  {field.value}", frame)
            tag.setObjectName("profileTag")
            tags.addWidget(tag)
        tags.addStretch(1)
        layout.addLayout(tags)
        return frame

    def _build_primary_stats(self, stats: tuple[ProfileStat, ...]) -> QFrame:
        frame = self._section_frame("核心属性")
        section_layout = frame.layout()
        section_layout.setContentsMargins(12, 8, 12, 12)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index, stat in enumerate(stats):
            tile = QFrame(frame)
            tile.setObjectName("statTile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(12, 10, 12, 10)
            tile_layout.setSpacing(4)
            label = QLabel(stat.label, tile)
            label.setObjectName("statLabel")
            value = QLabel(f"{stat.initial}  →  {stat.maximum}", tile)
            value.setObjectName("statValue")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            tile_layout.addWidget(label)
            tile_layout.addWidget(value)
            grid.addWidget(tile, 0, index)
        section_layout.addLayout(grid)
        return frame

    def _build_skills_section(self, skills: tuple[ProfileSkill, ...]) -> QFrame:
        frame = self._section_frame("技能")
        layout = frame.layout()
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)
        for skill in skills:
            card = QFrame(frame)
            card.setObjectName("skillCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(4)
            title = QLabel(skill.label, card)
            title.setObjectName("skillTitle")
            description = QLabel(card)
            description.setObjectName("skillDescription")
            description.setTextFormat(Qt.RichText)
            description.setTextInteractionFlags(Qt.TextSelectableByMouse)
            description.setWordWrap(True)
            description.setText(self._format_skill_description(skill.description))
            self._skill_labels.append((description, skill.description))
            card_layout.addWidget(title)
            card_layout.addWidget(description)
            layout.addWidget(card)
        return frame

    def _build_text_section(self, title: str, text: str) -> QFrame:
        frame = self._section_frame(title)
        layout = frame.layout()
        layout.setContentsMargins(12, 8, 12, 12)
        label = QLabel(text, frame)
        label.setObjectName("profileBodyText")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)
        return frame

    def _build_fields_section(self, title: str, fields: tuple[ProfileField, ...], columns: int) -> QFrame:
        frame = self._section_frame(title)
        section_layout = frame.layout()
        section_layout.setContentsMargins(12, 8, 12, 12)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        for index, field in enumerate(fields):
            row, column = divmod(index, columns)
            cell = QWidget(frame)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            label = QLabel(field.label, cell)
            label.setObjectName("profileFieldLabel")
            value = QLabel(field.value, cell)
            value.setObjectName("profileFieldValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            cell_layout.addWidget(label)
            cell_layout.addWidget(value)
            grid.addWidget(cell, row, column)
        section_layout.addLayout(grid)
        return frame

    @staticmethod
    def _section_frame(title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("profileSection")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 0)
        heading = QLabel(title, frame)
        heading.setObjectName("profileSectionTitle")
        layout.addWidget(heading)
        return frame
