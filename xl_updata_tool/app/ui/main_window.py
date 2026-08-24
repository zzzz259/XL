import os
import shutil
import subprocess
import sys

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QMessageBox, QToolBar, QStatusBar, QApplication,
    QTableWidgetItem, QToolButton,
    QFileDialog, QCheckBox, QMenu, QComboBox, QProgressDialog,
    QDialog, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer, QMimeData, QUrl, QSettings, QEvent
from PySide6.QtGui import QColor, QBrush

try:
    import qtawesome as qta
    QT_AWESOME_AVAILABLE = True
except ImportError:
    qta = None
    QT_AWESOME_AVAILABLE = False

from .theme import (
    ACCENT, TEXT_PRIMARY, TEXT_MUTED, SUCCESS, WARNING, DANGER, INFO,
    FORMAL_THEME, THEME_LABEL, apply_theme, get_color, normalize_theme_name,
)
from .panels import ticks_to_date
from app.core.version_manager import VersionManager
from .workers.download import CheckUpdateThread, DownloadWorker
from app.core.version_data import compute_download_hashes, compute_version_delta_map
from app.core.local_bundle_sync import sync_local_bundles
from app.core.character_cache import (
    derive_character_index,
    load_cache as load_character_cache_file,
    save_cache as save_character_cache_file,
    source_mtime,
)
from app.core.character_repository import (
    clear_all_unread as clear_all_character_unread,
    clear_unread as clear_character_unread,
    current_characters as repository_characters,
    load_repository as load_character_repository,
    merge_snapshot as merge_character_snapshot,
    repository_path as character_repository_path,
    unread_status as character_unread_status,
)
from app.core.bundle_selector import (
    audio_assets_map_path,
    lua_assets_map_path,
    select_audio_bundles,
    select_lua_bundles,
)
from app.core.character_presenter import export_characters_csv
from app.core.character_profile import build_character_profile
from app.core.audio_repository import unread_files as audio_unread_files
from app.core.preview_catalog import build_skel_map, scan_cardspine_roles, scan_preview_roles
from app.core.seed_versions import seed_bundled_versions
from app.core.version_cleanup import count_downloaded_bundles, delete_downloaded_bundles
from app.core.version_update import append_changelog, record_downloaded_bundle, register_checked_version
from app.core.version_download import calculate_missing_downloads
from app.core import database as db
from app.core.logger import logger, timed
from app.core.path_utils import get_data_dir, get_base_dir, get_tools_dir
from app.core.lua_repository import (
    has_character_sources,
    latest_lua_version,
    should_auto_parse,
    version_directory,
)

# 拆分后的模块导入
from .dialogs.image_viewer import ImageViewerDialog
from .workers.image_loader import ImageLoadWorker
from .workers.preview_export import PreviewExportWorker
from .workers.import_as import ImportASWorker
from .adapters.spine_adapter import extract_skin_name_from_png, is_composite_png
from .views.preview_view import create_preview_view
from app.features.audio.page import AudioPage
from app.features.audio.controller import AudioController
from .views.character_view import create_character_view
from .views.version_view import create_version_header, create_version_table
from .features.export_controller import (
    export_composite_video,
    export_with_dialog,
    batch_export_with_dialog,
)
from .features.preview_controller import build_preview_item
from app.core.character_loader import load_character_data

DATA_DIR = get_data_dir()
BUNDLES_DIR = os.path.join(DATA_DIR, "bundles")
LUA_OUTPUT_DIR = os.path.join(get_base_dir(), "output", "lua")
CHARACTER_DATA_DIR = os.path.join(get_base_dir(), "output", "character_data")
CHARACTER_REPOSITORY_PATH = character_repository_path(CHARACTER_DATA_DIR)
# 角色数据解析依赖的源文件（用于缓存失效判断）
CHARACTER_SOURCE_FILES = [
    "BaseWord_cn.lua", "BaseCvNameCn.lua", "BaseCardLevelUp.lua",
    "BaseCardQualityUp.lua", "BaseSkill.lua", "BaseSkillLevelUp.lua",
    "BaseBadgeSuitGroup.lua", "BaseItem.lua", "BaseCard.lua",
]


