import os
import shutil
import subprocess
import sys

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QMessageBox, QToolBar, QStatusBar,
    QToolButton,
    QCheckBox, QComboBox, QProgressDialog,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer, QSettings

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
from app.bootstrap import build_app_context, create_application_runtime
from app.platform import database as db
from app.platform.diagnostics import logger
from app.platform.paths import get_data_dir, get_base_dir, get_tools_dir

DATA_DIR = get_data_dir()
BUNDLES_DIR = os.path.join(DATA_DIR, "bundles")
LUA_OUTPUT_DIR = os.path.join(get_base_dir(), "output", "lua")
CHARACTER_DATA_DIR = os.path.join(get_base_dir(), "output", "character_data")


class MainWindow(QMainWindow):
    def __init__(self, debug_mode=False, runtime=None):
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
        # 仅保留 Shell 自己拥有的导入任务；各 Feature 的任务由各自 Runtime 管理。
        self._import_worker = None
        self._show_character = False
        self._init_db()
        self.runtime = runtime or create_application_runtime(
            build_app_context(), parent=self
        )
        self._pages = {
            feature.descriptor.key: feature.page for feature in self.runtime.features
        }
        self._shell_actions = self.runtime.install_shell(self)
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

        for feature in self.runtime.features:
            if feature.descriptor.key == "importer":
                continue
            page = feature.page
            page.setVisible(feature.descriptor.key == "versions")
            content_layout.addWidget(page, 1)

        body.addWidget(content, 1)
        root.addLayout(body, 1)

        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(f"""
            QStatusBar {{ background-color:{get_color('BG_SURFACE')}; border-top:1px solid {get_color('BORDER')};
                         padding:4px 12px; color:{get_color('TEXT_SECONDARY')}; font-size:14px; }}
        """)
        self.setStatusBar(self.status_bar)
        self.runtime.registry.bind_status(self.status_bar.showMessage)
        self.runtime.registry.bind_badge(self._refresh_unread_badges)
        self._refresh_unread_badges()
        self._anim_timer = QTimer()
        self._anim_dots = 0

    def _on_version_progress(self, current, total, message):
        if total <= 0:
            self.dl_progress.setVisible(False)
            return
        self.dl_progress.setVisible(True)
        self.dl_progress.setMaximum(total)
        self.dl_progress.setValue(current)
        self.dl_progress.setFormat(message)

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
        self._nav_buttons = {}

        def add_nav_button(feature):
            key = feature.descriptor.key
            badge_key = "home" if key == "versions" else key
            wrapper = QWidget()
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(0, 0, 2, 0)
            wrapper_layout.setSpacing(0)
            button = self._tbtn(
                feature.descriptor.title,
                icon=self._icon(feature.descriptor.icon),
            )
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, key=key: self._activate_feature(key))
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
            self._unread_badges[badge_key] = badge
            self._nav_buttons[key] = button
            return button

        for feature in self.runtime.features:
            if feature.descriptor.key != "importer":
                add_nav_button(feature)
        self.btn_home = self._nav_buttons["versions"]
        self.btn_image_preview = self._nav_buttons["preview"]
        self.btn_audio = self._nav_buttons["audio"]
        self.btn_lua = self._nav_buttons["character"]
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
        unread = dict(self.runtime.registry.badge_states())
        for key, badge in getattr(self, "_unread_badges", {}).items():
            badge.setVisible(bool(unread.get(key, False)))

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

        self._shell_action_buttons = {}
        for action in self._shell_actions:
            button = _side_btn(action.text, action.icon)
            if action.primary:
                button.setProperty("fluentAppearance", "primary")
            button.clicked.connect(action.callback)
            layout.addWidget(button)
            self._shell_action_buttons[action.text] = button
        self.btn_check = self._shell_action_buttons.get("检查更新")
        self.btn_browse = self._shell_action_buttons.get("导入AS")
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

    # ShellPort：bootstrap 编排只通过这些无领域 UI 能力访问 Qt。
    def schedule(self, delay_ms, callback):
        QTimer.singleShot(delay_ms, callback)

    def show_warning(self, title, message):
        QMessageBox.warning(self, title, message)

    def show_information(self, title, message):
        QMessageBox.information(self, title, message)

    def confirm(self, title, message):
        return QMessageBox.question(
            self, title, message, QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        ) == QMessageBox.Yes

    def create_progress_dialog(self, label, cancel_text):
        dialog = QProgressDialog(label, cancel_text, 0, 100, self)
        dialog.setWindowModality(Qt.NonModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumWidth(520)
        dialog.setMinimumHeight(160)
        return dialog

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
        self._pages["versions"].set_visible(visible)

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
        return self.runtime.shell_contribution.delta_hashes(ts)

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
        return self.runtime.shell_contribution.sync_local(ts)

    def _load_data(self):
        self._activate_feature("versions")

    def _get_selected_ts(self):
        """迁移期导入适配：返回版本功能域当前选中的版本。"""
        return self.runtime.shell_contribution.selected_version()

    # ========== BROWSE ==========

    def _import_selected(self):
        self.runtime.shell_contribution.import_selected()
        return

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
        self.runtime.import_workflow.handle_import_finished(
            success,
            message,
            getattr(self, "_import_progress_dialog", None),
            self._finish_import,
        )

    def _finish_import(self, success, message, audio_error=None, cancelled=False):
        """结束导入及其后处理阶段，统一关闭共享弹窗和恢复按钮状态。"""
        self.btn_browse.setEnabled(True)
        self.dl_progress.setVisible(False)
        progress_dialog = getattr(self, "_import_progress_dialog", None)
        if cancelled:
            if progress_dialog:
                progress_dialog.close()
                self._import_progress_dialog = None
            self.status_bar.showMessage("导入已取消，已完成的文件已保留")
            QMessageBox.information(self, "已取消", message)
            return

        if success:
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
        self.runtime.shell_contribution.append_changelog(message)

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

    # ========== IMAGE GALLERY PREVIEW ==========

    def _set_active_view_btn(self, active_btn):
        """高亮当前激活的视图按钮（取消其他）"""
        for btn in self._nav_buttons.values():
            btn.setChecked(btn is active_btn)

    def _activate_feature(self, key):
        """按 Runtime descriptor 激活页面，并执行该页面的首次加载动作。"""
        self.runtime.activate_feature(key)

    def _set_toolbars_visible(self, visible):
        """切换视图时只隐藏侧边栏，顶部导航栏（view_toolbar/debug_toolbar）始终保留"""
        if hasattr(self, "action_toolbar"):
            self.action_toolbar.setVisible(visible)

    def _toggle_preview_mode(self, show_preview):
        """切换预览视图/版本列表"""
        self._activate_feature("preview" if show_preview else "versions")

    # ==================== 音频管理器 ====================

    def _toggle_audio_mode(self, show_audio):
        """切换音频管理器视图"""
        self._activate_feature("audio" if show_audio else "versions")

    # ========== 角色视图 ==========

    ELEMENT_MAP = {
        1: "火焰", 2: "水", 3: "木", 4: "光", 5: "暗",
    }

    def _toggle_character_mode(self, show_character):
        """切换角色视图显示/隐藏"""
        self._activate_feature("character" if show_character else "versions")

    def _start_lua_decrypt(self):
        """点击【角色】按钮：只切换视图并读取本地数据，不现场解析 Lua。"""
        if self._show_character:
            # 当前已显示角色视图，切换到版本列表
            self._activate_feature("versions")
            return
        self._activate_feature("character")

    def _on_feature_progress(self, page, current, total, stage):
        """接入任意 Feature 的统一进度语义。"""
        if total > 0:
            progress = getattr(page, "preview_progress", None)
            if progress is not None:
                progress.setMaximum(total)
                progress.setValue(current)
                progress.setFormat(f"{stage}... {current}/{total}")
        self.status_bar.showMessage(
            f"{stage}: {current}/{total}" if total > 0 else f"{stage}: 处理中..."
        )

    # ========== DELETE ==========

    def _delete_version(self, ts):
        self.runtime.shell_contribution.delete_version(ts)

    # ========== SEED ==========

    def _seed_bundled_version(self):
        self.runtime.shell_contribution.seed()

    def _check_auto(self):
        self.runtime.shell_contribution.schedule_update_check()

    def closeEvent(self, event):
        self.runtime.registry.close()
        super().closeEvent(event)
