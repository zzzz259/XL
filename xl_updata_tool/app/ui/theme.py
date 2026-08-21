import os
import tempfile

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPixmap

THUMB_SIZE = 150
# XL 正式视觉基线：克制的蓝灰深色工作台。
# 这些名称保留为兼容别的 UI 模块的语义别名；新代码优先使用 get_color()。
ACCENT = "#4f9bd6"
ACCENT_HOVER = "#67afe2"
BG_DARK = "#18232e"
BG_SURFACE = "#1f2c38"
BG_ELEVATED = "#293947"
BG_HOVER = "#304553"
BORDER = "#3d4e5d"
TEXT_PRIMARY = "#e5ebf0"
TEXT_SECONDARY = "#aab7c3"
TEXT_MUTED = "#8193a3"
SUCCESS = "#62b892"
WARNING = "#d8ab5f"
DANGER = "#d97777"
INFO = "#6c9ed2"
ROW_ALT = "#20303d"
PROGRESS_BG = "#293947"
PROGRESS_FILL = "#4f9bd6"

BASE_STYLESHEET = f"""
QMainWindow {{
    background-color: {BG_DARK};
}}
QWidget#viewContainer, QWidget#viewContent {{
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
QMenu#contextMenu::separator {{
    height: 1px;
    background-color: {BORDER};
    margin: 4px 8px;
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
QListWidget#previewImageList {{
    background-color: {BG_DARK};
    border: none;
    padding: 10px;
}}
QListWidget#previewImageList::item {{
    border-radius: 6px;
    padding: 4px;
}}
QListWidget#previewImageList::item:hover,
QListWidget#previewImageList::item:selected {{
    background-color: {BG_ELEVATED};
}}
QProgressBar#previewProgress {{
    background-color: {BG_DARK};
    border: none;
    border-radius: 4px;
    text-align: center;
    color: {TEXT_PRIMARY};
    font-size: 12px;
}}
QProgressBar#previewProgress::chunk {{
    background-color: {SUCCESS};
    border-radius: 4px;
}}
QTreeWidget#audioTree {{
    background-color: {BG_DARK};
    border: none;
}}
QTreeWidget#audioTree::item {{
    padding: 6px 8px;
    font-size: 13px;
}}
QTreeWidget#audioTree::item:selected {{
    background-color: transparent;
}}
QLabel#audioNowPlaying {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
    min-width: 160px;
}}
QLabel#audioPosition {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}
QPushButton#audioPlayButton {{
    padding: 4px 10px;
}}
QSlider#audioProgressSlider::groove:horizontal,
QSlider#audioVolumeSlider::groove:horizontal {{
    background: {BG_ELEVATED};
    height: 4px;
    border-radius: 2px;
}}
QSlider#audioProgressSlider::handle:horizontal {{
    background: {ACCENT};
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider#audioVolumeSlider::handle:horizontal {{
    background: {ACCENT};
    width: 10px;
    margin: -3px 0;
    border-radius: 5px;
}}
QSlider#audioProgressSlider::sub-page:horizontal,
QSlider#audioVolumeSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QTableWidget#workspaceTable {{
    background-color: {BG_DARK};
    border: none;
    gridline-color: {BORDER};
}}
QTableWidget#workspaceTable::item {{
    padding: 10px 12px;
    font-size: 13px;
}}
QDialog#imageViewerDialog,
QDialog#characterSelectDialog,
QDialog#exportSettingsDialog {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
}}
QFrame#imageViewerHeader,
QFrame#imageViewerFooter {{
    background-color: {BG_SURFACE};
    border-color: {BORDER};
}}
QLabel#imageViewerFilename {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 600;
}}
QGraphicsView#imageCanvas {{
    background-color: {BG_DARK};
    border: none;
}}
QLabel#imageViewerInfo {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
}}
QPushButton#dialogCloseButton {{
    background-color: transparent;
    border: none;
    color: {TEXT_SECONDARY};
    font-weight: 600;
}}
QPushButton#dialogCloseButton:hover {{
    background-color: {DANGER};
    color: #ffffff;
}}
QPushButton#imagePreviousButton,
QPushButton#imageNextButton {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT_PRIMARY};
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#imagePreviousButton:hover,
QPushButton#imageNextButton:hover {{
    border-color: {ACCENT};
}}
QPushButton#imagePreviousButton:disabled,
QPushButton#imageNextButton:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}
QLabel#dialogTitle {{
    color: {ACCENT};
    font-size: 16px;
    font-weight: 700;
}}
QLabel#dialogFieldLabel {{
    color: {TEXT_SECONDARY};
    font-weight: 600;
}}
QLabel#fileLabel {{
    background-color: {BG_DARK};
    border: 1px solid {BORDER};
    border-radius: 4px;
    color: {TEXT_SECONDARY};
    font-size: 11px;
    padding: 6px;
}}
QScrollArea#characterSelectScroll {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QWidget#characterSelectContainer {{
    background-color: {BG_SURFACE};
}}
QCheckBox#characterOption {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
    spacing: 6px;
}}
QPushButton#selectAllButton,
QPushButton#clearAllButton,
QPushButton#dialogCancelButton {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT_SECONDARY};
}}
QPushButton#selectAllButton:hover,
QPushButton#clearAllButton:hover,
QPushButton#dialogCancelButton:hover {{
    border-color: {ACCENT};
    color: {TEXT_PRIMARY};
}}
QLabel#dialogEmptyState {{
    color: {TEXT_MUTED};
    padding: 24px;
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
    font-size: 12px;
}}
QFrame#pageHeader {{
    background-color: {BG_SURFACE};
    border-bottom: 1px solid {BORDER};
}}
QLabel#pageTitle {{
    color: {TEXT_PRIMARY};
    font-size: 16px;
    font-weight: 700;
    padding: 0;
}}
QFrame#pageCommandBar {{
    background-color: {BG_ELEVATED};
    border-bottom: 1px solid {BORDER};
}}
QLabel#pageStatus {{
    background-color: {BG_SURFACE};
    border-top: 1px solid {BORDER};
    color: {TEXT_SECONDARY};
    font-size: 12px;
    padding: 4px 16px;
}}
QLabel#emptyState {{
    color: {TEXT_MUTED};
    font-size: 15px;
    padding: 32px;
    background-color: transparent;
}}
QLabel#detailEmptyState {{
    color: {TEXT_MUTED};
    font-size: 15px;
    padding: 32px;
    background-color: transparent;
}}
QFrame#profileHeader {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QLabel#profileName {{
    color: {TEXT_PRIMARY};
    font-size: 21px;
    font-weight: 700;
}}
QLabel#profileId {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel#profileTag {{
    background-color: {BG_HOVER};
    border: 1px solid {BORDER};
    border-radius: 4px;
    color: {TEXT_SECONDARY};
    font-size: 12px;
    padding: 4px 8px;
}}
QFrame#profileSection {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QLabel#profileSectionTitle {{
    color: {ACCENT};
    font-size: 14px;
    font-weight: 700;
    padding-bottom: 2px;
}}
QFrame#statTile {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
QLabel#statLabel, QLabel#profileFieldLabel {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel#statValue, QLabel#profileFieldValue {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 600;
}}
QFrame#skillCard {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-left: 3px solid {ACCENT};
    border-radius: 6px;
}}
QLabel#skillTitle {{
    color: {TEXT_PRIMARY};
    font-weight: 700;
}}
QLabel#skillDescription, QLabel#profileBodyText {{
    color: {TEXT_SECONDARY};
    font-size: 13px;
    line-height: 1.4;
}}
QPushButton#numberHighlightButton {{
    padding: 6px 12px;
    border-color: {DANGER};
    color: {DANGER};
}}
QPushButton#numberHighlightButton:checked {{
    background-color: {DANGER};
    border-color: {DANGER};
    color: #ffffff;
}}
QFrame#pagePlayerBar {{
    background-color: {BG_SURFACE};
    border-top: 1px solid {BORDER};
}}
QFrame#workspaceHeader {{
    background-color: {BG_DARK};
    border-bottom: 1px solid {BORDER};
}}
QLabel#workspaceTitle {{
    color: {TEXT_PRIMARY};
    font-size: 18px;
    font-weight: 700;
}}
QLabel#workspaceDescription {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel#workspaceSummary {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
    padding: 6px 10px;
    border: 1px solid {BORDER};
    border-radius: 6px;
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
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 2px solid {TEXT_MUTED};
    border-radius: 4px;
    background: {BG_ELEVATED};
}}
QCheckBox::indicator:hover {{ border-color: {TEXT_PRIMARY}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: url({{CHECK}});
}}
QRadioButton::indicator {{
    width: 18px; height: 18px;
    border: 2px solid {TEXT_MUTED};
    border-radius: 9px;
    background: {BG_ELEVATED};
}}
QRadioButton::indicator:hover {{ border-color: {TEXT_PRIMARY}; }}
QRadioButton::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: url({{CHECK}});
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
    "ACCENT": ACCENT,
    "ACCENT_HOVER": ACCENT_HOVER,
    "ACCENT_2": "#6eabc9",
    "BG_DARK": BG_DARK,
    "BG_SURFACE": BG_SURFACE,
    "BG_ELEVATED": BG_ELEVATED,
    "BG_HOVER": BG_HOVER,
    "ROW_ALT": ROW_ALT,
    "BORDER": BORDER,
    "TEXT_PRIMARY": TEXT_PRIMARY,
    "TEXT_SECONDARY": TEXT_SECONDARY,
    "TEXT_MUTED": TEXT_MUTED,
}

# 旧颜色值 -> 新颜色值（复用 BASE_STYLESHEET 模板，替换颜色）
_REPLACEMENTS = {
    "#8b5cf6": ACCENT,
    "#a78bfa": ACCENT_HOVER,
    "#0f0f1a": BG_DARK,
    "#1a1a2e": BG_SURFACE,
    "#252540": BG_ELEVATED,
    "#2d2d4a": BG_HOVER,
    "#35355a": BORDER,
    "#9898b8": TEXT_SECONDARY,
    "#686890": TEXT_MUTED,
    "#1e1e35": ROW_ALT,
}

# 正式主题追加的控件状态样式。
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
    border-bottom: 2px solid {ACCENT};
}}
QPushButton[fluentAppearance="primary"] {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton[fluentAppearance="primary"]:hover {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton[fluentAppearance="danger"] {{
    background-color: transparent;
    border-color: {DANGER};
    color: {DANGER};
}}
QPushButton[fluentAppearance="danger"]:hover {{
    background-color: {DANGER};
    color: #ffffff;
}}
QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus {{
    border-color: {ACCENT_HOVER};
}}
QPushButton:disabled, QToolButton:disabled, QComboBox:disabled, QLineEdit:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}
"""