class MainWindow(QMainWindow):
    def __init__(self, debug_mode=False):
        super().__init__()
        self.debug_mode = debug_mode
        self.setWindowTitle("XL 更新管理工具")
        self.resize(1200, 700)
        self.setMinimumSize(900, 500)
        self.showMaximized()
        # 读取历史主题偏好并统一迁移到正式蓝灰主题。
        settings = QSettings("XL", "xl_updata_tool")
        _theme = normalize_theme_name(settings.value("theme", FORMAL_THEME))
        settings.setValue("theme", _theme)
        apply_theme(self, _theme)
        self.version_mgr = VersionManager()
        # 后台工作线程实例（避免 AttributeError）
        self._preview_worker = None
        self._batch_worker = None
        self._composite_worker = None
        self._image_worker = None
        self._import_worker = None
        self._pending_import_message = None
        self._show_character = False
        self._character_data_loaded = False
        self._character_loading = False
        self.character_unread = {}
        self.character_source_version = None
        self.character_base = []      # 角色基础信息：{raw_id, name, display_index}
        self.characters = []           # 角色完整数据：{name, element_type, max_hp, atk, def, skills, raw_text}
        self.characters_full = {}      # 角色完整数据字典：char_id -> 完整数据
        self.word_map = {}            # BaseWord_cn.lua 中的文本映射：id->文本
        self._skel_map = {}
        self._init_db()
        logger.info(f"当前 DATA_DIR: {DATA_DIR}")
        self._seed_bundled_version()
        self._setup_ui()
        self._load_data()
        self._check_auto()

    def _init_db(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        db.init_db(os.path.join(DATA_DIR, "xl_updata.db"))

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.view_toolbar = self._view_toolbar()
        root.addWidget(self.view_toolbar)
        if self.debug_mode:
            self.debug_toolbar = self._debug_toolbar()
            root.addWidget(self.debug_toolbar)

        # 主体：侧边功能工具栏 + 内容区
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.action_toolbar = self._action_toolbar()
        body.addWidget(self.action_toolbar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.version_header, self.version_summary = create_version_header(self)
        content_layout.addWidget(self.version_header)
        self.table = create_version_table(self)
        self._hover_row = -1
        self._checkbox_containers = {}
        self._checked_ts = set()
        content_layout.addWidget(self.table, 1)

        # 预览视图容器（默认隐藏）
        self.preview_container, self.preview_controls = create_preview_view(self)
        self.preview_container.setVisible(False)
        content_layout.addWidget(self.preview_container, 1)

        # 音频管理器视图容器（默认隐藏）。页面自身拥有控件，主窗口只接收语义信号。
        self.audio_page = AudioPage(self)
        self.audio_controller = AudioController(
            page=self.audio_page,
            material_dir=os.path.join(DATA_DIR, "material"),
            debank_dir=os.path.join(get_tools_dir(), "epic7_debank_v1_0"),
            lua_output_dir=LUA_OUTPUT_DIR,
            output_dir=os.path.join(get_base_dir(), "output"),
            parent=self,
        )
        self.audio_page.setVisible(False)
        content_layout.addWidget(self.audio_page, 1)

        # 角色视图容器（默认隐藏）
        self.character_container, self.character_controls = create_character_view(self)
        self.character_container.setVisible(False)
        content_layout.addWidget(self.character_container, 1)

        body.addWidget(content, 1)
        root.addLayout(body, 1)

        # 暴露预览控件引用（供其他方法使用）
        self.preview_title = self.preview_controls["preview_title"]
        self.preview_progress = self.preview_controls["preview_progress"]
        self.image_list = self.preview_controls["image_list"]
        self.empty_label = self.preview_controls["empty_label"]
        self.preview_status = self.preview_controls["preview_status"]
        self.btn_reload = self.preview_controls["btn_reload"]

        # 暴露角色控件引用
        self.character_title = self.character_controls["character_title"]
        self.character_search = self.character_controls["character_search"]
        self.character_table = self.character_controls["character_table"]
        self.character_detail = self.character_controls["character_detail"]
        self.character_profile_view = self.character_controls["character_profile_view"]
        self.character_status = self.character_controls["character_status"]
        self.character_empty = self.character_controls["character_empty"]
        self._refresh_unread_badges()

        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(f"""
            QStatusBar {{ background-color:{get_color('BG_SURFACE')}; border-top:1px solid {get_color('BORDER')};
                         padding:4px 12px; color:{get_color('TEXT_SECONDARY')}; font-size:14px; }}
        """)
        self.setStatusBar(self.status_bar)
        self._connect_audio_controller()
        self._anim_timer = QTimer()
        self._anim_dots = 0

    def _connect_audio_controller(self):
        """连接音频功能域的任务结果与全局 Shell 状态。"""
        self.audio_page.close_requested.connect(lambda: self._toggle_audio_mode(False))
        self.audio_controller.status_changed.connect(self.status_bar.showMessage)
        self.audio_controller.unread_changed.connect(self._refresh_unread_badges)
        self.audio_controller.processing_finished.connect(self._on_audio_processing_finished)
        self.audio_controller.processing_cancelled.connect(self._on_audio_processing_cancelled)
        self.audio_controller.processing_error.connect(self._on_audio_processing_error)

    def _on_audio_processing_finished(self, shared):
        """导入流程使用共享弹窗时，接收 AudioController 的完成结果。"""
        if shared and self._pending_import_message:
            self._finish_import(True, self._pending_import_message)

    def _on_audio_processing_cancelled(self, shared):
        """导入流程中的音频任务取消后结束导入流程。"""
        if shared and self._pending_import_message:
            self._finish_import(
                False,
                "音频后处理已取消，已完成的文件已保留，可稍后重新处理音频。",
                cancelled=True,
            )

    def _on_audio_processing_error(self, error_message, shared):
        """导入流程中的音频任务失败后保留导入结果并提示用户。"""
        if shared and self._pending_import_message:
            self._finish_import(
                True,
                self._pending_import_message,
                audio_error=error_message,
            )

    def _view_toolbar(self):
        """顶部视图切换工具栏（版本列表 / 图片预览 / 音频 / 角色）"""
        bar = QToolBar(); bar.setMovable(False)
        brand = QWidget()
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(8, 0, 16, 0)
        brand_layout.setSpacing(0)
        brand_layout.addWidget(self._lbl("XL", 16, get_color("ACCENT"), True))
        brand_layout.addWidget(self._lbl("资源工作台", 10, get_color("TEXT_MUTED")))
        bar.addWidget(brand)
        bar.addSeparator()
        self._unread_badges = {}

        def add_nav_button(key, text, icon, callback):
            wrapper = QWidget()
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(0, 0, 2, 0)
            wrapper_layout.setSpacing(0)
            button = self._tbtn(text, icon=self._icon(icon))
            button.setCheckable(True)
            button.clicked.connect(callback)
            badge = QLabel("●")
            badge.setFixedWidth(14)
            badge.setAlignment(Qt.AlignCenter)
            badge.setToolTip("有新的或变更的角色数据")
            badge.setStyleSheet(
                f"color:{get_color('DANGER')}; background:transparent; font-size:11px;"
            )
            badge.setVisible(False)
            wrapper_layout.addWidget(button)
            wrapper_layout.addWidget(badge)
            bar.addWidget(wrapper)
            self._unread_badges[key] = badge
            return button

        self.btn_home = add_nav_button("home", "版本列表", "list", self._load_data)
        self.btn_image_preview = add_nav_button(
            "preview", "图片预览", "image", lambda: self._toggle_preview_mode(True)
        )
        self.btn_audio = add_nav_button(
            "audio", "音频", "music", lambda: self._toggle_audio_mode(True)
        )
        self.btn_lua = add_nav_button("character", "角色", "users", self._start_lua_decrypt)
        bar.addSeparator()
        self.dl_progress = QProgressBar()
        self.dl_progress.setFixedHeight(28); self.dl_progress.setFixedWidth(260)
        self.dl_progress.setVisible(False)
        self.dl_progress.setStyleSheet(f"""
            QProgressBar {{ background-color:{get_color('BG_ELEVATED')}; border:none; border-radius:4px;
                           text-align:center; color:{get_color('TEXT_PRIMARY')}; font-size:12px; }}
            QProgressBar::chunk {{ background-color:{get_color('SUCCESS')}; border-radius:4px; }}
        """)
        bar.addWidget(self.dl_progress)
        return bar

    def _refresh_unread_badges(self):
        """把各功能模块的未读状态同步到对应顶层工作区标签。"""
        repository = load_character_repository(CHARACTER_REPOSITORY_PATH)
        unread = character_unread_status(repository)
        character_has_unread = bool(unread) or bool(self.character_unread)
        audio_dir = os.path.join(get_base_dir(), "output", "audio")
        audio_has_unread = bool(audio_unread_files(audio_dir))
        for key, badge in getattr(self, "_unread_badges", {}).items():
            badge.setVisible(
                (key == "character" and character_has_unread)
                or (key == "audio" and audio_has_unread)
            )

    def _action_toolbar(self):
        """侧边功能工具栏：检查更新/导入AS + 导出配置 + 主题（设置区）+ 刷新/作者（底部），竖排"""
        bar = QWidget()
        bar.setMinimumWidth(176)
        bar.setMaximumWidth(216)
        bar.setObjectName("sideToolbar")
        bar.setStyleSheet(f"QWidget#sideToolbar {{ background-color:{get_color('BG_SURFACE')}; border-right:1px solid {get_color('BORDER')}; }}")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(6, 10, 6, 10)
        layout.setSpacing(6)

        def _side_btn(t, icon):
            b = QPushButton(t)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setMinimumHeight(36)
            ic = self._icon(icon)
            if ic:
                b.setIcon(ic)
            b.setToolTip(t)
            b.setAccessibleName(t)
            b.setProperty("fluentAppearance", "secondary")
            return b

        self.btn_check = _side_btn("检查更新", "arrows-rotate")
        self.btn_check.setProperty("fluentAppearance", "primary")
        self.btn_check.clicked.connect(self._check_update)
        layout.addWidget(self.btn_check)
        self.btn_browse = _side_btn("导入AS", "file-import")
        self.btn_browse.setProperty("fluentAppearance", "primary")
        self.btn_browse.clicked.connect(self._import_selected)
        layout.addWidget(self.btn_browse)
        layout.addSpacing(8)
        layout.addWidget(self._lbl("导出内容", 11, get_color("TEXT_SECONDARY"), True))
        self.cb_export_lua = QCheckBox("lua")
        self.cb_export_lua.setChecked(True)
        self.cb_export_character = QCheckBox("角色立绘")
        self.cb_export_character.setChecked(True)
        self.cb_export_fgui = QCheckBox("FGUI图集")
        self.cb_export_fgui.setChecked(True)
        self.cb_export_audio = QCheckBox("音频")
        self.cb_export_audio.setChecked(True)
        # 4 个勾选竖排（一列一个，不挤）
        for cb in (self.cb_export_lua, self.cb_export_character, self.cb_export_fgui, self.cb_export_audio):
            layout.addWidget(cb)
        layout.addSpacing(12)
        layout.addWidget(self._lbl("界面主题", 11, get_color("TEXT_SECONDARY"), True))
        self.theme_selector = QComboBox()
        self.theme_selector.addItem(THEME_LABEL, FORMAL_THEME)
        self.theme_selector.setCurrentIndex(0)
        self.theme_selector.setToolTip("当前正式主题")
        self.theme_selector.setAccessibleName("当前界面主题")
        self.theme_selector.setEnabled(False)
        layout.addWidget(self.theme_selector)
        # 底部：刷新 + 作者（stretch 推到最下面）
        layout.addStretch()
        self.btn_refresh = _side_btn("刷新", "arrow-rotate-right")
        self.btn_refresh.clicked.connect(self._load_data)
        layout.addWidget(self.btn_refresh)
        self.btn_author = _side_btn("作者", "user")
        self.btn_author.clicked.connect(self._show_author_info)
        layout.addWidget(self.btn_author)
        return bar

    def _on_theme_changed(self):
        """兼容未来主题入口；当前正式版只保留蓝灰深色主题。"""
        name = normalize_theme_name(self.theme_selector.currentData())
        apply_theme(self, name)
        QSettings("XL", "xl_updata_tool").setValue("theme", name)
        logger.info(f"切换主题: {name}")

    def _get_export_categories(self):
        """返回勾选的导出分类集合（lua/character/fgui/audio）"""
        cats = set()
        if self.cb_export_lua.isChecked():
            cats.add("lua")
        if self.cb_export_character.isChecked():
            cats.add("character")
        if self.cb_export_fgui.isChecked():
            cats.add("fgui")
        if self.cb_export_audio.isChecked():
            cats.add("audio")
        return cats

    def _lbl(self, t, s=12, c=TEXT_PRIMARY, b=False):
        if c is None:
            c = get_color("TEXT_PRIMARY")
        l = QLabel(t)
        l.setStyleSheet(f"color:{c};font-size:{s}px;font-weight:{'bold' if b else 'normal'};padding:2px 8px;background:transparent;border:none;")
        return l

    def _icon(self, name, color=TEXT_PRIMARY):
        if QT_AWESOME_AVAILABLE:
            return qta.icon(f"fa6s.{name}", color=color)
        return None

    def _tbtn(self, t, accent=False, icon=None):
        b = QToolButton(); b.setText(t)
        b.setMinimumWidth(110)
        if icon:
            b.setIcon(icon)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setToolTip(t)
        b.setAccessibleName(t)
        if accent: b.setProperty("accent","true"); b.setStyleSheet(b.styleSheet())
        return b

    def _debug_toolbar(self):
        """调试模式专属工具栏：导入范围 + 清空各数据区域"""
        bar = QToolBar()
        bar.setMovable(False)
        bar.addWidget(self._lbl("调试模式", 12, ACCENT, True))
        bar.addSeparator()
        bar.addWidget(self._lbl("导入范围:", 12))
        self.debug_import_scope = QComboBox()
        self.debug_import_scope.addItem("全量导入", False)
        self.debug_import_scope.addItem("增量导入", True)
        self.debug_import_scope.setFixedWidth(130)
        bar.addWidget(self.debug_import_scope)
        bar.addSeparator()
        output_dir = os.path.join(get_base_dir(), "output")
        self._add_clear_btn(bar, "清空ab包", BUNDLES_DIR)
        self._add_clear_btn(bar, "清空AS导出", os.path.join(DATA_DIR, "material"))
        self._add_clear_btn(bar, "清空立绘", os.path.join(output_dir, "character"))
        self._add_clear_btn(bar, "清空Lua临时", os.path.join(DATA_DIR, "material", "assets", "lua"))
        self._add_clear_btn(bar, "清空Lua版本", LUA_OUTPUT_DIR)
        self._add_clear_btn(bar, "清空音频", os.path.join(output_dir, "audio"))
        return bar

    def _add_clear_btn(self, bar, text, target_dir):
        btn = self._tbtn(text)
        btn.clicked.connect(lambda: self._clear_dir(target_dir))
        bar.addWidget(btn)

    def _set_version_content_visible(self, visible):
        """整体切换版本工作区，避免页面切换留下单独的工作区标题。"""
        self.version_header.setVisible(visible)
        self.table.setVisible(visible)

    def _clear_dir(self, target_dir):
        """确认后清空目录（rmtree + 重建）"""
        if not os.path.isdir(target_dir):
            QMessageBox.information(self, "清空", f"目录不存在:\n{target_dir}")
            return
        ret = QMessageBox.question(self, "确认清空",
                                   f"确定要清空此目录？\n{target_dir}",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            shutil.rmtree(target_dir)
            os.makedirs(target_dir, exist_ok=True)
            logger.info(f"[调试] 已清空目录: {target_dir}")
            # 清空 ab 包后同步清数据库下载状态并刷新版本列表
            if target_dir == BUNDLES_DIR:
                c = db.get_conn()
                c.execute("UPDATE sub_bundles SET local_path=NULL, downloadable=0 WHERE local_path IS NOT NULL")
                c.commit()
                c.close()
                self._load_data()
            QMessageBox.information(self, "清空", f"已清空:\n{target_dir}")
        except Exception as e:
            logger.error(f"[调试] 清空失败 {target_dir}: {e}")
            QMessageBox.warning(self, "清空失败", str(e))

    def _compute_delta_hashes(self, ts):
        """返回本版本相对上一版本的增量 hash 集合（新增/修改的 bundle）；无上一版本时返回全量"""
        vs = self.version_mgr.get_versions()
        hashes_by_version = {
            row[0]: (r[0] for r in (db.get_sub_bundles(row[0]) or []))
            for row in vs
            if "(delta" not in (row[10] or "")
        }
        return compute_download_hashes(ts, (row[0] for row in vs), hashes_by_version)

    def _row_btn(self, text, color, tooltip=""):
        b = QPushButton(text)
        b.setFixedHeight(30)
        if "增量" in text or "全量" in text: b.setFixedWidth(78)
        elif "删除" in text: b.setFixedWidth(100)
        else: b.setFixedWidth(64)
        # 克制配色：描边 + 同色字，hover 才实心
        b.setStyleSheet(f"""
            QPushButton {{ background-color:transparent; border:1px solid {color};
                          border-radius:6px; padding:2px 8px; color:{color};
                          font-size:12px; font-weight:600; }}
            QPushButton:hover {{ background-color:{color}; color:#fff; }}
        """)
        return b

    # ========== DATA ==========

    def _sync_local_bundles(self, ts=None):
        """扫描磁盘实际 .bundle 文件，同步 DB 下载状态（local_path/downloadable）

        磁盘是事实来源：磁盘有但 DB 未记录 → 标记已下载；DB 有记录但磁盘无 → 清除。
        ts 非空时只同步该版本，否则同步全部版本。
        """
        return sync_local_bundles(BUNDLES_DIR, ts)

    def _load_data(self):
        # 确保版本列表可见（隐藏图片预览、音频和角色视图）
        self._set_version_content_visible(True)
        self.preview_container.setVisible(False)
        self.audio_page.setVisible(False)
        self.character_container.setVisible(False)
        self._show_character = False
        self._set_active_view_btn(self.btn_home)
        self._set_toolbars_visible(True)
        self._sync_local_bundles()
        versions = self.version_mgr.refresh()
        delta_map = self._compute_version_deltas(versions)
        self._populate_table(versions, delta_map)
        self.status_bar.showMessage(f"已追踪 {len(versions)} 个版本")

    def _compute_version_deltas(self, versions):
        """计算每个版本相对上一版本的 bundle 差异，返回 {ts: (added, removed, common)}"""
        hashes_by_version = {
            row[0]: (r[0] for r in (db.get_sub_bundles(row[0]) or []))
            for row in versions
        }
        return compute_version_delta_map((row[0] for row in versions), hashes_by_version)

    def _populate_table(self, versions, delta_map=None):
        self.table.setSortingEnabled(False)
        # 捕获当前勾选态（刷新后恢复，保持用户选择不丢失）
        self._checked_ts = {ts for _r, (ts, cb) in getattr(self, "_version_checkboxes", {}).items() if cb.isChecked()}
        # fully clear old widgets and rows
        self.table.clearContents()
        self.table.setRowCount(0)
        self.table.setRowCount(len(versions))
        self._version_checkboxes = {}  # row -> (ts, checkbox)
        self._checkbox_containers = {}
        self._hover_row = -1
        downloaded_versions = 0
        for i, v in enumerate(versions):
            ts, arts, data, other, video, apk, manifest, is_cur, dl, created, notes = v
            # checkbox 列（第 0 列，打勾选中；单选冲突，Ctrl 多选）
            cb = QCheckBox()
            cb.setStyleSheet("background:transparent; border:none;")
            cb.setChecked(ts in self._checked_ts)
            cb.clicked.connect(lambda checked, r=i: self._set_version_checked(r, checked))
            cb_widget = QWidget()
            cb_widget.setAttribute(Qt.WA_StyledBackground, True)
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(i, 0, cb_widget)
            self._version_checkboxes[i] = (ts, cb)
            self._checkbox_containers[i] = cb_widget
            # 版本列（第 1 列）
            dt = ticks_to_date(ts); date_str = dt.strftime("%Y-%m-%d")
            label = date_str
            if is_cur: label += "  [最新]"
            vi = QTableWidgetItem(label); vi.setData(Qt.UserRole, ts)
            if is_cur: vi.setForeground(QColor("#f0a040")); f = vi.font(); f.setBold(True); vi.setFont(f)
            self.table.setItem(i, 1, vi)
            sub = db.get_sub_bundles(ts); total = len(sub) if sub else 0
            down = sum(1 for r in sub if r[2]) if sub else 0
            if total == 0: status = "无Bundle"
            elif down >= total: status = "已下载"
            elif down > 0: status = f"部分 ({down}/{total})"
            else: status = "未下载"
            if status == "已下载":
                downloaded_versions += 1
            si = QTableWidgetItem(status)
            if status == "已下载": si.setForeground(QColor(SUCCESS))
            elif "部分" in status: si.setForeground(QColor(WARNING))
            else: si.setForeground(QColor(TEXT_MUTED))
            self.table.setItem(i, 2, si)
            self.table.setItem(i, 3, QTableWidgetItem(f"{total:,}" if total else "-"))
            # 备注：优先显示相对上一版本的增量/删除
            if delta_map and ts in delta_map:
                added, removed, common = delta_map[ts]
                display_notes = f"新增 {added} | 移除 {removed} | 未变 {common}"
            else:
                display_notes = notes or ""
            self.table.setItem(i, 4, QTableWidgetItem(display_notes))
            # per-row buttons: delta, full, delete
            for col, (txt, clr, action) in enumerate([
                ("增量下载", SUCCESS, lambda c, t=ts: self._download_version(t, True)),
                ("全量下载", INFO, lambda c, t=ts: self._download_version(t, False)),
                ("删除已下载", DANGER, lambda c, t=ts: self._delete_version(t)),
            ], start=5):
                btn = self._row_btn(txt, clr)
                btn.clicked.connect(action)
                self.table.setCellWidget(i, col, btn)
        self.table.setSortingEnabled(True)
        self._version_count = len(versions)
        self._downloaded_version_count = downloaded_versions
        self._update_version_summary()

    def _update_version_summary(self):
        """刷新版本工作区摘要，补充选择与下载状态的文字信息。"""
        selected = sum(
            1 for _ts, checkbox in getattr(self, "_version_checkboxes", {}).values()
            if checkbox.isChecked()
        )
        self.version_summary.setText(
            f"{getattr(self, '_version_count', 0)} 个版本 · "
            f"已下载 {getattr(self, '_downloaded_version_count', 0)} · "
            f"已选择 {selected}"
        )

    def _get_selected_ts(self):
        """返回第一个勾选的版本 ts（导入 AS 等单版本操作用）"""
        for _row, (ts, cb) in getattr(self, "_version_checkboxes", {}).items():
            if cb.isChecked():
                return ts
        return None

    def _set_version_checked(self, row, checked):
        """设置某行勾选态；默认单选冲突（点一个取消另一个），按住 Ctrl 多选"""
        if row not in self._version_checkboxes:
            return
        ts, cb = self._version_checkboxes[row]
        ctrl = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
        if checked and not ctrl:
            for r, (_t, other) in self._version_checkboxes.items():
                if r != row and other.isChecked():
                    other.blockSignals(True)
                    other.setChecked(False)
                    other.blockSignals(False)
        cb.blockSignals(True)
        cb.setChecked(checked)
        cb.blockSignals(False)
        if checked:
            self._checked_ts.add(ts)
        else:
            self._checked_ts.discard(ts)
        self._update_version_summary()

    def _on_cell_clicked(self, row, col):
        """点击整行任意位置（除勾选列/按钮列）→ 翻转该行勾选"""
        if col == 0:
            return  # 勾选框自己处理
        if row in self._version_checkboxes:
            _ts, cb = self._version_checkboxes[row]
            self._set_version_checked(row, not cb.isChecked())

    def _clear_row_hover(self):
        """清掉上一悬停行的背景，恢复交替行色"""
        r = self._hover_row
        if r < 0:
            return
        for c in range(self.table.columnCount()):
            it = self.table.item(r, c)
            if it is not None:
                it.setBackground(QBrush())
        if r in self._checkbox_containers:
            self._checkbox_containers[r].setStyleSheet("")

    def _highlight_row(self, row):
        """整行悬停高亮（中性色，非蓝非单格）；row=-1 时清除"""
        if row == self._hover_row:
            return
        self._clear_row_hover()
        self._hover_row = row
        hover = get_color('BG_HOVER')
        for c in range(self.table.columnCount()):
            it = self.table.item(row, c)
            if it is not None:
                it.setBackground(QColor(hover))
        if row in self._checkbox_containers:
            self._checkbox_containers[row].setStyleSheet(f"background-color:{hover};")

    def eventFilter(self, obj, event):
        """viewport 上跟踪鼠标：MouseMove 高亮整行，Leave 清除"""
        if obj is self.table.viewport():
            if event.type() == QEvent.MouseMove:
                pos = event.position().toPoint()
                idx = self.table.indexAt(pos)
                self._highlight_row(idx.row() if idx.isValid() else -1)
            elif event.type() == QEvent.Leave:
                self._clear_row_hover()
                self._hover_row = -1
        return super().eventFilter(obj, event)

    def _on_row_select(self, current, prev):
        if current:
            it = self.table.item(current.row(), 1)
            if it and it.data(Qt.UserRole):
                self._sel_ts = it.data(Qt.UserRole)

    # ========== CHECK UPDATE ==========

    def _check_update(self):
        # 确保版本列表可见（隐藏图片预览和音频视图）
        self._set_version_content_visible(True)
        self.preview_container.setVisible(False)
        self.audio_page.setVisible(False)
        self.btn_check.setEnabled(False)
        self.table.insertRow(0)
        self.table.setItem(0, 0, QTableWidgetItem("正在检查更新"))
        anim_item = self.table.item(0, 0)
        for c in range(1, 7):
            self.table.setItem(0, c, QTableWidgetItem(""))
        def animate():
            self._anim_dots = (self._anim_dots + 1) % 4
            try: anim_item.setText("正在检查更新" + "." * (self._anim_dots + 1))
            except: pass
            self._anim_timer.start(400)
        self._anim_timer.timeout.connect(animate)
        self._anim_timer.start(400)
        self.status_bar.showMessage("正在检查更新...")
        pv = self.version_mgr.get_current(); oh = []
        if pv:
            or_ = db.get_sub_bundles(pv[0]); oh = [r[0] for r in or_] if or_ else []
        logger.info(f"开始检查更新：当前版本 {pv[0] if pv else '无'}，已有 {len(oh)} 个 hash")
        out_dir = os.path.join(BUNDLES_DIR, "current")
        self.thread = CheckUpdateThread(out_dir, oh)
        self.thread.finished.connect(self._on_update_checked)
        self.thread.error.connect(self._on_check_error)
        self.thread.start()

    def _on_update_checked(self, info, versions, new_hashes, delta):
        self._anim_timer.stop()
        self.btn_check.setEnabled(True)
        result = register_checked_version(self.version_mgr, info, versions, new_hashes, delta)
        if result:
            self.status_bar.showMessage(f"发现新版本! 新增 {result['added']} 个 bundle.")
            QMessageBox.information(self, "更新完成", f"发现新版本!\n\n{result['notes']}")
        else:
            logger.info(f"检查更新：版本 {versions['timestamp']} 已存在，无需更新")
            self.status_bar.showMessage("已是最新版本.")
            QMessageBox.information(self, "已是最新", "当前已是最新版本，无需更新。")
        self._load_data()

    def _on_check_error(self, err):
        self._anim_timer.stop(); self.btn_check.setEnabled(True)
        self._load_data()
        self.status_bar.showMessage(f"错误: {err}")
        QMessageBox.warning(self, "错误", f"检查更新失败:\n{err}")

    # ========== DOWNLOAD ==========

    def _download_version(self, ts, delta_only=True):
        if not ts: return
        ah = [r[0] for r in db.get_sub_bundles(ts)]
        if not ah: QMessageBox.information(self, "无Bundle", "此版本无 bundle."); return
        sub = db.get_sub_bundles(ts)
        label = "增量下载"
        if delta_only:
            target = self._compute_delta_hashes(ts)
        else:
            rp = QMessageBox.question(self, "全量下载确认",
                f"全量下载将下载此版本的全部 {len(ah)} 个 bundle 文件.\n\n"
                "通常只需「增量下载」即可获取本版本新增/修改的文件.\n"
                "全量下载耗时较长且占用大量磁盘空间.\n\n"
                "建议: 先尝试增量下载.\n\n"
                "是否仍然进行全量下载?",
                QMessageBox.Yes|QMessageBox.No, QMessageBox.No)
            if rp != QMessageBox.Yes: return
            target = set(ah); label = "全量下载"
        missing = calculate_missing_downloads(sub, target)
        if not missing: QMessageBox.information(self, "已下载", "全部已下载."); return

        self._dl_ts = ts; self._dl_total = len(missing); self._dl_size = 0
        self._dl_count = 0; self._dl_label = label

        # update status cell for this version
        self._update_row_status(ts, f"下载中 0/{len(missing)}")

        self.dl_progress.setVisible(True)
        self.dl_progress.setMaximum(len(missing)); self.dl_progress.setValue(0)
        self.dl_progress.setFormat(f"{label}: 0/{len(missing)}")
        self.status_bar.showMessage(f"{label}: 准备下载 {len(missing)} 个文件...")
        downloaded_count = len(sub) - len(calculate_missing_downloads(sub, set(ah)))
        logger.info(f"开始{label}版本 {ts}：待下载 {len(missing)} 个文件（已下载 {downloaded_count} 个）")

        out_dir = os.path.join(BUNDLES_DIR, str(ts))
        self.dl_worker = DownloadWorker(missing, out_dir)
        self.dl_worker.progress.connect(lambda n, d, t: (
            setattr(self, '_dl_count', d),
            self.dl_progress.setValue(d),
            self.dl_progress.setFormat(f"{label}: {d}/{len(missing)}"),
            self._update_row_status(ts, f"下载中 {d}/{len(missing)} | {self._dl_size/1048576:.1f}MB"),
            self.status_bar.showMessage(f"{label}: {d}/{len(missing)} | {self._dl_size/1048576:.1f}MB | {n[:30]}")))
        self.dl_worker.item_done.connect(lambda n, f, p: (
            self._dl_done(n, f, p, ts),
            setattr(self, '_dl_size', self._dl_size + (os.path.getsize(p) if os.path.exists(p) else 0))))
        self.dl_worker.item_fail.connect(lambda h, msg: logger.error(f"文件下载失败: {h[:16]}... - {msg}"))
        self.dl_worker.all_done.connect(self._dl_complete)
        self.dl_worker.error.connect(lambda e: (
            logger.error(f"下载错误: {e}"),
            self.status_bar.showMessage(f"下载出错: {e}")))
        self.dl_worker.start()

    def _update_row_status(self, ts, text):
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 1)
            if it and it.data(Qt.UserRole) == ts:
                si = QTableWidgetItem(text)
                si.setForeground(QColor(SUCCESS))
                self.table.setItem(i, 2, si)
                break
        self.table.blockSignals(False)

    def _dl_done(self, n, f, p, ts):
        try:
            record_downloaded_bundle(ts, n, p)
            logger.debug(f"文件下载完成并更新数据库: {n[:16]}...")
        except Exception as e:
            logger.error(f"更新数据库失败: {n[:16]}... - {e}", exc_info=True)

    def _dl_complete(self):
        self.dl_progress.setVisible(False)
        self._load_data()
        self.status_bar.showMessage("下载完成!")
        logger.info("下载完成")
        QMessageBox.information(self, "完成", "下载完毕!")

    # ========== BROWSE ==========

    def _import_selected(self):
        # 确保版本列表可见（隐藏图片预览和音频视图）
        self._set_version_content_visible(True)
        self.preview_container.setVisible(False)
        self.audio_page.setVisible(False)
        ts = self._get_selected_ts()
        if not ts: QMessageBox.warning(self, "未选择", "请先选中一个版本."); return
        export_categories = self._get_export_categories()
        if not export_categories:
            QMessageBox.warning(self, "提示", "请至少勾选一类要导出的资源")
            return

        # 同步该版本磁盘实际状态（磁盘为准），再取已下载的本地路径
        self._sync_local_bundles(ts)
        sub = db.get_sub_bundles(ts)
        # debug 模式：可选「全量导入 / 增量导入」
        delta_only = (self.debug_mode and getattr(self, "debug_import_scope", None)
                      and self.debug_import_scope.currentData())
        if delta_only:
            delta_hashes = self._compute_delta_hashes(ts)
            fs = [r[2] for r in sub if r[2] and os.path.exists(r[2]) and r[0] in delta_hashes]
            logger.info(f"[导入AS] 增量导入：{len(fs)} 个增量 bundle（增量 hash {len(delta_hashes)} 个）")
        else:
            fs = [r[2] for r in sub if r[2] and os.path.exists(r[2])]
        if not fs:
            QMessageBox.information(self, "无文件", "此版本没有已下载的 bundle，请先下载.")
            return

        # 计算路径
        bundle_dir = os.path.dirname(fs[0])
        isolate_bundle_dir = False
        if export_categories in ({"lua"}, {"audio"}):
            map_path = (
                lua_assets_map_path(bundle_dir)
                if export_categories == {"lua"}
                else audio_assets_map_path(bundle_dir)
            )
            selector = select_lua_bundles if export_categories == {"lua"} else select_audio_bundles
            selected_fs, mapped, asset_count = selector(fs, map_path)
            if mapped:
                fs = selected_fs
                isolate_bundle_dir = True
                logger.info(
                    "[导入AS] %s 资源映射命中 %s 个资源，筛选 %s/%s 个 bundle",
                    "Lua" if export_categories == {"lua"} else "音频",
                    asset_count, len(fs), len(sub),
                )
                if not fs:
                    QMessageBox.information(
                        self,
                        "没有对应资源",
                        "该版本的资源映射中没有找到所选分类的资源。",
                    )
                    return
            else:
                logger.warning(
                    "[导入AS] %s 资源映射不可用，无法提前精准筛选 bundle，将兼容扫描已下载包：%s",
                    "Lua" if export_categories == {"lua"} else "音频",
                    map_path,
                )
                self.status_bar.showMessage("未找到资源映射，将扫描已下载 bundle")
        as_cli = os.path.join(get_tools_dir(), "AssetStudio", "AssetStudio.CLI.exe")
        material_dir = os.path.join(DATA_DIR, "material")

        if not os.path.exists(as_cli):
            logger.error(f"AssetStudio.CLI.exe 不存在: {as_cli}")
            QMessageBox.warning(self, "错误", f"AssetStudio CLI 不存在:\n{as_cli}")
            return
        logger.debug(f"AssetStudio.CLI 路径确认: {as_cli}")

        # 取消已有导入线程
        if self._import_worker is not None:
            self._import_worker.cancel()
            self._import_worker.wait(2000)

        # 禁用导入按钮，显示进度条
        self.btn_browse.setEnabled(False)
        self.dl_progress.setVisible(True)
        self.dl_progress.setValue(0)
        self.dl_progress.setFormat("修复文件头: 0/0")
        self.status_bar.showMessage("正在导入AS: 修复文件头...")

        # 启动后台工作线程
        # 大规模操作告知耗时（全量导出 bundle 数大时）
        if len(fs) > 1000:
            ret = QMessageBox.question(
                self, "耗时提示",
                f"本次将导入 {len(fs)} 个 bundle，预计耗时较长（可能数分钟）。\n\n是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret != QMessageBox.Yes:
                self.btn_browse.setEnabled(True)
                self.dl_progress.setVisible(False)
                return
        logger.info(f"开始导入AS: 版本 {ts}, 共 {len(fs)} 个 bundle 文件，勾选导出分类 {sorted(export_categories)}")
        self._import_worker = ImportASWorker(
            fs, bundle_dir, material_dir, as_cli, self,
            export_categories=export_categories,
            version_timestamp=ts,
            lua_output_dir=LUA_OUTPUT_DIR,
            isolate_bundle_dir=isolate_bundle_dir,
        )
        self._import_worker.progress_stage.connect(self._on_import_progress)
        self._import_worker.stage_finished.connect(self._on_import_stage_finished)
        self._import_worker.category_finished.connect(self._on_import_category_finished)
        self._import_worker.all_finished.connect(self._on_import_all_finished)
        self._import_worker.start()

        # 进度弹窗（非模态，可取消）
        self._import_progress_dialog = QProgressDialog("正在导入 AS...", "取消", 0, 100, self)
        self._import_progress_dialog.setWindowTitle("导入 AS")
        self._import_progress_dialog.setWindowModality(Qt.NonModal)
        self._import_progress_dialog.setMinimumDuration(0)
        self._import_progress_dialog.setAutoClose(False)
        self._import_progress_dialog.setAutoReset(False)
        self._import_progress_dialog.setMinimumWidth(520)
        self._import_progress_dialog.setMinimumHeight(160)
        self._import_progress_dialog.canceled.connect(self._import_worker.cancel)
        self._import_progress_dialog.show()

    def _on_import_progress(self, stage_name, current, total):
        """导入AS进度更新（两行：阶段名 + 进度/处理中，避免来回跳）"""
        if getattr(self, "_import_progress_dialog", None):
            if total > 0:
                self._import_progress_dialog.setLabelText(f"{stage_name}\n已处理 {current}/{total}")
                self._import_progress_dialog.setRange(0, total)
                self._import_progress_dialog.setValue(current)
            else:
                self._import_progress_dialog.setLabelText(f"{stage_name}\n处理中...")
                self._import_progress_dialog.setRange(0, 0)
        if total > 0:
            self.dl_progress.setMaximum(total)
            self.dl_progress.setValue(current)
            self.dl_progress.setFormat(f"{stage_name}: {current}/{total}")
        else:
            self.dl_progress.setFormat(f"{stage_name}: 处理中...")
        self.status_bar.showMessage(f"导入AS: {stage_name} {current}/{total}" if total > 0 else f"导入AS: {stage_name} 处理中...")

    def _on_import_stage_finished(self, stage_name):
        """导入AS阶段完成"""
        logger.info(f"导入AS阶段完成: {stage_name}")
        self.status_bar.showMessage(f"导入AS: {stage_name} 完成")

    def _on_import_category_finished(self, label):
        """记录分类完成；音频需等后处理结束后再刷新最终列表。"""
        logger.debug(f"[导入AS] 分类完成: {label}")

    def _on_import_all_finished(self, success, message):
        """导入AS全部完成"""
        if not success:
            self._finish_import(False, message)
            return

        self.status_bar.showMessage("导入AS: 文件已分类完成，准备执行后处理")
        self._append_changelog(message)
        worker = getattr(self, "_import_worker", None)
        categories = getattr(worker, "export_categories", set()) or set()
        self._pending_import_message = message
        if "audio" in categories:
            # 音频解密是导出后的必经步骤，复用导入弹窗，避免用户误以为已经完成。
            self.audio_controller.start_decrypt(
                force=False,
                shared_dialog=getattr(self, "_import_progress_dialog", None),
            )
            return

        self._finish_import(True, message)

    def _finish_import(self, success, message, audio_error=None, cancelled=False):
        """结束导入及其后处理阶段，统一关闭共享弹窗和恢复按钮状态。"""
        self.btn_browse.setEnabled(True)
        self.dl_progress.setVisible(False)
        if getattr(self, "_import_progress_dialog", None):
            self._import_progress_dialog.close()
            self._import_progress_dialog = None
        self._pending_import_message = None

        if cancelled:
            self.status_bar.showMessage("导入已取消，已完成的文件已保留")
            QMessageBox.information(self, "已取消", message)
            return

        if success:
            self._auto_parse_after_lua_export()
            if audio_error:
                self.status_bar.showMessage("导入完成，但音频后处理失败")
                QMessageBox.warning(
                    self,
                    "导入完成但音频解析失败",
                    f"{message}\n\n音频解析失败：{audio_error}\n可重新导入音频分类进行补偿。",
                )
            else:
                self.status_bar.showMessage("导入AS: 文件与后处理均已完成")
                QMessageBox.information(self, "完成", message)
            return

        self.status_bar.showMessage("导入AS: 失败")
        QMessageBox.warning(
            self, "失败",
            f"{message}\n\n文件可能损坏，请点击【删除已下载】并重新下载。"
        )

    def _append_changelog(self, message):
        """追加导入更新日志到 output/CHANGELOG.md（像 git 提交记录）"""
        try:
            out_log = os.path.join(get_base_dir(), "output", "CHANGELOG.md")
            append_changelog(out_log, message)
            logger.info(f"已写更新日志: {out_log}")
        except Exception as e:
            logger.warning(f"写更新日志失败: {e}")

    def _browse_version(self, ts):
        """备用方法：打开资源浏览器窗口（当前未使用，保留以备后用）"""
        sub = db.get_sub_bundles(ts)
        fs = [r[2] for r in sub if r[2] and os.path.exists(r[2])] if sub else []
        if not fs: QMessageBox.information(self, "无文件", "此版本没有已下载的 bundle，请先下载."); return
        from .legacy.asset_browser_entry import open_legacy_asset_browser
        open_legacy_asset_browser(self, fs, ts)
        self.status_bar.showMessage("资源浏览器已关闭.")

    # ========== AUTHOR INFO ==========

    def _show_author_info(self):
        """显示作者信息"""
        QMessageBox.information(
            self,
            "关于",
            "作者：患得患失和Lucky_King共同创作"
        )

    # ========== OPEN SPINEVIEWER ==========

    def _open_spineviewer(self):
        """启动 SpineViewer 独立程序"""
        sv_exe = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewer.exe")

        if not os.path.exists(sv_exe):
            logger.error(f"SpineViewer.exe 未找到: {sv_exe}")
            QMessageBox.warning(
                self, "错误",
                "SpineViewer.exe 未找到，请确认 tools/SpineViewer/ 目录完整"
            )
            return

        try:
            logger.info(f"启动 SpineViewer: {sv_exe}")
            if sys.platform == "win32":
                os.startfile(sv_exe)
            else:
                subprocess.Popen([sv_exe])
            self.status_bar.showMessage("SpineViewer 已启动")
        except Exception as e:
            logger.error(f"启动 SpineViewer 失败: {e}")
            QMessageBox.warning(self, "错误", f"启动 SpineViewer 失败:\n{e}")

    # ========== PREVIEW ==========

    def _preview_images(self):
        """预览图片：检查缓存，有则直接加载，无则导出后加载"""
        output_dir = os.path.join(get_base_dir(), "output", "character")

        os.makedirs(output_dir, exist_ok=True)

        # 检查已有图片（递归：角色图按编号分目录）
        existing_pngs = []
        for root, _dirs, files in os.walk(output_dir):
            existing_pngs.extend(f for f in files if f.lower().endswith(".png"))

        if existing_pngs:
            # 情况 A：已有图片，直接加载
            logger.info(f"预览目录已存在 {len(existing_pngs)} 张图片，直接加载")
            self._preview_source = "缓存"
            self._toggle_preview_mode(True)
            return

        # 情况 B：没有图片，启动后台线程导出
        logger.info("预览目录为空，开始导出 ...")
        self._start_preview_export(force=False)

    def _start_preview_export(self, force=False, selected_roles=None):
        """启动后台线程执行 .skel → PNG 导出（含配对合成 + 皮肤导出）

        force=True 时强制重新导出（跳过去重检查）。
        selected_roles 非空时只导出这些角色（角色名集合）。
        """
        material_dir = os.path.join(DATA_DIR, "material")
        output_dir = os.path.join(get_base_dir(), "output", "character")
        spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")

        # 前置校验（UI 线程中执行，可弹窗）
        if not os.path.isdir(material_dir):
            logger.warning(f"素材目录不存在: {material_dir}")
            QMessageBox.warning(self, "错误", f"素材目录不存在:\n{material_dir}\n\n请先点击【导入AS】导出资源.")
            return

        if not os.path.exists(spine_cli):
            logger.warning(f"SpineViewerCLI 不存在: {spine_cli}")
            QMessageBox.warning(self, "错误", f"SpineViewerCLI 不存在:\n{spine_cli}")
            return

        # 取消已有的导出线程
        self._cancel_preview_worker()

        # 切换到预览视图（显示进度条）
        self._preview_source = "刚导出"
        self._toggle_preview_mode(True)

        self.status_bar.showMessage("正在处理..." if not force else "强制重新导出中...")
        if hasattr(self, "preview_progress"):
            self.preview_progress.setVisible(True)
            self.preview_progress.setValue(0)

        # 启动后台线程
        self._preview_worker = PreviewExportWorker(
            material_dir, output_dir, spine_cli, force=force, selected_roles=selected_roles, parent=self
        )
        self._preview_worker.progress.connect(self._on_preview_export_progress)
        self._preview_worker.export_finished.connect(self._on_preview_export_finished)
        self._preview_worker.error.connect(self._on_preview_export_error)
        self._preview_worker.start()

    def _on_preview_export_progress(self, current, total):
        """预览导出进度更新"""
        if hasattr(self, "preview_progress") and self.preview_progress:
            self.preview_progress.setMaximum(total)
            self.preview_progress.setValue(current)
            self.preview_progress.setFormat(f"导出中... {current}/{total}")
        self.status_bar.showMessage(f"正在导出预览图片: {current}/{total}")

    def _on_preview_export_finished(self, success, summary):
        """预览导出完成回调"""
        if hasattr(self, "preview_progress") and self.preview_progress:
            self.preview_progress.setVisible(False)
        self.status_bar.showMessage("导出完成" if success else "导出失败")
        if self.preview_container.isVisible():
            # 视图可见才弹窗 + 立即刷新；切走时不打扰（返回预览会自动 _load_preview_images）
            QMessageBox.information(self, "导出完成", summary)
            self._load_preview_images()

    def _on_preview_export_error(self, err_msg):
        """预览导出错误回调"""
        if hasattr(self, "preview_progress") and self.preview_progress:
            self.preview_progress.setVisible(False)
        self.status_bar.showMessage("导出失败")
        QMessageBox.warning(self, "错误", f"预览导出失败:\n{err_msg}")

    def _force_reload_preview(self):
        """重新加载预览图片：弹出角色选择对话框，只导出勾选的角色"""
        roles = self._scan_cardspine_roles()
        if not roles:
            QMessageBox.warning(self, "提示", "未找到角色立绘，请先导入资源")
            return
        from .dialogs.character_select import CharacterSelectDialog
        dialog = CharacterSelectDialog(roles, self)
        if dialog.exec() != QDialog.Accepted:
            return
        selected = dialog.selected_roles()
        if not selected:
            QMessageBox.information(self, "提示", "未选择任何角色")
            return
        logger.info(f"重新加载预览图片，选中 {len(selected)} 个角色")
        self.status_bar.showMessage(f"导出 {len(selected)} 个角色...")
        self._start_preview_export(force=False, selected_roles=selected)

    def _scan_cardspine_roles(self):
        """扫描 material 的 cardspine .skel，返回角色名列表（去重排序，排除 _bg）"""
        material_dir = os.path.join(DATA_DIR, "material", "assets", "art", "models", "cardspine")
        return scan_cardspine_roles(material_dir)

    # ========== IMAGE GALLERY PREVIEW ==========

    def _set_active_view_btn(self, active_btn):
        """高亮当前激活的视图按钮（取消其他）"""
        for btn in (self.btn_home, self.btn_image_preview, self.btn_audio, self.btn_lua):
            btn.setChecked(btn is active_btn)

    def _set_toolbars_visible(self, visible):
        """切换视图时只隐藏侧边栏，顶部导航栏（view_toolbar/debug_toolbar）始终保留"""
        if hasattr(self, "action_toolbar"):
            self.action_toolbar.setVisible(visible)

    def _toggle_preview_mode(self, show_preview):
        """切换预览视图/版本列表"""
        self._set_active_view_btn(self.btn_image_preview if show_preview else self.btn_home)
        self._set_toolbars_visible(not show_preview)
        self._set_version_content_visible(not show_preview)
        self.preview_container.setVisible(show_preview)
        self.character_container.setVisible(False)
        self._show_character = False
        if show_preview:
            self.audio_page.setVisible(False)
            self._load_preview_images()

    # ==================== 音频管理器 ====================

    def _toggle_audio_mode(self, show_audio):
        """切换音频管理器视图"""
        self._set_active_view_btn(self.btn_audio if show_audio else self.btn_home)
        self._set_toolbars_visible(not show_audio)
        self._set_version_content_visible(not show_audio)
        self.preview_container.setVisible(False)
        self.character_container.setVisible(False)
        self._show_character = False
        self.audio_page.setVisible(show_audio)
        if show_audio:
            self.audio_controller.initialize_player()
            # 页面切换只读取最终产物；已加载过的树保留，用户可用“刷新列表”主动重建。
            self.audio_controller.load_catalog()

    def _cancel_preview_worker(self):
        """取消预览导出线程"""
        if self._preview_worker is not None:
            self._preview_worker.cancel()
            self._preview_worker.wait(2000)
            self._preview_worker = None

    # ========== 角色视图 ==========

    ELEMENT_MAP = {
        1: "火焰", 2: "水", 3: "木", 4: "光", 5: "暗",
    }

    def _toggle_character_mode(self, show_character):
        """切换角色视图显示/隐藏"""
        self._set_active_view_btn(self.btn_lua if show_character else self.btn_home)
        self._set_toolbars_visible(not show_character)
        self._set_version_content_visible(not show_character)
        self.preview_container.setVisible(False)
        self.audio_page.setVisible(False)
        self.character_container.setVisible(show_character)
        self._show_character = show_character
        if show_character:
            self.character_container.raise_()
            self.character_container.show()

    @timed("角色数据加载")
    def _load_character_data(self, lua_dir=None, version_timestamp=None, force=False, automatic=False):
        """加载最新有效 Lua，并把解析结果增量写入角色数据仓库。"""
        if lua_dir is None and version_timestamp is None and not force:
            if self._restore_character_data():
                return True
        if lua_dir is None:
            version_timestamp, lua_dir = self._latest_lua_source()
        if not lua_dir or not self._has_character_source(lua_dir):
            self.status_bar.showMessage("未找到完整角色 Lua 数据，请先导出包含 BaseCard/BaseWord 的最新版本")
            self._character_loading = False
            return False

        repository = load_character_repository(CHARACTER_REPOSITORY_PATH)
        if (
            not force
            and version_timestamp is not None
            and repository.get("current_version") == int(version_timestamp)
            and repository.get("current_characters")
        ):
            self._apply_character_repository(repository)
            self._character_data_loaded = bool(self.characters)
            self._character_loading = False
            self.status_bar.showMessage(f"角色数据从版本仓库加载: {len(self.characters)} 个角色")
            return True

        self._character_loading = True
        source_label = f"版本 {version_timestamp}" if version_timestamp is not None else "当前 Lua"
        self.status_bar.showMessage(f"正在解析角色数据（{source_label}）...")
        if automatic and getattr(self, "_import_progress_dialog", None):
            self._import_progress_dialog.setLabelText("自动解析角色数据\n准备读取 Lua...")
            self._import_progress_dialog.setRange(0, 100)
            self._import_progress_dialog.setValue(0)
        QApplication.processEvents()
        logger.info(f"开始加载角色数据，version={version_timestamp}, lua_dir: {lua_dir}")

        def on_progress(prog, msg):
            self.dl_progress.setValue(prog)
            self.status_bar.showMessage(msg)
            if automatic and getattr(self, "_import_progress_dialog", None):
                self._import_progress_dialog.setLabelText(f"自动解析角色数据\n{msg}")
                self._import_progress_dialog.setRange(0, 100)
                self._import_progress_dialog.setValue(prog)
            QApplication.processEvents()

        # 实际解析委托给 app.core.character_loader.load_character_data（纯解析引擎）
        characters, characters_full, word_map = load_character_data(lua_dir, on_progress)
        if not characters_full:
            self._character_loading = False
            self.status_bar.showMessage("角色 Lua 未解析出有效数据，保留已有角色数据")
            return False
        self.word_map = word_map
        if version_timestamp is not None:
            baseline = None
            if not repository.get("current_characters"):
                baseline = self._load_character_cache(validate_source=False)
            merged = merge_character_snapshot(
                CHARACTER_DATA_DIR,
                version_timestamp,
                characters_full,
                source_dir=lua_dir,
                baseline_characters=baseline,
            )
            self.characters_full = merged["characters_full"]
            self.character_unread = merged["unread"]
            self.character_source_version = int(version_timestamp)
        else:
            # 兼容升级前的根目录 output/lua，不阻断旧用户第一次启动。
            self.characters_full = characters_full
            self.character_unread = {}
            self._save_character_cache(characters_full, lua_dir)
        self.characters = self._derive_character_index(self.characters_full)

        self.dl_progress.setValue(100)
        self._populate_character_table()
        self._character_data_loaded = len(self.characters) > 0
        self._character_loading = False

        if len(self.characters) > 0:
            action = "自动解析完成" if automatic else "角色数据加载完成"
            unread_count = len(self.character_unread)
            self.status_bar.showMessage(
                f"{action}: {len(self.characters)} 个角色，{unread_count} 个新/变更"
            )
        else:
            self.status_bar.showMessage("角色数据加载完成: 无匹配角色")

        # 保留旧缓存文件作为迁移期兼容产物；正式状态以角色仓库为准。
        self._save_character_cache(self.characters_full, lua_dir)
        self._refresh_unread_badges()
        return True

    def _restore_character_data(self):
        """只从本地仓库/缓存恢复角色数据，不触发 Lua 解析。"""
        repository = load_character_repository(CHARACTER_REPOSITORY_PATH)
        if repository_characters(repository):
            self._apply_character_repository(repository)
            self._character_data_loaded = bool(self.characters)
            self._character_loading = False
            self.status_bar.showMessage(f"角色数据从本地仓库加载: {len(self.characters)} 个角色")
            return True

        # 旧版本只有 characters_full.json，没有版本仓库。这里宁可显示本地
        # 缓存，也不在切换页面时现场解析；用户可用“开始解析”主动刷新。
        cached = self._load_character_cache(validate_source=False)
        if not cached:
            return False
        self.characters_full = cached
        self.character_unread = character_unread_status(repository)
        self.character_source_version = None
        self.characters = self._derive_character_index(self.characters_full)
        self._character_data_loaded = bool(self.characters)
        self._character_loading = False
        self._populate_character_table()
        self._refresh_unread_badges()
        self.status_bar.showMessage(f"角色数据从本地缓存加载: {len(self.characters)} 个角色")
        return self._character_data_loaded

    def _latest_lua_source(self):
        """返回最新版本 Lua 目录；兼容升级前的 output/lua 根目录。"""
        version = latest_lua_version(LUA_OUTPUT_DIR)
        if version is not None:
            return version, version_directory(LUA_OUTPUT_DIR, version)
        if has_character_sources(LUA_OUTPUT_DIR):
            return None, LUA_OUTPUT_DIR
        return None, None

    def _has_character_source(self, lua_dir=None):
        """检查指定 Lua 目录是否具备自动角色解析所需的 Base 文件。"""
        if lua_dir is None:
            _version, lua_dir = self._latest_lua_source()
        return bool(lua_dir and has_character_sources(lua_dir))

    def _apply_character_repository(self, repository):
        """从角色仓库恢复当前数据和未读状态。"""
        self.characters_full = repository_characters(repository)
        self.character_unread = character_unread_status(repository)
        version = repository.get("current_version")
        self.character_source_version = int(version) if version is not None else None
        self.characters = self._derive_character_index(self.characters_full)
        self._character_data_loaded = bool(self.characters)
        self._populate_character_table()
        self._refresh_unread_badges()

    def _character_source_mtime(self, lua_dir=LUA_OUTPUT_DIR):
        """返回角色源文件的最大 mtime（用于缓存失效）；缺失返回 0"""
        return source_mtime(lua_dir, CHARACTER_SOURCE_FILES)

    def _derive_character_index(self, characters_full):
        """从完整角色数据派生表格索引列表（复用 load_character_data 的过滤逻辑）"""
        characters = derive_character_index(characters_full)
        for item in characters:
            item["change_status"] = self.character_unread.get(str(item.get("char_id")))
        return characters

    def _save_character_cache(self, characters_full, lua_dir=LUA_OUTPUT_DIR):
        """留存完整角色数据到 output/character_data/characters_full.json"""
        try:
            out_json = os.path.join(CHARACTER_DATA_DIR, "characters_full.json")
            save_character_cache_file(out_json, characters_full, self._character_source_mtime(lua_dir))
            logger.info(f"已留存完整角色数据到 {out_json}")
        except Exception as e:
            logger.warning(f"留存角色数据失败: {e}")

    def _load_character_cache(self, lua_dir=LUA_OUTPUT_DIR, validate_source=True):
        """读完整角色数据缓存；源文件 mtime 变化时返回 None 触发重解析"""
        out_json = os.path.join(CHARACTER_DATA_DIR, "characters_full.json")
        return load_character_cache_file(
            out_json,
            self._character_source_mtime(lua_dir),
            validate_source=validate_source,
        )

    def _auto_parse_after_lua_export(self):
        """Lua 导出完成后，仅对最新版本且 Base 文件齐全的结果自动解析。"""
        worker = getattr(self, "_import_worker", None)
        result = getattr(worker, "lua_export_result", None)
        if not result:
            return
        version = result.get("version")
        lua_dir = result.get("directory")
        latest_version = latest_lua_version(LUA_OUTPUT_DIR)
        if not should_auto_parse(version, latest_version, lua_dir):
            if not result.get("character_sources"):
                self.status_bar.showMessage("Lua 已按版本留存，但缺少角色 Base 文件，未自动解析")
                logger.info("跳过 Lua 自动角色解析：版本 %s 缺少必要 Base 文件", version)
                return
            self.status_bar.showMessage(f"历史 Lua 已留存（版本 {version}），非最新版本，不自动解析角色")
            logger.info("跳过 Lua 自动角色解析：导出版本 %s 不是最新版本 %s", version, latest_version)
            return
        self._load_character_data(lua_dir, version, force=True, automatic=True)

    def _populate_character_table(self):
        """填充角色表格"""
        self.character_table.setSortingEnabled(False)
        self.character_table.setRowCount(0)
        self.character_profile_view.clear_profile()
        count = len(self.characters)
        if count == 0:
            self.character_empty.setVisible(True)
            self.character_status.setText("暂无角色数据")
            return
        self.character_empty.setVisible(False)
        self.character_table.setRowCount(count)
        for i, char in enumerate(self.characters):
            idx_item = QTableWidgetItem(str(char.get("display_index", i + 1)))
            idx_item.setTextAlignment(Qt.AlignCenter)
            self.character_table.setItem(i, 0, idx_item)
            name_item = QTableWidgetItem(char.get("name", "未知"))
            self.character_table.setItem(i, 1, name_item)
            badge_item = QTableWidgetItem("新" if char.get("change_status") else "")
            badge_item.setTextAlignment(Qt.AlignCenter)
            if char.get("change_status"):
                badge_item.setForeground(QBrush(QColor(DANGER)))
                badge_item.setToolTip("新版本新增或数据发生变化，打开详情后清除")
            self.character_table.setItem(i, 2, badge_item)
        self.character_status.setText(f"共 {count} 个角色")

    def _filter_character_table(self, text):
        """根据搜索文本过滤角色表格"""
        for i in range(self.character_table.rowCount()):
            name_item = self.character_table.item(i, 1)
            if name_item:
                match = text.lower() in name_item.text().lower()
                self.character_table.setRowHidden(i, not match)

    def _refresh_character_list(self):
        """刷新角色列表（重新加载 Lua 文件，不重新解密）"""
        self._load_character_data(force=True)

    def _on_character_select(self):
        """角色表格选中行时更新详情卡片"""
        rows = self.character_table.selectionModel().selectedRows()
        if not rows:
            self.character_profile_view.clear_profile()
            return
        row = rows[0].row()
        if row < 0 or row >= len(self.characters):
            return
        # 从 self.characters 获取角色信息
        char_item = self.characters[row]
        # 优先使用 char_id 查找
        char_id = char_item.get("char_id", 0)
        char_data = self.characters_full.get(char_id, {})
        # 如果 char_id 找不到，尝试用 raw_id 查找
        if not char_data:
            raw_id = char_item.get("raw_id", 0)
            for cid, cinfo in self.characters_full.items():
                if cinfo.get("raw_id") == raw_id:
                    char_data = cinfo
                    break
        if char_data:
            if clear_character_unread(CHARACTER_DATA_DIR, char_id):
                self.character_unread.pop(str(char_id), None)
                char_item["change_status"] = None
                badge_item = self.character_table.item(row, 2)
                if badge_item:
                    badge_item.setText("")
                self._refresh_unread_badges()
            self._update_character_detail(char_data)

    def _update_character_detail(self, char):
        """把角色资料模型交给 Wiki 详情视图。"""
        self.character_profile_view.set_profile(build_character_profile(char))

    def _export_characters_csv(self):
        """弹出保存对话框并委托无 Qt 的 CSV 导出逻辑。"""
        if not self.characters_full:
            QMessageBox.information(self, "提示", "没有角色数据可导出，请先加载角色视图。")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存 CSV 文件", "characters.csv", "CSV 文件 (*.csv)"
        )
        if not file_path:
            return

        self.status_bar.showMessage("正在生成角色数据...")
        QApplication.processEvents()
        count = export_characters_csv(file_path, self.characters_full)
        if not count:
            QMessageBox.information(self, "提示", "未找到匹配的角色数据。")
            self.status_bar.showMessage("CSV 导出失败：无匹配角色")
            return

        logger.info(f"CSV 导出完成: {file_path} ({count} 个角色)")
        self.status_bar.showMessage(f"CSV 导出完成: {count} 个角色")

    # ========== Lua Decrypt ==========

    def _start_lua_decrypt(self):
        """点击【角色】按钮：只切换视图并读取本地数据，不现场解析 Lua。"""
        if self._show_character:
            # 当前已显示角色视图，切换到版本列表
            self._toggle_character_mode(False)
            return
        self._toggle_character_mode(True)
        if not self._character_data_loaded:
            self._restore_character_data()

    def _manual_load_character(self):
        """手动开始：读 output/lua 并加载角色数据（AS 导入已反编译）"""
        if self._character_loading:
            return
        if not self._has_character_source():
            QMessageBox.information(self, "提示", "未找到完整角色 Lua 数据。\n\n请先导出包含 BaseCard/BaseWord 的版本后再解析。")
            self.status_bar.showMessage("请先导入AS")
            return
        self._load_character_data(force=True)

    def _mark_all_characters_read(self):
        """清除角色仓库中的全部未读状态，并同步所有顶层标签。"""
        cleared = clear_all_character_unread(CHARACTER_DATA_DIR)
        self.character_unread.clear()
        for item in self.characters:
            item["change_status"] = None
        for row in range(self.character_table.rowCount()):
            badge = self.character_table.item(row, 2)
            if badge:
                badge.setText("")
        self._refresh_unread_badges()
        self.status_bar.showMessage("已将全部角色数据标记为已读" if cleared else "当前没有未读角色数据")

    def _populate_character_filter(self):
        """扫描 output/character 的角色目录，填充图片预览的角色过滤下拉框"""
        cb = getattr(self, "character_filter", None)
        if cb is None:
            return
        preview_dir = os.path.join(get_base_dir(), "output", "character")
        roles = scan_preview_roles(preview_dir)
        cur = cb.currentText()
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("全部角色", "")
        for r in roles:
            cb.addItem(r, r)
        idx = cb.findText(cur)
        cb.setCurrentIndex(idx if idx >= 0 else 0)
        cb.blockSignals(False)

    def _on_character_filter_changed(self):
        """角色过滤：只显示选中角色的图片"""
        cb = getattr(self, "character_filter", None)
        if cb is None:
            return
        role = cb.currentData()
        for i in range(self.image_list.count()):
            item = self.image_list.item(i)
            info = item.data(Qt.UserRole)
            png = info.get("png", "") if isinstance(info, dict) else ""
            visible = True
            if role:
                visible = f"/{role}/" in png.replace("\\", "/")
            item.setHidden(not visible)
        self._update_preview_status()

    def _on_preview_selection_changed(self):
        """同步预览区的选择数量，帮助用户确认批量操作范围。"""
        self._update_preview_status()

    def _update_preview_status(self):
        """更新预览图片总数与当前选择数。"""
        if not hasattr(self, "preview_status"):
            return
        total = self.image_list.count()
        selected = len(self.image_list.selectedItems())
        self.preview_status.setText(f"共 {total} 张图片 · 已选 {selected}")

    @timed("图片缩略图加载")
    def _load_preview_images(self):
        """异步加载预览图片"""
        preview_dir = os.path.join(get_base_dir(), "output", "character")
        material_dir = os.path.join(DATA_DIR, "material")

        logger.info(f"开始加载预览图片: {preview_dir}")
        self._populate_character_filter()

        if not os.path.isdir(preview_dir):
            os.makedirs(preview_dir, exist_ok=True)
            logger.warning(f"预览目录不存在，已创建: {preview_dir}")

        # 预扫描 material 目录，建立 文件名→skel路径 映射
        self._skel_map = build_skel_map(material_dir)
        logger.debug(f"扫描到 {len(self._skel_map)} 个 .skel 文件用于路径匹配")

        self._clear_list()
        self._thumb_cache = {}

        if hasattr(self, "_image_worker") and self._image_worker is not None:
            self._image_worker.cancel()
            self._image_worker.wait(2000)

        self.preview_progress.setVisible(True)
        self.preview_progress.setValue(0)
        self.empty_label.setVisible(False)

        self._image_worker = ImageLoadWorker(preview_dir, 150)
        self._image_worker.progress.connect(self._on_load_progress)
        self._image_worker.image_loaded.connect(self._on_thumbnail_loaded)
        self._image_worker.finished_loading.connect(self._on_load_finished)
        self._image_worker.start()

    def _clear_list(self):
        """清空 QListWidget"""
        self.image_list.clear()
        self._image_paths = []

    def _on_load_progress(self, current, total):
        """更新加载进度"""
        self.preview_progress.setMaximum(total)
        self.preview_progress.setValue(current)
        self.preview_progress.setFormat(f"加载中... {current}/{total}")
        self.preview_title.setText(f"角色预览器 · 共 {total} 张图片 · 加载中 {current}/{total}")

    def _on_thumbnail_loaded(self, image_path, thumbnail):
        """添加缩略图到 QListWidget，同时存储 skel/atlas 路径"""
        self._thumb_cache[image_path] = thumbnail
        self._image_paths.append(image_path)

        item = build_preview_item(image_path, thumbnail, self._skel_map)
        self.image_list.addItem(item)

    def _on_load_finished(self, loaded_paths):
        """加载完成回调"""
        self.preview_progress.setVisible(False)
        count = len(loaded_paths)
        self._image_paths = loaded_paths
        self.preview_title.setText(f"角色预览器 · 共 {count} 张图片")

        if count == 0:
            self.empty_label.setVisible(True)
            self._update_preview_status()
            logger.warning("预览目录为空")
        else:
            self.empty_label.setVisible(False)
            self._update_preview_status()

        logger.info(f"加载完成，共 {count} 张图片")
        self.status_bar.showMessage(f"图片预览: 共 {count} 张图片")


    def _show_context_menu(self, position):
        """显示右键菜单（单选/多选自适应），支持仅有 PNG 无 skel/atlas 的项"""
        # 如果右键的项未选中，先清空选择并选中该项
        item_at_pos = self.image_list.itemAt(position)
        if item_at_pos and not item_at_pos.isSelected():
            self.image_list.clearSelection()
            item_at_pos.setSelected(True)

        selected_items = self.image_list.selectedItems()
        if not selected_items:
            return

        # 收集所有选中项的 skel/atlas/png 数据
        entries = []
        png_only_entries = []
        for item in selected_items:
            data = item.data(Qt.UserRole)
            if not data:
                continue
            png_path = data.get("png", "")
            if not png_path or not os.path.exists(png_path):
                continue
            if data.get("skel") and data.get("atlas"):
                entries.append((data["skel"], data["atlas"], png_path))
            else:
                png_only_entries.append(png_path)

        has_skel = len(entries) > 0
        has_png = len(png_only_entries) > 0

        if not has_skel and not has_png:
            return

        menu = QMenu(self)
        menu.setObjectName("contextMenu")

        is_multi = len(selected_items) > 1

        if is_multi:
            # 多选
            if has_skel:
                act_batch_gif = menu.addAction(f"批量导出 GIF（{len(entries)} 个）")
                act_batch_video = menu.addAction(f"批量导出视频（{len(entries)} 个）")
                if has_png:
                    menu.addSeparator()
            if has_png:
                act_open = menu.addAction("打开文件所在目录")
                act_copy = menu.addAction("复制文件")

            action = menu.exec(self.image_list.mapToGlobal(position))
            if has_skel and action == act_batch_gif:
                batch_export_with_dialog(self, entries, "GIF")
            elif has_skel and action == act_batch_video:
                batch_export_with_dialog(self, entries, "MP4")
            elif has_png and action == act_open:
                png_path = png_only_entries[0] if png_only_entries else entries[0][2]
                self._open_file_location(png_path)
            elif has_png and action == act_copy:
                png_path = png_only_entries[0] if png_only_entries else entries[0][2]
                self._copy_file_to_clipboard(png_path)
        else:
            # 单选
            data = selected_items[0].data(Qt.UserRole)
            png_path = data.get("png", "")

            act_open = menu.addAction("打开文件所在目录")
            act_copy = menu.addAction("复制文件")

            if has_skel:
                menu.addSeparator()
                skel_path, atlas_path, _ = entries[0]
                is_composite = is_composite_png(png_path)
                if is_composite:
                    act_export_gif = menu.addAction("导出合成 GIF")
                    act_export_video = menu.addAction("导出合成视频")
                else:
                    act_export_gif = menu.addAction("导出 GIF")
                    act_export_video = menu.addAction("导出视频")

            action = menu.exec(self.image_list.mapToGlobal(position))
            if action == act_open:
                self._open_file_location(png_path)
            elif action == act_copy:
                self._copy_file_to_clipboard(png_path)
            elif has_skel and action == act_export_gif:
                skin_name = extract_skin_name_from_png(png_path)
                if is_composite:
                    export_composite_video(self, png_path, "GIF", skin_name=skin_name)
                else:
                    export_with_dialog(self, skel_path, atlas_path, "GIF", skin_name=skin_name)
            elif has_skel and action == act_export_video:
                skin_name = extract_skin_name_from_png(png_path)
                if is_composite:
                    export_composite_video(self, png_path, "MP4", skin_name=skin_name)
                else:
                    export_with_dialog(self, skel_path, atlas_path, "MP4", skin_name=skin_name)

    def _on_item_clicked(self, item):
        """左键点击缩略图 → 不做任何操作（仅选中）"""
        pass

    def _on_item_double_clicked(self, item):
        """双击缩略图 → 打开独立预览窗口"""
        data = item.data(Qt.UserRole)
        if not data:
            return
        png_path = data.get("png", "")
        if not png_path or not os.path.exists(png_path):
            return

        # 获取 output/character/ 目录下所有 PNG 文件列表，用于上下导航
        output_dir = os.path.dirname(png_path)
        all_pngs = []
        current_index = 0
        if os.path.isdir(output_dir):
            for fname in sorted(os.listdir(output_dir)):
                if fname.lower().endswith(".png"):
                    full_path = os.path.join(output_dir, fname)
                    all_pngs.append(full_path)
                    if os.path.normpath(full_path) == os.path.normpath(png_path):
                        current_index = len(all_pngs) - 1

        if not all_pngs:
            all_pngs = [png_path]
            current_index = 0

        logger.debug(f"双击预览: {png_path}, 索引 {current_index}/{len(all_pngs)}")
        dialog = ImageViewerDialog(all_pngs, current_index, self)
        dialog.exec()

    def _open_file_location(self, file_path):
        """打开文件所在目录并选中该文件"""
        logger.info(f"打开文件所在目录: {file_path}")
        try:
            subprocess.Popen(
                ['explorer', '/select,', os.path.normpath(file_path)]
            )
        except Exception as e:
            logger.error(f"打开文件位置失败: {e}")
            QMessageBox.warning(self, "错误", f"打开文件位置失败:\n{e}")

    def _copy_file_to_clipboard(self, file_path):
        """复制文件到系统剪贴板"""
        logger.info(f"复制文件: {file_path}")
        try:
            mime_data = QMimeData()
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(file_path))])
            QApplication.clipboard().setMimeData(mime_data)
            self.status_bar.showMessage(f"已复制文件: {os.path.basename(file_path)}")
        except Exception as e:
            logger.error(f"复制文件失败: {e}")
            QMessageBox.warning(self, "错误", f"复制文件失败:\n{e}")




    # ========== DELETE ==========

    def _delete_version(self, ts):
        sub = db.get_sub_bundles(ts)
        down = count_downloaded_bundles(sub)
        if down == 0:
            QMessageBox.information(self, "无文件", "没有已下载的 bundle.")
            return
        reply = QMessageBox.question(
            self, "确认删除", f"删除此版本 {down} 个文件?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        logger.info(f"删除版本 {ts}：共 {down} 个已下载文件")
        delete_downloaded_bundles(ts, sub)
        self._load_data()
        logger.info(f"已删除版本 {ts} 的 {down} 个文件，并清空数据库下载状态")
        self.status_bar.showMessage(f"已删除 {down} 个文件.")
        QMessageBox.information(self, "完成", f"已删除 {down} 个文件.")

    # ========== SEED ==========

    def _seed_bundled_version(self):
        seed_bundled_versions(self.version_mgr, BUNDLES_DIR)

    def _check_auto(self):
        cur = self.version_mgr.get_current()
        if not cur:
            self.status_bar.showMessage("首次启动, 自动检查更新...")
            QTimer.singleShot(1500, self._check_update)
        else:
            QTimer.singleShot(500, lambda: self.status_bar.showMessage("就绪"))
