THUMB_SIZE = 150
ACCENT = "#8b5cf6"
ACCENT_HOVER = "#a78bfa"
BG_DARK = "#0f0f1a"
BG_SURFACE = "#1a1a2e"
BG_ELEVATED = "#252540"
BG_HOVER = "#2d2d4a"
BORDER = "#35355a"
TEXT_PRIMARY = "#e8e8f0"
TEXT_SECONDARY = "#9898b8"
TEXT_MUTED = "#686890"
SUCCESS = "#34d399"
WARNING = "#fbbf24"
DANGER = "#f87171"
INFO = "#60a5fa"
ROW_ALT = "#1e1e35"
PROGRESS_BG = "#252540"
PROGRESS_FILL = "#8b5cf6"

BASE_STYLESHEET = f"""
QMainWindow {{
    background-color: {BG_DARK};
}}
QWidget {{
    background-color: transparent;
    color: {TEXT_PRIMARY};
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13px;
}}
QMenuBar {{
    background-color: {BG_SURFACE};
    border-bottom: 1px solid {BORDER};
    padding: 4px 8px;
    font-size: 13px;
}}
QMenuBar::item:selected {{
    background-color: {BG_HOVER};
    border-radius: 4px;
}}
QMenu {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    padding: 8px 32px 8px 16px;
    border-radius: 4px;
    margin: 2px 4px;
}}
QMenu::item:selected {{
    background-color: {ACCENT};
}}
QToolBar {{
    background-color: {BG_SURFACE};
    border-bottom: 1px solid {BORDER};
    padding: 4px;
    spacing: 6px;
}}
QToolButton {{
    background-color: transparent;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 500;
}}
QToolButton:hover {{
    background-color: {BG_HOVER};
}}
QToolButton:pressed {{
    background-color: {ACCENT};
}}
QToolButton:checked {{
    background-color: {ACCENT};
    color: white;
    font-weight: 600;
}}
QToolButton[accent="true"] {{
    background-color: {ACCENT};
    color: white;
    font-weight: 600;
}}
QToolButton[accent="true"]:hover {{
    background-color: {ACCENT_HOVER};
}}
QTreeView, QTableView, QListView {{
    background-color: {BG_SURFACE};
    alternate-background-color: {ROW_ALT};
    border: none;
    border-radius: 8px;
    gridline-color: {BORDER};
    outline: none;
    selection-background-color: {ACCENT};
    selection-color: white;
    font-size: 13px;
}}
QTreeView::item, QTableView::item, QListView::item {{
    padding: 6px 10px;
    border: none;
}}
QTreeView::item:hover, QTableView::item:hover, QListView::item:hover {{
    background-color: {BG_HOVER};
}}
QHeaderView::section {{
    background-color: {BG_ELEVATED};
    padding: 10px 12px;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 2px solid {BORDER};
    font-weight: 600;
    font-size: 12px;
    color: {TEXT_SECONDARY};
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QHeaderView::section:hover {{
    background-color: {BG_HOVER};
    color: {TEXT_PRIMARY};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 40px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_MUTED};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 4px;
    min-width: 40px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {TEXT_MUTED};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QLabel[heading="true"] {{
    font-size: 16px;
    font-weight: 700;
    color: {TEXT_PRIMARY};
    padding: 4px 0;
}}
QLabel[subheading="true"] {{
    font-size: 12px;
    font-weight: 500;
    color: {TEXT_SECONDARY};
    text-transform: uppercase;
    letter-spacing: 1px;
    padding: 4px 0;
}}
QLineEdit {{
    background-color: {BG_ELEVATED};
    border: 1.5px solid {BORDER};
    border-radius: 8px;
    padding: 10px 14px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit::placeholder {{
    color: {TEXT_MUTED};
}}
QComboBox {{
    background-color: {BG_ELEVATED};
    border: 1.5px solid {BORDER};
    border-radius: 8px;
    padding: 10px 14px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
    min-width: 120px;
}}
QComboBox:hover {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    selection-background-color: {ACCENT};
    padding: 4px;
}}
QPushButton {{
    background-color: {BG_ELEVATED};
    border: 1.5px solid {BORDER};
    border-radius: 8px;
    padding: 10px 20px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {BG_HOVER};
    border-color: {TEXT_MUTED};
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: white;
}}
QPushButton[accent="true"] {{
    background-color: {ACCENT};
    border: none;
    color: white;
    font-weight: 600;
}}
QPushButton[accent="true"]:hover {{
    background-color: {ACCENT_HOVER};
}}
QProgressBar {{
    background-color: {PROGRESS_BG};
    border: none;
    border-radius: 6px;
    height: 8px;
    text-align: center;
    font-size: 11px;
    color: {TEXT_SECONDARY};
}}
QProgressBar::chunk {{
    background-color: {PROGRESS_FILL};
    border-radius: 6px;
}}
QTabWidget::pane {{
    background-color: {BG_SURFACE};
    border: none;
    border-radius: 8px;
}}
QTabBar::tab {{
    background-color: transparent;
    padding: 10px 20px;
    border: none;
    border-bottom: 2px solid transparent;
    color: {TEXT_SECONDARY};
    font-size: 13px;
    font-weight: 500;
    margin-right: 4px;
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
    background-color: {BG_HOVER};
    border-radius: 6px;
}}
QSplitter::handle {{
    background-color: {BORDER};
    width: 1px;
}}
QStatusBar {{
    background-color: {BG_SURFACE};
    border-top: 1px solid {BORDER};
    padding: 4px 12px;
    color: {TEXT_SECONDARY};
    font-size: 14px;
}}
QToolTip {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 12px;
    color: {TEXT_PRIMARY};
    font-size: 12px;
}}
QGroupBox {{
    font-weight: 600;
    border: 1.5px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    padding: 20px 16px 16px 16px;
    color: {TEXT_PRIMARY};
}}
QCheckBox, QRadioButton {{
    color: {TEXT_PRIMARY};
    spacing: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 8px;
    color: {ACCENT};
}}
"""