def _build_stylesheet(p, replacements):
    """复用 BASE_STYLESHEET 模板替换颜色 + 追加新控件样式，生成新主题 QSS"""
    qss = BASE_STYLESHEET
    for old, new in replacements.items():
        qss = qss.replace(old, new)
    qss += _NEW_CONTROL_STYLES.format(
        ACCENT=p["ACCENT"], ACCENT_HOVER=p["ACCENT_HOVER"], ACCENT_2=p["ACCENT_2"], BORDER=p["BORDER"],
        DANGER=DANGER,
        BG_ELEVATED=p["BG_ELEVATED"], TEXT_PRIMARY=p["TEXT_PRIMARY"],
        TEXT_MUTED=p["TEXT_MUTED"], TEXT_SECONDARY=p["TEXT_SECONDARY"],
    )
    return qss


OLD_STYLESHEET = BASE_STYLESHEET
NEW_STYLESHEET = _build_stylesheet(NEW_PALETTE, _REPLACEMENTS)

# 浅色主题保留为历史兼容数据，不再作为正式 UI 选项。
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


# 旧深色主题 palette（供 get_color 动态读取，含语义色）
OLD_PALETTE = {
    "ACCENT": "#8b5cf6",
    "ACCENT_HOVER": "#a78bfa",
    "ACCENT_2": "#8b5cf6",
    "BG_DARK": "#0f0f1a",
    "BG_SURFACE": "#1a1a2e",
    "BG_ELEVATED": "#252540",
    "BG_HOVER": "#2d2d4a",
    "BORDER": "#35355a",
    "TEXT_PRIMARY": "#e8e8f0",
    "TEXT_SECONDARY": "#9898b8",
    "TEXT_MUTED": "#686890",
}

