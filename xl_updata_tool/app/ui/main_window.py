import os
import shutil
import subprocess
import sys

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QMessageBox, QToolBar, QStatusBar, QApplication,
    QToolButton,
    QCheckBox, QMenu, QComboBox, QProgressDialog,
    QDialog, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer, QMimeData, QUrl, QSettings

try:
    import qtawesome as qta
    QT_AWESOME_AVAILABLE = True
except ImportError:
    qta = None
    QT_AWESOME_AVAILABLE = False

from .theme import (
    ACCENT, TEXT_PRIMARY,
    FORMAL_THEME, THEME_LABEL, apply_theme, get_color, normalize_theme_name,
)
from app.core.bundle_selector import (
    audio_assets_map_path,
    lua_assets_map_path,
    select_audio_bundles,
    select_lua_bundles,
)
from app.core.audio_repository import unread_files as audio_unread_files
from app.core.preview_catalog import scan_preview_roles
from app.core.version_update import append_changelog
from app.core import database as db
from app.core.logger import logger, timed
from app.core.path_utils import get_data_dir, get_base_dir, get_tools_dir

# 拆分后的模块导入
from .dialogs.image_viewer import ImageViewerDialog
from .adapters.spine_adapter import extract_skin_name_from_png, is_composite_png
from app.features.audio.page import AudioPage
from app.features.audio.controller import AudioController
from app.features.versions.page import VersionPage
from app.features.versions.controller import VersionController
from app.features.versions.service import VersionService
from app.features.characters.page import CharacterPage
from app.features.characters.controller import CharacterController
from app.features.characters.service import CharacterService
from app.features.importer.controller import ImportController
from app.features.importer.postprocessing import PostProcessorRegistry
from app.features.importer.service import ImporterService
from app.features.preview.page import PreviewPage
from app.features.preview.controller import PreviewController
from app.features.preview.service import PreviewService
from .features.export_controller import (
    export_composite_video,
    export_with_dialog,
    batch_export_with_dialog,
)
from app.features.preview.item import build_preview_item

