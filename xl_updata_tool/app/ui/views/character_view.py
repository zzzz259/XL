# -*- coding: utf-8 -*-
"""模块职责：角色数据视图构建器

创建角色数据视图容器（标题栏 + 搜索栏 + 角色表格 + 详情卡片 + 空状态提示），
返回 (container, controls_dict)，其中 controls_dict 提供外部操作所需的控件引用。
信号通过 parent 回调连接到主窗口。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QLineEdit, QAbstractItemView, QFrame, QScrollArea,
    QHeaderView,
)

from app.ui.theme import (
    get_color,
)


def create_character_view(parent=None):
    """创建角色数据视图容器，返回 (container, controls_dict)。

    controls_dict 包含外部需要操作的控件：
      character_title, character_search, character_table, character_detail,
      character_detail_name, character_detail_info, character_status,
      character_empty
    """
    container = QWidget()
    container.setStyleSheet(f"background-color:{get_color('BG_DARK')};")
    main_layout = QVBoxLayout(container)
    main_layout.setContentsMargins(0, 0, 0, 0)
    main_layout.setSpacing(0)

    # 顶部栏：标题
    top_bar = QFrame()
    top_bar.setFixedHeight(50)
    top_bar.setStyleSheet(f"QFrame {{ background-color:{get_color('BG_SURFACE')}; border-bottom:1px solid {get_color('BORDER')}; }}")
    top_layout = QHBoxLayout(top_bar)
    top_layout.setContentsMargins(16, 0, 16, 0)

    character_title = QLabel("角色数据")
    character_title.setStyleSheet(
        f"color:{get_color('TEXT_PRIMARY')}; font-size:16px; font-weight:bold; background:transparent; border:none;"
    )
    top_layout.addWidget(character_title)
    top_layout.addStretch()
    main_layout.addWidget(top_bar)

    # 搜索与控制栏
    ctrl_bar = QFrame()
    ctrl_bar.setFixedHeight(50)
    ctrl_bar.setStyleSheet(f"QFrame {{ background-color:{get_color('BG_ELEVATED')}; border-bottom:1px solid {get_color('BORDER')}; }}")
    ctrl_layout = QHBoxLayout(ctrl_bar)
    ctrl_layout.setContentsMargins(16, 0, 16, 0)

    btn_parse = QPushButton("🔍 开始解析")
    btn_parse.setFixedSize(110, 32)
    btn_parse.setStyleSheet(f"""
        QPushButton {{ background-color:{get_color('ACCENT')}; border:none; border-radius:6px;
                      color:#fff; font-size:12px; font-weight:600; }}
        QPushButton:hover {{ background-color:{get_color('ACCENT_HOVER')}; }}
        QPushButton:pressed {{ background-color:{get_color('ACCENT')}; }}
    """)
    if parent is not None:
        btn_parse.clicked.connect(parent._manual_load_character)
    ctrl_layout.addWidget(btn_parse)

    character_search = QLineEdit()
    character_search.setPlaceholderText("搜索角色名称...")
    character_search.setFixedWidth(250)
    character_search.setFixedHeight(32)
    character_search.setStyleSheet(f"""
        QLineEdit {{ background-color:{get_color('BG_DARK')}; border:1px solid {get_color('BORDER')}; border-radius:6px;
                    padding:4px 12px; color:{get_color('TEXT_PRIMARY')}; font-size:13px; }}
        QLineEdit:focus {{ border-color:{get_color('ACCENT')}; }}
    """)
    if parent is not None:
        character_search.textChanged.connect(parent._filter_character_table)
    ctrl_layout.addWidget(character_search)

    btn_refresh = QPushButton("刷新列表")
    btn_refresh.setFixedSize(100, 32)
    btn_refresh.setStyleSheet(f"""
        QPushButton {{ background-color:{get_color('INFO')}; border:none; border-radius:6px;
                      color:#fff; font-size:12px; font-weight:600; }}
        QPushButton:hover {{ background-color:#93c5fd; }}
        QPushButton:pressed {{ background-color:{get_color('INFO')}; }}
    """)
    if parent is not None:
        btn_refresh.clicked.connect(parent._refresh_character_list)
    ctrl_layout.addWidget(btn_refresh)

    btn_csv = QPushButton("下载CSV")
    btn_csv.setFixedSize(100, 32)
    btn_csv.setStyleSheet(f"""
        QPushButton {{ background-color:{get_color('INFO')}; border:none; border-radius:6px;
                      color:#fff; font-size:12px; font-weight:600; }}
        QPushButton:hover {{ background-color:#93c5fd; }}
        QPushButton:pressed {{ background-color:{get_color('INFO')}; }}
    """)
    if parent is not None:
        btn_csv.clicked.connect(parent._export_characters_csv)
    ctrl_layout.addWidget(btn_csv)

    character_status = QLabel("")
    character_status.setStyleSheet(f"color:{get_color('TEXT_SECONDARY')}; font-size:12px; background:transparent; padding-left:16px;")
    ctrl_layout.addWidget(character_status)
    ctrl_layout.addStretch()
    main_layout.addWidget(ctrl_bar)

    # 主体内容：左右布局
    body = QWidget()
    body_layout = QHBoxLayout(body)
    body_layout.setContentsMargins(8, 8, 8, 8)
    body_layout.setSpacing(8)

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
    character_table.verticalHeader().setDefaultSectionSize(36)
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
    detail_container.setStyleSheet(f"background-color:{get_color('BG_ELEVATED')}; border:1px solid {get_color('BORDER')}; border-radius:8px;")
    character_detail = detail_container
    detail_layout = QVBoxLayout(detail_container)
    detail_layout.setContentsMargins(16, 16, 16, 16)
    detail_layout.setSpacing(8)

    character_detail_name = QLabel("请选择一个角色")
    character_detail_name.setStyleSheet(f"color:{get_color('TEXT_PRIMARY')}; font-size:18px; font-weight:bold; background:transparent;")
    detail_layout.addWidget(character_detail_name)

    character_detail_info = QLabel("")
    character_detail_info.setWordWrap(True)
    character_detail_info.setStyleSheet(f"color:{get_color('TEXT_SECONDARY')}; font-size:13px; background:transparent;")
    detail_layout.addWidget(character_detail_info)

    detail_layout.addStretch()

    # 滚动区域包裹
    scroll_area = QScrollArea()
    scroll_area.setWidgetResizable(True)
    scroll_area.setWidget(detail_container)
    scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
    body_layout.addWidget(scroll_area, 7)

    main_layout.addWidget(body, 1)

    # 空状态提示
    character_empty = QLabel("暂无角色数据，请先导入资源并解密 Lua")
    character_empty.setAlignment(Qt.AlignCenter)
    character_empty.setStyleSheet(f"color:{get_color('TEXT_MUTED')}; font-size:16px; background:transparent;")
    character_empty.setVisible(False)
    main_layout.addWidget(character_empty)

    controls = {
        "character_title": character_title,
        "character_search": character_search,
        "character_table": character_table,
        "character_detail": character_detail,
        "character_detail_name": character_detail_name,
        "character_detail_info": character_detail_info,
        "character_status": character_status,
        "character_empty": character_empty,
    }
    return container, controls