# ============ 新主题（蓝灰专业深色，参考 QDarkStyleSheet）============

NEW_PALETTE = {
    "ACCENT": "#259ae9",
    "ACCENT_HOVER": "#4fb3f7",
    "ACCENT_2": "#2dd4bf",
    "BG_DARK": "#19232d",
    "BG_SURFACE": "#1f2c3a",
    "BG_ELEVATED": "#293544",
    "BG_HOVER": "#37414f",
    "BORDER": "#455364",
    "TEXT_PRIMARY": "#dfe1e2",
    "TEXT_SECONDARY": "#acb1b6",
    "TEXT_MUTED": "#9da9b5",
}

# 旧颜色值 -> 新颜色值（复用 BASE_STYLESHEET 模板，替换颜色）
_REPLACEMENTS = {
    "#8b5cf6": "#259ae9",  # ACCENT / PROGRESS_FILL
    "#a78bfa": "#4fb3f7",  # ACCENT_HOVER
    "#0f0f1a": "#19232d",  # BG_DARK
    "#1a1a2e": "#1f2c3a",  # BG_SURFACE
    "#252540": "#293544",  # BG_ELEVATED / PROGRESS_BG
    "#2d2d4a": "#37414f",  # BG_HOVER
    "#35355a": "#455364",  # BORDER
    "#9898b8": "#acb1b6",  # TEXT_SECONDARY
    "#686890": "#9da9b5",  # TEXT_MUTED
    "#1e1e35": "#1f2c3a",  # ROW_ALT
}

# 新主题追加的控件样式（覆盖 BASE_STYLESHEET，双强调色紫主粉辅）
_NEW_CONTROL_STYLES = """
QCheckBox {{ color: {TEXT_PRIMARY}; spacing: 6px; }}
QRadioButton {{ color: {TEXT_PRIMARY}; spacing: 6px; }}
QLineEdit {{
    background-color: {BG_ELEVATED};
    border: 1.5px solid {BORDER};
    border-radius: 6px;
    padding: 10px 14px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_2};
}}
QLineEdit:hover {{ border-color: {TEXT_MUTED}; }}
QLineEdit:focus {{ border-color: {ACCENT_2}; }}
QComboBox {{
    background-color: {BG_ELEVATED};
    border: 1.5px solid {BORDER};
    border-radius: 6px;
    padding: 10px 14px;
    color: {TEXT_PRIMARY};
}}
QComboBox:hover {{ border-color: {TEXT_MUTED}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {TEXT_SECONDARY};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
}}
QTreeView, QTableView, QListView {{
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT_2};
}}
"""


def _build_stylesheet(p, replacements):
    """复用 BASE_STYLESHEET 模板替换颜色 + 追加新控件样式，生成新主题 QSS"""
    qss = BASE_STYLESHEET
    for old, new in replacements.items():
        qss = qss.replace(old, new)
    qss += _NEW_CONTROL_STYLES.format(
        ACCENT=p["ACCENT"], ACCENT_2=p["ACCENT_2"], BORDER=p["BORDER"],
        BG_ELEVATED=p["BG_ELEVATED"], TEXT_PRIMARY=p["TEXT_PRIMARY"],
        TEXT_MUTED=p["TEXT_MUTED"], TEXT_SECONDARY=p["TEXT_SECONDARY"],
    )
    return qss


OLD_STYLESHEET = BASE_STYLESHEET
NEW_STYLESHEET = _build_stylesheet(NEW_PALETTE, _REPLACEMENTS)

# 浅色主题（参考 QDarkStyleSheet light）
LIGHT_PALETTE = {
    "ACCENT": "#1a72bb",
    "ACCENT_HOVER": "#259ae9",
    "ACCENT_2": "#2dd4bf",
    "BG_DARK": "#f5f5f5",
    "BG_SURFACE": "#ffffff",
    "BG_ELEVATED": "#ececec",
    "BG_HOVER": "#e0e0e0",
    "BORDER": "#c8c8c8",
    "TEXT_PRIMARY": "#19232d",
    "TEXT_SECONDARY": "#455364",
    "TEXT_MUTED": "#788d9c",
}

_LIGHT_REPLACEMENTS = {
    "#8b5cf6": "#1a72bb",  # ACCENT / PROGRESS_FILL
    "#a78bfa": "#259ae9",  # ACCENT_HOVER
    "#e8e8f0": "#19232d",  # TEXT_PRIMARY（浅→深）
    "#0f0f1a": "#f5f5f5",  # BG_DARK
    "#1a1a2e": "#ffffff",  # BG_SURFACE
    "#252540": "#ececec",  # BG_ELEVATED / PROGRESS_BG
    "#2d2d4a": "#e0e0e0",  # BG_HOVER
    "#35355a": "#c8c8c8",  # BORDER
    "#9898b8": "#455364",  # TEXT_SECONDARY
    "#686890": "#788d9c",  # TEXT_MUTED
    "#1e1e35": "#e8e8e8",  # ROW_ALT
}

LIGHT_STYLESHEET = _build_stylesheet(LIGHT_PALETTE, _LIGHT_REPLACEMENTS)


def apply_theme(app, name):
    """应用主题（"old" / "new" / "light"），立即生效"""
    if name == "new":
        app.setStyleSheet(NEW_STYLESHEET)
    elif name == "light":
        app.setStyleSheet(LIGHT_STYLESHEET)
    else:
        app.setStyleSheet(OLD_STYLESHEET)
