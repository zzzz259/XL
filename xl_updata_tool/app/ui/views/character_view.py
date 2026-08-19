# -*- coding: utf-8 -*-
"""模块职责：角色数据视图构建器

创建角色数据视图容器（标题栏 + 搜索栏 + 角色表格 + 详情卡片 + 空状态提示），
返回 (container, controls_dict)，其中 controls_dict 提供外部操作所需的控件引用。
信号通过 parent 回调连接到主窗口。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QTableWidget, QLineEdit, QAbstractItemView, QScrollArea,
    QHeaderView,
)

from app.ui.theme import (
    get_color,
)
from app.ui.widgets.view_chrome import (
    create_action_button,
    create_command_bar,
    create_empty_state,
    create_page_header,
)
from app.ui.widgets.character_profile import CharacterProfileView


def create_character_view(parent=None):
    """创建角色数据视图容器，返回 (container, controls_dict)。

    controls_dict 包含外部需要操作的控件：
      character_title, character_search, character_table, character_detail,
      character_profile_view, character_status, character_empty
    """
    container = QWidget()
    container.setObjectName("viewContainer")
    main_layout = QVBoxLayout(container)
    main_layout.setContentsMargins(0, 0, 0, 0)
    main_layout.setSpacing(0)

    # 顶部栏：标题
    top_bar, character_title, _ = create_page_header("角色数据", parent=container)
    main_layout.addWidget(top_bar)

    # 搜索与控制栏
    ctrl_bar, ctrl_layout = create_command_bar(container)

    btn_parse = create_action_button(
        "开始解析",
        "primary",
        parent._manual_load_character if parent is not None else None,
        container,
    )
    ctrl_layout.addWidget(btn_parse)

    character_search = QLineEdit()
    character_search.setPlaceholderText("搜索角色名称...")
    character_search.setMinimumWidth(250)
    character_search.setToolTip("按名称筛选角色")
    character_search.setAccessibleName("角色名称筛选")
    if parent is not None:
        character_search.textChanged.connect(parent._filter_character_table)
    ctrl_layout.addWidget(character_search)

    btn_refresh = create_action_button(
        "刷新列表",
        "secondary",
        parent._refresh_character_list if parent is not None else None,
        container,
    )
    ctrl_layout.addWidget(btn_refresh)

    btn_csv = create_action_button(
        "下载 CSV",
        "secondary",
        parent._export_characters_csv if parent is not None else None,
        container,
    )
    ctrl_layout.addWidget(btn_csv)

    character_status = QLabel("")
    character_status.setStyleSheet(f"color:{get_color('TEXT_SECONDARY')}; font-size:12px; background:transparent; padding-left:16px;")
    ctrl_layout.addWidget(character_status)
    ctrl_layout.addStretch()
    main_layout.addWidget(ctrl_bar)

    # 主体内容：左右布局
    body = QWidget()
    body_layout = QHBoxLayout(body)
    body_layout.setContentsMargins(16, 16, 16, 16)
    body_layout.setSpacing(12)

    # 左侧：角色表格
    character_table = QTableWidget()
    character_table.setColumnCount(2)
    character_table.setHorizontalHeaderLabels(["序号", "名称"])
    hdr = character_table.horizontalHeader()
    hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(1, QHeaderView.Stretch)
    character_table.setAlternatingRowColors(True)
    character_table.setSelectionBehavior(QAbstractItemView.SelectRows)
    character_table.setEditTriggers(QTableWidget.NoEditTriggers)
    character_table.verticalHeader().setVisible(False)
    character_table.setShowGrid(True)
    character_table.verticalHeader().setDefaultSectionSize(34)
    character_table.setStyleSheet(f"""
        QTableWidget {{ background-color:{get_color('BG_DARK')}; border:1px solid {get_color('BORDER')}; border-radius:6px; }}
        QTableWidget::item {{ padding:6px 12px; font-size:13px; }}
        QTableWidget::item:selected {{ background-color:{get_color('ACCENT')}; color:#fff; }}
        QHeaderView::section {{ background-color:{get_color('BG_SURFACE')}; padding:8px 12px;
            border:none; border-bottom:2px solid {get_color('BORDER')}; font-size:12px;
            font-weight:600; color:{get_color('TEXT_SECONDARY')}; }}
    """)
    if parent is not None:
        character_table.itemSelectionChanged.connect(parent._on_character_select)
    body_layout.addWidget(character_table, 3)

    # 右侧：详情卡片（放入 QScrollArea）
    detail_container = QWidget()
    detail_container.setObjectName("detailPanel")
    detail_container.setStyleSheet(f"QWidget#detailPanel {{ background-color:{get_color('BG_ELEVATED')}; border:1px solid {get_color('BORDER')}; border-radius:8px; }}")
    character_detail = detail_container
    detail_layout = QVBoxLayout(detail_container)
    detail_layout.setContentsMargins(16, 16, 16, 16)
    detail_layout.setSpacing(0)
    character_profile_view = CharacterProfileView(detail_container)
    detail_layout.addWidget(character_profile_view)

    # 滚动区域包裹
    scroll_area = QScrollArea()
    scroll_area.setWidgetResizable(True)
    scroll_area.setWidget(detail_container)
    scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
    body_layout.addWidget(scroll_area, 7)

    # 空状态提示
    content = QWidget(container)
    content.setObjectName("viewContent")
    content_layout = QGridLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(0)
    content_layout.addWidget(body, 0, 0)

    character_empty = create_empty_state("暂无角色数据，请先导入资源并解密 Lua", content)
    character_empty.setVisible(False)
    character_empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    content_layout.addWidget(character_empty, 0, 0)
    main_layout.addWidget(content, 1)

    controls = {
        "character_title": character_title,
        "character_search": character_search,
        "character_table": character_table,
        "character_detail": character_detail,
        "character_profile_view": character_profile_view,
        "character_status": character_status,
        "character_empty": character_empty,
    }
    return container, controls
