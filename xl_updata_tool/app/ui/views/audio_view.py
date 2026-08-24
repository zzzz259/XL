# -*- coding: utf-8 -*-
"""模块职责：音频管理器视图构建器

创建音频管理器视图容器（标题栏 + 工具栏 + 音频表格 + 播放控制区 + 状态栏），
返回 (container, controls_dict)，其中 controls_dict 提供外部操作所需的控件引用。
信号通过 parent 回调连接到主窗口。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QTreeWidget, QAbstractItemView, QFrame, QSlider, QHeaderView,
)

from app.shared.qt.chrome import (
    create_action_button,
    create_command_bar,
    create_empty_state,
    create_page_header,
    create_status_label,
)


def create_audio_view(parent=None):
    """创建音频管理器视图容器，返回 (container, controls_dict)。

    controls_dict 包含外部需要操作的控件：
      audio_title, audio_table, audio_play_btn, audio_now_playing,
      audio_position_label, audio_slider, audio_volume, audio_status
    """
    container = QWidget()
    container.setObjectName("viewContainer")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # 顶部栏：标题 + 关闭按钮
    top_bar, audio_title, btn_close_audio = create_page_header(
        "音频管理器 · 共 0 个音频文件",
        "关闭音频",
        (lambda: parent._toggle_audio_mode(False)) if parent is not None else None,
        container,
    )
    layout.addWidget(top_bar)

    # 工具栏
    ctrl_bar, ctrl_layout = create_command_bar(container)

    btn_refresh = create_action_button(
        "刷新列表",
        "secondary",
        (lambda: parent._load_audio_list(force_reload=True)) if parent is not None else None,
        container,
    )
    ctrl_layout.addWidget(btn_refresh)

    btn_export = create_action_button(
        "导出选中",
        "secondary",
        parent._export_selected_audio if parent is not None else None,
        container,
    )
    ctrl_layout.addWidget(btn_export)

    btn_play = create_action_button(
        "播放选中",
        "secondary",
        parent._play_selected_audio if parent is not None else None,
        container,
    )
    ctrl_layout.addWidget(btn_play)

    btn_mark_read = create_action_button(
        "全部标为已读",
        "secondary",
        parent._mark_all_audio_read if parent is not None else None,
        container,
    )
    ctrl_layout.addWidget(btn_mark_read)

    ctrl_layout.addStretch()
    layout.addWidget(ctrl_bar)

    # 音频树（按目录层级折叠收起，加快加载）
    audio_table = QTreeWidget()
    audio_table.setObjectName("audioTree")
    audio_table.setColumnCount(6)
    audio_table.setHeaderLabels(["文件名", "目录", "时长", "格式", "大小", "状态"])
    hdr = audio_table.header()
    hdr.setSectionResizeMode(0, QHeaderView.Stretch)
    hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
    audio_table.setAlternatingRowColors(True)
    # 勾选状态承担文件管理选择，不使用蓝色选中态；Ctrl 多选由主窗口统一处理。
    audio_table.setSelectionMode(QAbstractItemView.NoSelection)
    audio_table.setSelectionBehavior(QAbstractItemView.SelectRows)
    audio_table.setEditTriggers(QTreeWidget.NoEditTriggers)
    audio_table.setContextMenuPolicy(Qt.CustomContextMenu)
    if parent is not None:
        audio_table.customContextMenuRequested.connect(parent._show_audio_context_menu)
        audio_table.itemPressed.connect(parent._on_audio_item_pressed)
        audio_table.itemClicked.connect(parent._on_audio_item_clicked)
        audio_table.itemDoubleClicked.connect(parent._on_audio_double_click)
    content = QWidget(container)
    content.setObjectName("viewContent")
    content_layout = QGridLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(0)
    content_layout.addWidget(audio_table, 0, 0)

    audio_empty = create_empty_state("暂无音频文件\n请先导入包含音频的资源", content)
    audio_empty.setObjectName("audioEmptyState")
    audio_empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    content_layout.addWidget(audio_empty, 0, 0)
    layout.addWidget(content, 1)

    # 底部播放控制区
    player_bar = QFrame()
    player_bar.setObjectName("pagePlayerBar")
    player_bar.setFixedHeight(56)
    player_layout = QHBoxLayout(player_bar)
    player_layout.setContentsMargins(16, 0, 16, 0)

    audio_play_btn = QPushButton("播放")
    audio_play_btn.setObjectName("audioPlayButton")
    audio_play_btn.setFixedSize(64, 32)
    audio_play_btn.setProperty("fluentAppearance", "primary")
    audio_play_btn.setToolTip("播放或暂停当前音频")
    audio_play_btn.setAccessibleName("播放或暂停当前音频")
    audio_play_btn.setEnabled(False)
    if parent is not None:
        audio_play_btn.clicked.connect(parent._toggle_play)
    player_layout.addWidget(audio_play_btn)

    audio_now_playing = QLabel("未播放")
    audio_now_playing.setObjectName("audioNowPlaying")
    player_layout.addWidget(audio_now_playing)

    audio_position_label = QLabel("00:00 / 00:00")
    audio_position_label.setObjectName("audioPosition")
    player_layout.addWidget(audio_position_label)

    audio_slider = QSlider(Qt.Horizontal)
    audio_slider.setObjectName("audioProgressSlider")
    audio_slider.setFixedHeight(20)
    audio_slider.setEnabled(False)
    if parent is not None:
        audio_slider.sliderMoved.connect(parent._on_audio_slider_moved)
        audio_slider.sliderPressed.connect(parent._on_audio_slider_pressed)
        audio_slider.sliderReleased.connect(parent._on_audio_slider_released)
    player_layout.addWidget(audio_slider, 1)

    vol_label = QLabel("音量")
    vol_label.setAccessibleName("音量")
    player_layout.addWidget(vol_label)

    audio_volume = QSlider(Qt.Horizontal)
    audio_volume.setObjectName("audioVolumeSlider")
    audio_volume.setFixedWidth(80)
    audio_volume.setRange(0, 100)
    audio_volume.setValue(80)
    if parent is not None:
        audio_volume.valueChanged.connect(parent._set_audio_volume)
    player_layout.addWidget(audio_volume)

    layout.addWidget(player_bar)

    # 状态栏
    audio_status = create_status_label("已选: 0 个 | 共 0 个音频文件", container)
    audio_status.setFixedHeight(28)
    layout.addWidget(audio_status)

    controls = {
        "audio_title": audio_title,
        "audio_table": audio_table,
        "audio_play_btn": audio_play_btn,
        "audio_now_playing": audio_now_playing,
        "audio_position_label": audio_position_label,
        "audio_slider": audio_slider,
        "audio_volume": audio_volume,
        "audio_status": audio_status,
        "audio_empty": audio_empty,
    }
    return container, controls