# 语义色（三主题通用，不随主题变）
_SEMANTIC = {
    "SUCCESS": "#34d399",
    "WARNING": "#fbbf24",
    "DANGER": "#f87171",
    "INFO": "#60a5fa",
}

_current_palette = NEW_PALETTE

FORMAL_THEME = "new"
THEME_LABEL = "蓝灰深色"


def normalize_theme_name(name):
    """将旧配置映射到当前正式主题，避免历史设置导致启动失败。"""
    return FORMAL_THEME


def get_color(key):
    """返回当前主题的 palette 颜色（供各 view 的 inline 样式动态读取）"""
    if key in _SEMANTIC:
        return _SEMANTIC[key]
    return _current_palette.get(key, "")


_checkmark_url = None


def _checkmark_path():
    """生成并缓存白色对勾 PNG，返回 QSS url() 可用的正斜杠路径"""
    global _checkmark_url
    if _checkmark_url is not None:
        return _checkmark_url
    path = os.path.join(tempfile.gettempdir(), "xl_checkmark.png")
    if not os.path.exists(path):
        pm = QPixmap(18, 18)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor("#ffffff"))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawLine(QPoint(4, 9), QPoint(8, 13))
        p.drawLine(QPoint(8, 13), QPoint(14, 5))
        p.end()
        pm.save(path, "PNG")
    _checkmark_url = path.replace("\\", "/")
    return _checkmark_url


