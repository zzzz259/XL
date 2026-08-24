"""音频功能域页面。

页面只向外发出语义信号，不再把主窗口作为回调宿主，也不返回控件字典。
业务状态和解密线程仍在 P1b 迁移；本页先完成 UI 所有权收口。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QHeaderView

from app.shared.qt.chrome import (
    create_action_button,
    create_command_bar,
    create_empty_state,
    create_page_header,
    create_status_label,
)


class AudioPage(QWidget):
    """音频管理器视图及其对外语义信号。"""

    close_requested = Signal()
    refresh_requested = Signal()
    export_requested = Signal()
    play_selected_requested = Signal()
    mark_all_read_requested = Signal()
    context_menu_requested = Signal(object)
    item_pressed = Signal(object, int)
    item_clicked = Signal(object, int)
    item_double_clicked = Signal(object, int)
    play_toggled = Signal()
    slider_moved = Signal(int)
    slider_pressed = Signal()
    slider_released = Signal()
    volume_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("viewContainer")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_bar, self.audio_title, _ = create_page_header(
            "音频管理器 · 共 0 个音频文件",
            "关闭音频",
            self.close_requested.emit,
            self,
        )
        layout.addWidget(top_bar)

        ctrl_bar, ctrl_layout = create_command_bar(self)
        for text, signal in (
            ("刷新列表", self.refresh_requested),
            ("导出选中", self.export_requested),
            ("播放选中", self.play_selected_requested),
            ("全部标为已读", self.mark_all_read_requested),
        ):
            button = create_action_button(text, "secondary", signal.emit, self)
            ctrl_layout.addWidget(button)
        ctrl_layout.addStretch()
        layout.addWidget(ctrl_bar)

        self.audio_table = QTreeWidget()
        self.audio_table.setObjectName("audioTree")
        self.audio_table.setColumnCount(6)
        self.audio_table.setHeaderLabels(["文件名", "目录", "时长", "格式", "大小", "状态"])
        header = self.audio_table.header()
        for index in range(6):
            header.setSectionResizeMode(
                index,
                QHeaderView.Stretch if index == 0 else QHeaderView.ResizeToContents,
            )
        self.audio_table.setAlternatingRowColors(True)
        self.audio_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.audio_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.audio_table.setEditTriggers(QTreeWidget.NoEditTriggers)
        self.audio_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.audio_table.customContextMenuRequested.connect(self.context_menu_requested.emit)
        self.audio_table.itemPressed.connect(self.item_pressed.emit)
        self.audio_table.itemClicked.connect(self.item_clicked.emit)
        self.audio_table.itemDoubleClicked.connect(self.item_double_clicked.emit)

        content = QWidget(self)
        content.setObjectName("viewContent")
        content_layout = QGridLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.audio_table, 0, 0)

        self.audio_empty = create_empty_state("暂无音频文件\n请先导入包含音频的资源", content)
        self.audio_empty.setObjectName("audioEmptyState")
        self.audio_empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        content_layout.addWidget(self.audio_empty, 0, 0)
        layout.addWidget(content, 1)

        player_bar = QFrame()
        player_bar.setObjectName("pagePlayerBar")
        player_bar.setFixedHeight(56)
        player_layout = QHBoxLayout(player_bar)
        player_layout.setContentsMargins(16, 0, 16, 0)

        self.audio_play_btn = QPushButton("播放")
        self.audio_play_btn.setObjectName("audioPlayButton")
        self.audio_play_btn.setFixedSize(64, 32)
        self.audio_play_btn.setProperty("fluentAppearance", "primary")
        self.audio_play_btn.setToolTip("播放或暂停当前音频")
        self.audio_play_btn.setAccessibleName("播放或暂停当前音频")
        self.audio_play_btn.setEnabled(False)
        self.audio_play_btn.clicked.connect(self.play_toggled.emit)
        player_layout.addWidget(self.audio_play_btn)

        self.audio_now_playing = QLabel("未播放")
        self.audio_now_playing.setObjectName("audioNowPlaying")
        player_layout.addWidget(self.audio_now_playing)

        self.audio_position_label = QLabel("00:00 / 00:00")
        self.audio_position_label.setObjectName("audioPosition")
        player_layout.addWidget(self.audio_position_label)

        self.audio_slider = QSlider(Qt.Horizontal)
        self.audio_slider.setObjectName("audioProgressSlider")
        self.audio_slider.setFixedHeight(20)
        self.audio_slider.setEnabled(False)
        self.audio_slider.sliderMoved.connect(self.slider_moved.emit)
        self.audio_slider.sliderPressed.connect(self.slider_pressed.emit)
        self.audio_slider.sliderReleased.connect(self.slider_released.emit)
        player_layout.addWidget(self.audio_slider, 1)

        volume_label = QLabel("音量")
        volume_label.setAccessibleName("音量")
        player_layout.addWidget(volume_label)

        self.audio_volume = QSlider(Qt.Horizontal)
        self.audio_volume.setObjectName("audioVolumeSlider")
        self.audio_volume.setFixedWidth(80)
        self.audio_volume.setRange(0, 100)
        self.audio_volume.setValue(80)
        self.audio_volume.valueChanged.connect(self.volume_changed.emit)
        player_layout.addWidget(self.audio_volume)

        layout.addWidget(player_bar)

        self.audio_status = create_status_label("已选: 0 个 | 共 0 个音频文件", self)
        self.audio_status.setFixedHeight(28)
        layout.addWidget(self.audio_status)
