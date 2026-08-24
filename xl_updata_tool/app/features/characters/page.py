"""角色功能域页面。

页面自持角色列表、筛选、Wiki 详情和操作栏，只向外发出语义信号；角色数据
恢复、Lua 解析、仓库增量和未读状态由 CharacterController/Service 负责。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QHeaderView,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.shared.qt.tokens import get_color
from .profile_widget import CharacterProfileView
from app.shared.qt.chrome import (
    create_action_button,
    create_command_bar,
    create_empty_state,
    create_page_header,
)


class CharacterPage(QWidget):
    """角色数据工作区及其对外语义信号。"""

    parse_requested = Signal()
    search_changed = Signal(str)
    refresh_requested = Signal()
    mark_all_read_requested = Signal()
    export_csv_requested = Signal()
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("viewContainer")
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        top_bar, self.character_title, _ = create_page_header("角色数据", parent=self)
        main_layout.addWidget(top_bar)

        ctrl_bar, ctrl_layout = create_command_bar(self)
        ctrl_layout.addWidget(create_action_button("开始解析", "primary", self.parse_requested.emit, self))

        self.character_search = QLineEdit()
        self.character_search.setPlaceholderText("搜索角色名称...")
        self.character_search.setMinimumWidth(250)
        self.character_search.setToolTip("按名称筛选角色")
        self.character_search.setAccessibleName("角色名称筛选")
        self.character_search.textChanged.connect(self.search_changed.emit)
        ctrl_layout.addWidget(self.character_search)
        ctrl_layout.addWidget(create_action_button("刷新列表", "secondary", self.refresh_requested.emit, self))
        self.btn_mark_all_read = create_action_button(
            "全部标为已读", "secondary", self.mark_all_read_requested.emit, self
        )
        ctrl_layout.addWidget(self.btn_mark_all_read)
        ctrl_layout.addWidget(create_action_button("下载 CSV", "secondary", self.export_csv_requested.emit, self))

        self.character_status = QLabel("")
        self.character_status.setStyleSheet(
            f"color:{get_color('TEXT_SECONDARY')}; font-size:12px; "
            "background:transparent; padding-left:16px;"
        )
        ctrl_layout.addWidget(self.character_status)
        ctrl_layout.addStretch()
        main_layout.addWidget(ctrl_bar)

        body = QWidget(self)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(16, 16, 16, 16)
        body_layout.setSpacing(12)

        self.character_table = QTableWidget()
        self.character_table.setColumnCount(3)
        self.character_table.setHorizontalHeaderLabels(["序号", "名称", "状态"])
        header = self.character_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.character_table.setAlternatingRowColors(True)
        self.character_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.character_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.character_table.verticalHeader().setVisible(False)
        self.character_table.setShowGrid(True)
        self.character_table.verticalHeader().setDefaultSectionSize(34)
        self.character_table.setStyleSheet(f"""
            QTableWidget {{ background-color:{get_color('BG_DARK')}; border:1px solid {get_color('BORDER')}; border-radius:6px; }}
            QTableWidget::item {{ padding:6px 12px; font-size:13px; }}
            QTableWidget::item:selected {{ background-color:{get_color('ACCENT')}; color:#fff; }}
            QHeaderView::section {{ background-color:{get_color('BG_SURFACE')}; padding:8px 12px;
                border:none; border-bottom:2px solid {get_color('BORDER')}; font-size:12px;
                font-weight:600; color:{get_color('TEXT_SECONDARY')}; }}
        """)
        self.character_table.itemSelectionChanged.connect(self.selection_changed.emit)
        body_layout.addWidget(self.character_table, 3)

        detail_container = QWidget()
        detail_container.setObjectName("detailPanel")
        detail_container.setStyleSheet(
            f"QWidget#detailPanel {{ background-color:{get_color('BG_ELEVATED')}; "
            f"border:1px solid {get_color('BORDER')}; border-radius:8px; }}"
        )
        self.character_detail = detail_container
        detail_layout = QVBoxLayout(detail_container)
        detail_layout.setContentsMargins(16, 16, 16, 16)
        detail_layout.setSpacing(0)
        self.character_profile_view = CharacterProfileView(detail_container)
        detail_layout.addWidget(self.character_profile_view)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(detail_container)
        scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        body_layout.addWidget(scroll_area, 7)

        content = QWidget(self)
        content.setObjectName("viewContent")
        content_layout = QGridLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(body, 0, 0)

        self.character_empty = create_empty_state("暂无角色数据，请先导入资源并解密 Lua", content)
        self.character_empty.setVisible(False)
        self.character_empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        content_layout.addWidget(self.character_empty, 0, 0)
        main_layout.addWidget(content, 1)