def _build_qpalette(palette):
    """为 Qt 原生控件提供与正式 QSS 一致的基础色板。"""
    qpalette = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup
    active = {
        role.Window: palette["BG_DARK"],
        role.WindowText: palette["TEXT_PRIMARY"],
        role.Base: palette["BG_DARK"],
        role.AlternateBase: palette["ROW_ALT"],
        role.Text: palette["TEXT_PRIMARY"],
        role.Button: palette["BG_ELEVATED"],
        role.ButtonText: palette["TEXT_PRIMARY"],
        role.Highlight: palette["ACCENT"],
        role.HighlightedText: "#ffffff",
        role.ToolTipBase: palette["BG_ELEVATED"],
        role.ToolTipText: palette["TEXT_PRIMARY"],
    }
    for color_role, value in active.items():
        qcolor = QColor(value)
        qpalette.setColor(group.Active, color_role, qcolor)
        qpalette.setColor(group.Inactive, color_role, qcolor)
    for color_role in (role.WindowText, role.Text, role.ButtonText):
        qpalette.setColor(group.Disabled, color_role, QColor(palette["TEXT_MUTED"]))
    qpalette.setColor(group.Disabled, role.Window, QColor(palette["BG_DARK"]))
    qpalette.setColor(group.Disabled, role.Base, QColor(palette["BG_SURFACE"]))
    qpalette.setColor(group.Disabled, role.Button, QColor(palette["BG_SURFACE"]))
    return qpalette


def apply_theme(app, name):
    """应用正式蓝灰主题；old/light 名称仅作为历史配置兼容入口。"""
    global _current_palette
    check = _checkmark_path()
    _current_palette = NEW_PALETTE
    app.setPalette(_build_qpalette(NEW_PALETTE))
    app.setStyleSheet(NEW_STYLESHEET.replace("{CHECK}", check))