DATA_DIR = get_data_dir()
BUNDLES_DIR = os.path.join(DATA_DIR, "bundles")
LUA_OUTPUT_DIR = os.path.join(get_base_dir(), "output", "lua")
CHARACTER_DATA_DIR = os.path.join(get_base_dir(), "output", "character_data")


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
        self.version_service = VersionService(BUNDLES_DIR)
        self.importer_service = ImporterService(
            os.path.join(DATA_DIR, "material"), LUA_OUTPUT_DIR
        )
        self.import_controller = ImportController(self.importer_service, self)
        self.postprocessor_registry = PostProcessorRegistry(("lua", "audio"))
        self.import_controller.progress_stage.connect(self._on_import_progress)
        self.import_controller.stage_finished.connect(self._on_import_stage_finished)
        self.import_controller.category_finished.connect(self._on_import_category_finished)
        self.import_controller.all_finished.connect(self._on_import_all_finished)
        # 后台工作线程实例（避免 AttributeError）
        self._preview_worker = None
        self._batch_worker = None
        self._composite_worker = None
        self._image_worker = None
        self._import_worker = None
        self._pending_import_message = None
        self._show_character = False
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

        self.version_page = VersionPage(self)
        self.version_controller = VersionController(self.version_page, self.version_service, self)
        self.version_header = self.version_page.version_header
        self.version_summary = self.version_page.version_summary
        self.table = self.version_page.table
        content_layout.addWidget(self.version_page, 1)

        # 预览视图容器（默认隐藏）
        self.preview_page = PreviewPage(self)
        self.preview_controller = PreviewController(
            page=self.preview_page,
            service=PreviewService(
                os.path.join(DATA_DIR, "material"),
                os.path.join(get_base_dir(), "output", "character"),
            ),
            parent=self,
        )
        self.preview_container = self.preview_page
        self.preview_controls = self.preview_page.controls
        self.preview_container.setVisible(False)
        content_layout.addWidget(self.preview_page, 1)

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

        # 角色功能域（页面拥有控件，控制器拥有数据和行为）
        self.character_page = CharacterPage(self)
        self.character_controller = CharacterController(
            page=self.character_page,
            service=CharacterService(CHARACTER_DATA_DIR, LUA_OUTPUT_DIR),
            parent=self,
        )
        self.character_page.setVisible(False)
        content_layout.addWidget(self.character_page, 1)

        body.addWidget(content, 1)
        root.addLayout(body, 1)

        # 暴露预览控件引用（供其他方法使用）
        self.preview_title = self.preview_controls["preview_title"]
        self.preview_progress = self.preview_controls["preview_progress"]
        self.image_list = self.preview_controls["image_list"]
        self.empty_label = self.preview_controls["empty_label"]
        self.preview_status = self.preview_controls["preview_status"]
        self.btn_reload = self.preview_controls["btn_reload"]
        self.character_filter = self.preview_controls["character_filter"]

        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(f"""
            QStatusBar {{ background-color:{get_color('BG_SURFACE')}; border-top:1px solid {get_color('BORDER')};
                         padding:4px 12px; color:{get_color('TEXT_SECONDARY')}; font-size:14px; }}
        """)
        self.setStatusBar(self.status_bar)
        self._connect_audio_controller()
        self._connect_version_controller()
        self._connect_character_controller()
        self.preview_page.close_requested.connect(lambda: self._toggle_preview_mode(False))
        self.preview_page.reload_requested.connect(self._force_reload_preview)
        self.preview_controller.progress_changed.connect(self._on_preview_controller_progress)
        self.preview_controller.status_changed.connect(self.status_bar.showMessage)
        self.preview_controller.error.connect(self._on_preview_export_error)
        self.preview_controller.export_finished.connect(self._on_preview_export_finished)
        self.preview_controller.context_menu_requested.connect(self._show_context_menu)
        self.preview_controller.item_double_clicked.connect(self._on_item_double_clicked)
        # 只恢复本地角色仓库/缓存，确保启动时顶层“角色”角标准确；绝不解析 Lua。
        self.character_controller.restore_local()
        self._refresh_unread_badges()
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

    def _connect_version_controller(self):
        """连接版本功能域状态到全局状态栏和下载进度条。"""
        self.version_controller.status_changed.connect(self.status_bar.showMessage)
        self.version_controller.progress_changed.connect(self._on_version_progress)

    def _on_version_progress(self, current, total, message):
        if total <= 0:
            self.dl_progress.setVisible(False)
            return
        self.dl_progress.setVisible(True)
        self.dl_progress.setMaximum(total)
        self.dl_progress.setValue(current)
        self.dl_progress.setFormat(message)

    def _connect_character_controller(self):
        """连接角色功能域的状态信号到应用壳层。"""
        self.character_controller.status_changed.connect(self.status_bar.showMessage)
        self.character_controller.unread_changed.connect(self._refresh_unread_badges)

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
        character_has_unread = bool(
            getattr(getattr(self, "character_controller", None), "has_unread", False)
        )
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
        self.btn_check.clicked.connect(lambda: self.version_controller.check_update())
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
        self.version_page.set_visible(visible)

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
        return self.version_service.delta_hashes(ts)

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
        return self.version_service.sync_local(ts)

    def _load_data(self):
        # 确保版本列表可见（隐藏图片预览、音频和角色视图）
        self._set_version_content_visible(True)
        self.preview_container.setVisible(False)
        self.audio_page.setVisible(False)
        self.character_page.setVisible(False)
        self._show_character = False
        self._set_active_view_btn(self.btn_home)
        self._set_toolbars_visible(True)
        self.version_controller.load()

    def _get_selected_ts(self):
        """迁移期导入适配：返回版本功能域当前选中的版本。"""
        return self.version_controller.selected_version

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
        self._import_worker = self.import_controller.start(
            fs, bundle_dir, as_cli,
            export_categories=export_categories,
            version_timestamp=ts,
            lua_output_dir=LUA_OUTPUT_DIR,
            isolate_bundle_dir=isolate_bundle_dir,
        )

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
        result = getattr(self.import_controller, "last_result", None)
        self._pending_import_message = message
        if "audio" in self.postprocessor_registry.pending(result):
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
        progress_dialog = getattr(self, "_import_progress_dialog", None)
        self._pending_import_message = None

        if cancelled:
            if progress_dialog:
                progress_dialog.close()
                self._import_progress_dialog = None
            self.status_bar.showMessage("导入已取消，已完成的文件已保留")
            QMessageBox.information(self, "已取消", message)
            return

        if success:
            result = getattr(self.import_controller, "last_result", None)
            lua_result = result.lua_export_result if result else getattr(
                getattr(self, "_import_worker", None), "lua_export_result", None
            )
            if result is None or "lua" in self.postprocessor_registry.pending(result):
                self.character_controller.auto_parse_after_lua_export(
                    lua_result,
                    progress_dialog=progress_dialog,
                )
            if progress_dialog:
                progress_dialog.close()
                self._import_progress_dialog = None
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

        if progress_dialog:
            progress_dialog.close()
            self._import_progress_dialog = None
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
        if self.preview_controller.service.has_images():
            # 情况 A：已有图片，直接加载
            logger.info(
                "预览目录已存在 %s 张图片，直接加载",
                len(self.preview_controller.service.image_paths()),
            )
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
        spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")

        # 切换到预览视图（显示进度条）
        self._preview_source = "刚导出"
        self._toggle_preview_mode(True)

        self.status_bar.showMessage("正在处理..." if not force else "强制重新导出中...")
        if hasattr(self, "preview_progress"):
            self.preview_progress.setVisible(True)
            self.preview_progress.setValue(0)

        if not self.preview_controller.start_export(
            spine_cli, force=force, selected_roles=selected_roles
        ):
            self.preview_progress.setVisible(False)

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
        return self.preview_controller.service.cardspine_roles()

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
        self.character_page.setVisible(False)
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
        self.character_page.setVisible(False)
        self._show_character = False
        self.audio_page.setVisible(show_audio)
        if show_audio:
            self.audio_controller.initialize_player()
            # 页面切换只读取最终产物；已加载过的树保留，用户可用“刷新列表”主动重建。
            self.audio_controller.load_catalog()

    def _cancel_preview_worker(self):
        """取消预览导出线程"""
        self.preview_controller.cancel_export()

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
        self.character_page.setVisible(show_character)
        self._show_character = show_character
        if show_character:
            self.character_page.raise_()
            self.character_page.show()

    def _start_lua_decrypt(self):
        """点击【角色】按钮：只切换视图并读取本地数据，不现场解析 Lua。"""
        if self._show_character:
            # 当前已显示角色视图，切换到版本列表
            self._toggle_character_mode(False)
            return
        self._toggle_character_mode(True)
        if not self.character_controller.data_loaded:
            self.character_controller.restore_local()

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
        """通过 PreviewController 异步加载预览图片。"""
        self.preview_controller.load()
        self._skel_map = self.preview_controller.skel_map

    def _on_preview_controller_progress(self, current, total, stage):
        """接入 PreviewController 的统一进度语义。"""
        if total > 0:
            self.preview_progress.setMaximum(total)
            self.preview_progress.setValue(current)
            self.preview_progress.setFormat(f"{stage}... {current}/{total}")
        self.status_bar.showMessage(
            f"{stage}: {current}/{total}" if total > 0 else f"{stage}: 处理中..."
        )

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
        self.version_controller.delete_version(ts)

    # ========== SEED ==========

    def _seed_bundled_version(self):
        self.version_service.seed()

    def _check_auto(self):
        cur = self.version_service.current()
        if not cur:
            self.status_bar.showMessage("首次启动, 自动检查更新...")
            QTimer.singleShot(1500, self._check_update)
        else:
            QTimer.singleShot(500, lambda: self.status_bar.showMessage("就绪"))
