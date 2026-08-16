# -*- coding: utf-8 -*-
"""模块职责：音频管理器视图构建器

创建音频管理器视图容器（标题栏 + 工具栏 + 音频表格 + 播放控制区 + 状态栏），
返回 (container, controls_dict)，其中 controls_dict 提供外部操作所需的控件引用。
信号通过 parent 回调连接到主窗口。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QFrame, QSlider, QHeaderView,
)

from app.ui.theme import (
    BG_DARK, BG_SURFACE, BG_ELEVATED, BORDER, ACCENT,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, DANGER, INFO,
)


def create_audio_view(parent=None):
    """创建音频管理器视图容器，返回 (container, controls_dict)。

    controls_dict 包含外部需要操作的控件：
      audio_title, audio_table, audio_play_btn, audio_now_playing,
      audio_position_label, audio_slider, audio_volume, audio_status
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

    audio_title = QLabel("🎵 音频管理器  共 0 个音频文件")
    audio_title.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:16px; font-weight:bold; background:transparent; border:none;")
    top_layout.addWidget(audio_title)
    top_layout.addStretch()

    btn_close_audio = QPushButton("✕ 关闭音频")
    btn_close_audio.setFixedSize(100, 32)
    btn_close_audio.setStyleSheet(f"""
        QPushButton {{ background-color:{DANGER}; border:none; border-radius:6px;
                      color:#fff; font-size:12px; font-weight:600; }}
        QPushButton:hover {{ opacity:0.85; }}
    """)
    if parent is not None:
        btn_close_audio.clicked.connect(lambda: parent._toggle_audio_mode(False))
    top_layout.addWidget(btn_close_audio)
    layout.addWidget(top_bar)

    # 工具栏
    ctrl_bar = QFrame()
    ctrl_bar.setFixedHeight(50)
    ctrl_bar.setStyleSheet(f"QFrame {{ background-color:{BG_ELEVATED}; border-bottom:1px solid {BORDER}; }}")
    ctrl_layout = QHBoxLayout(ctrl_bar)
    ctrl_layout.setContentsMargins(16, 0, 16, 0)

    btn_style = f"""
        QPushButton {{ background-color:{INFO}; border:none; border-radius:6px;
                      color:#fff; font-size:12px; font-weight:600; padding:6px 12px; }}
        QPushButton:hover {{ opacity:0.85; }}
    """
    btn_decrypt = QPushButton("🔓 开始解密")
    btn_decrypt.setStyleSheet(btn_style)
    if parent is not None:
        btn_decrypt.clicked.connect(lambda: parent._start_audio_decrypt(force=False))
    ctrl_layout.addWidget(btn_decrypt)

    btn_refresh = QPushButton("🔄 刷新列表")
    btn_refresh.setStyleSheet(btn_style)
    if parent is not None:
        btn_refresh.clicked.connect(lambda: parent._load_audio_list())
    ctrl_layout.addWidget(btn_refresh)

    btn_export = QPushButton("📤 导出选中")
    btn_export.setStyleSheet(btn_style)
    if parent is not None:
        btn_export.clicked.connect(parent._export_selected_audio)
    ctrl_layout.addWidget(btn_export)

    btn_play = QPushButton("▶ 播放选中")
    btn_play.setStyleSheet(btn_style)
    if parent is not None:
        btn_play.clicked.connect(parent._play_selected_audio)
    ctrl_layout.addWidget(btn_play)

    ctrl_layout.addStretch()
    layout.addWidget(ctrl_bar)

    # 音频树（按目录层级折叠收起，加快加载）
    audio_table = QTreeWidget()
    audio_table.setColumnCount(5)
    audio_table.setHeaderLabels(["文件名", "目录", "时长", "格式", "大小"])
    hdr = audio_table.header()
    hdr.setSectionResizeMode(0, QHeaderView.Stretch)
    hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
    audio_table.setAlternatingRowColors(True)
    audio_table.setSelectionMode(QAbstractItemView.SingleSelection)
    audio_table.setEditTriggers(QTreeWidget.NoEditTriggers)
    audio_table.setStyleSheet(f"""
        QTreeWidget {{ background-color:{BG_DARK}; border:none; }}
        QTreeWidget::item {{ padding:6px 8px; font-size:13px; }}
        QTreeWidget::item:selected {{ background-color:{ACCENT}; color:#fff; }}
        QHeaderView::section {{ background-color:{BG_SURFACE}; padding:8px 10px;
            border:none; border-bottom:1px solid {BORDER}; font-size:12px;
            font-weight:600; color:{TEXT_SECONDARY}; }}
    """)
    audio_table.setContextMenuPolicy(Qt.CustomContextMenu)
    if parent is not None:
        audio_table.customContextMenuRequested.connect(parent._show_audio_context_menu)
        audio_table.itemDoubleClicked.connect(parent._on_audio_double_click)
    layout.addWidget(audio_table, 1)

    # 底部播放控制区
    player_bar = QFrame()
    player_bar.setFixedHeight(56)
    player_bar.setStyleSheet(f"QFrame {{ background-color:{BG_SURFACE}; border-top:1px solid {BORDER}; }}")
    player_layout = QHBoxLayout(player_bar)
    player_layout.setContentsMargins(16, 0, 16, 0)

    audio_play_btn = QPushButton("▶")
    audio_play_btn.setFixedSize(36, 36)
    audio_play_btn.setStyleSheet(f"""
        QPushButton {{ background-color:{ACCENT}; border:none; border-radius:18px;
                      color:#fff; font-size:16px; font-weight:bold; }}
        QPushButton:hover {{ opacity:0.85; }}
        QPushButton:disabled {{ background-color:{BG_ELEVATED}; color:{TEXT_MUTED}; }}
    """)
    audio_play_btn.setEnabled(False)
    if parent is not None:
        audio_play_btn.clicked.connect(parent._toggle_play)
    player_layout.addWidget(audio_play_btn)

    audio_now_playing = QLabel("未播放")
    audio_now_playing.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; background:transparent; border:none; min-width:160px;")
    player_layout.addWidget(audio_now_playing)

    audio_position_label = QLabel("00:00 / 00:00")
    audio_position_label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:11px; background:transparent; border:none;")
    player_layout.addWidget(audio_position_label)

    audio_slider = QSlider(Qt.Horizontal)
    audio_slider.setFixedHeight(20)
    audio_slider.setStyleSheet(f"""
        QSlider::groove:horizontal {{ background:{BG_ELEVATED}; height:4px; border-radius:2px; }}
        QSlider::handle:horizontal {{ background:{ACCENT}; width:14px; margin:-5px 0; border-radius:7px; }}
        QSlider::sub-page:horizontal {{ background:{ACCENT}; border-radius:2px; }}
    """)
    audio_slider.setEnabled(False)
    if parent is not None:
        audio_slider.sliderMoved.connect(parent._on_audio_slider_moved)
    player_layout.addWidget(audio_slider, 1)

    vol_label = QLabel("🔊")
    vol_label.setStyleSheet("background:transparent; border:none;")
    player_layout.addWidget(vol_label)

    audio_volume = QSlider(Qt.Horizontal)
    audio_volume.setFixedWidth(80)
    audio_volume.setRange(0, 100)
    audio_volume.setValue(80)
    audio_volume.setStyleSheet(f"""
        QSlider::groove:horizontal {{ background:{BG_ELEVATED}; height:4px; border-radius:2px; }}
        QSlider::handle:horizontal {{ background:{ACCENT}; width:10px; margin:-3px 0; border-radius:5px; }}
        QSlider::sub-page:horizontal {{ background:{ACCENT}; border-radius:2px; }}
    """)
    if parent is not None:
        audio_volume.valueChanged.connect(parent._set_audio_volume)
    player_layout.addWidget(audio_volume)

    layout.addWidget(player_bar)

    # 状态栏
    audio_status = QLabel("已选: 0 个 | 共 0 个音频文件")
    audio_status.setFixedHeight(28)
    audio_status.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:12px; padding:4px 16px; background-color:{BG_SURFACE}; border-top:1px solid {BORDER};")
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
    }
    return container, controls