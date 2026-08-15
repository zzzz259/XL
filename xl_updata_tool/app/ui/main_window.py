import os, re, shutil, subprocess, sys, urllib.request, ssl

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QMessageBox, QToolBar, QStatusBar, QApplication,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QToolButton, QHeaderView,
    QListWidgetItem, QFileDialog, QCheckBox, QMenu,
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QMimeData, QUrl
from PySide6.QtGui import QColor, QPixmap

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    QT_MULTIMEDIA_AVAILABLE = True
except ImportError:
    QT_MULTIMEDIA_AVAILABLE = False

from .theme import (
    BASE_STYLESHEET, ACCENT, BG_SURFACE, BG_DARK, BG_ELEVATED, BORDER,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, SUCCESS, WARNING, DANGER, INFO,
)
from .panels import ticks_to_date
from app.core.version_manager import VersionManager
from app.core.downloader import CheckUpdateThread, DownloadWorker
from app.core.bundle_parser import extract_manifest_hashes, fix_bundle_inplace
from app.core import database as db
from app.core.logger import logger
from app.core.path_utils import get_data_dir, get_base_dir, get_tools_dir

# 拆分后的模块导入
from .dialogs.image_viewer import ImageViewerDialog
from .widgets.drag_list import DragListWidget
from .workers.image_loader import ImageLoadWorker
from .workers.preview_export import PreviewExportWorker
from .workers.audio_decrypt import AudioDecryptWorker
from .workers.lua_decrypt import LuaDecryptWorker
from .workers.import_as import ImportASWorker
from .adapters.spine_adapter import extract_skin_name_from_png, is_composite_png
from .views.preview_view import create_preview_view
from .views.audio_view import create_audio_view
from .views.character_view import create_character_view
from .features.export_controller import (
    export_composite_video,
    export_composite_video_with_params,
    export_with_dialog,
    batch_export_with_dialog,
    on_composite_progress,
    on_composite_all_finished,
    start_regular_batch_export,
    on_regular_all_finished,
    on_batch_progress,
    on_batch_one_finished,
    on_batch_all_finished,
)
from app.core.character_loader import load_character_data

DATA_DIR = get_data_dir()
BUNDLES_DIR = os.path.join(DATA_DIR, "bundles")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XL 更新管理工具")
        self.resize(1200, 700)
        self.setMinimumSize(900, 500)
        self.showMaximized()
        self.setStyleSheet(BASE_STYLESHEET)
        self.version_mgr = VersionManager()
        # 后台工作线程实例（避免 AttributeError）
        self._preview_worker = None
        self._audio_worker = None
        self._batch_worker = None
        self._composite_worker = None
        self._image_worker = None
        self._import_worker = None
        self._lua_worker = None
        # 音频播放器相关属性（避免首次访问 AttributeError）
        self._audio_player = None
        self._audio_output = None
        self._audio_files = []
        self._audio_current_path = None
        self._show_character = False
        self._character_data_loaded = False
        self._character_loading = False
        self._pending_refresh = False
        self.character_base = []      # 角色基础信息：{raw_id, name, display_index}
        self.characters = []           # 角色完整数据：{name, element_type, max_hp, atk, def, skills, raw_text}
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
        root.addWidget(self._toolbar())

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["版本", "状态", "Bundle数", "备注", "", "", ""])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 90)
        self.table.setColumnWidth(6, 90)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setStyleSheet(f"""
            QTableWidget {{ background-color:{BG_DARK}; border:none; gridline-color:transparent; }}
            QTableWidget::item {{ padding:14px 12px; font-size:14px; }}
            QTableWidget::item:selected {{ background-color:{ACCENT}; color:#fff; }}
            QHeaderView::section {{ background-color:{BG_SURFACE}; padding:12px 14px;
                border:none; border-bottom:2px solid {BORDER}; font-size:13px;
                font-weight:600; color:{TEXT_SECONDARY}; }}
        """)
        self.table.currentItemChanged.connect(self._on_row_select)
        root.addWidget(self.table, 1)

        # 预览视图容器（默认隐藏）
        self.preview_container, self.preview_controls = create_preview_view(self)
        self.preview_container.setVisible(False)
        root.addWidget(self.preview_container, 1)

        # 音频管理器视图容器（默认隐藏）
        self.audio_container, self.audio_controls = create_audio_view(self)
        self.audio_container.setVisible(False)
        root.addWidget(self.audio_container, 1)

        # 角色视图容器（默认隐藏）
        self.character_container, self.character_controls = create_character_view(self)
        self.character_container.setVisible(False)
        root.addWidget(self.character_container, 1)

        # 暴露预览控件引用（供其他方法使用）
        self.preview_title = self.preview_controls["preview_title"]
        self.preview_progress = self.preview_controls["preview_progress"]
        self.image_list = self.preview_controls["image_list"]
        self.empty_label = self.preview_controls["empty_label"]
        self.preview_status = self.preview_controls["preview_status"]
        self.btn_reload = self.preview_controls["btn_reload"]

        # 暴露音频控件引用
        self.audio_title = self.audio_controls["audio_title"]
        self.audio_table = self.audio_controls["audio_table"]
        self.audio_play_btn = self.audio_controls["audio_play_btn"]
        self.audio_now_playing = self.audio_controls["audio_now_playing"]
        self.audio_position_label = self.audio_controls["audio_position_label"]
        self.audio_slider = self.audio_controls["audio_slider"]
        self.audio_volume = self.audio_controls["audio_volume"]
        self.audio_status = self.audio_controls["audio_status"]

        # 暴露角色控件引用
        self.character_title = self.character_controls["character_title"]
        self.character_search = self.character_controls["character_search"]
        self.character_table = self.character_controls["character_table"]
        self.character_detail = self.character_controls["character_detail"]
        self.character_detail_name = self.character_controls["character_detail_name"]
        self.character_detail_info = self.character_controls["character_detail_info"]
        self.character_status = self.character_controls["character_status"]
        self.character_empty = self.character_controls["character_empty"]

        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(f"""
            QStatusBar {{ background-color:{BG_SURFACE}; border-top:1px solid {BORDER};
                         padding:4px 12px; color:{TEXT_SECONDARY}; font-size:12px; }}
        """)
        self.setStatusBar(self.status_bar)
        self._anim_timer = QTimer()
        self._anim_dots = 0

    def _toolbar(self):
        bar = QToolBar(); bar.setMovable(False)
        bar.addWidget(self._lbl("XL 1.0.1", 16, ACCENT, True))
        bar.addSeparator()
        self.btn_check = self._tbtn("检查更新", True)
        self.btn_check.clicked.connect(self._check_update)
        bar.addWidget(self.btn_check)
        self.btn_browse = self._tbtn("导入AS")
        self.btn_browse.clicked.connect(self._import_selected)
        bar.addWidget(self.btn_browse)
        bar.addSeparator()
        self.btn_image_preview = self._tbtn("图片预览")
        self.btn_image_preview.clicked.connect(lambda: self._toggle_preview_mode(True))
        bar.addWidget(self.btn_image_preview)
        self.btn_audio = self._tbtn("音频")
        self.btn_audio.clicked.connect(lambda: self._toggle_audio_mode(True))
        bar.addWidget(self.btn_audio)
        self.btn_lua = self._tbtn("角色")
        self.btn_lua.clicked.connect(self._start_lua_decrypt)
        bar.addWidget(self.btn_lua)
        r = self._tbtn("刷新"); r.clicked.connect(self._load_data); bar.addWidget(r)
        self.btn_author = self._tbtn("作者")
        self.btn_author.clicked.connect(self._show_author_info)
        bar.addWidget(self.btn_author)
        bar.addSeparator()
        self.dl_progress = QProgressBar()
        self.dl_progress.setFixedHeight(28); self.dl_progress.setFixedWidth(260)
        self.dl_progress.setVisible(False)
        self.dl_progress.setStyleSheet(f"""
            QProgressBar {{ background-color:{BG_ELEVATED}; border:none; border-radius:4px;
                           text-align:center; color:{TEXT_PRIMARY}; font-size:12px; }}
            QProgressBar::chunk {{ background-color:{SUCCESS}; border-radius:4px; }}
        """)
        bar.addWidget(self.dl_progress)
        return bar

    def _lbl(self, t, s=12, c=TEXT_PRIMARY, b=False):
        l = QLabel(t); l.setStyleSheet(f"color:{c};font-size:{s}px;font-weight:{'bold' if b else 'normal'};padding:4px 8px;background:transparent;border:none;"); return l

    def _tbtn(self, t, accent=False):
        b = QToolButton(); b.setText(t)
        if accent: b.setProperty("accent","true"); b.setStyleSheet(b.styleSheet())
        return b

    def _row_btn(self, text, color, tooltip=""):
        b = QPushButton(text)
        b.setFixedHeight(32)
        if "增量" in text or "全量" in text: b.setFixedWidth(82)
        elif "删除" in text: b.setFixedWidth(82)
        else: b.setFixedWidth(64)
        b.setStyleSheet(f"""
            QPushButton {{ background-color:{color}; border:none; border-radius:6px;
                          padding:4px 8px; color:#fff; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ opacity:0.85; }}
        """)
        return b

    # ========== DATA ==========

    def _load_data(self):
        # 确保版本列表可见（隐藏图片预览、音频和角色视图）
        self.table.setVisible(True)
        self.preview_container.setVisible(False)
        self.audio_container.setVisible(False)
        self.character_container.setVisible(False)
        self._show_character = False
        prev = getattr(self, '_sel_ts', None)
        versions = self.version_mgr.refresh()
        self._populate_table(versions)
        if prev:
            self._sel_ts = prev
            for i in range(self.table.rowCount()):
                if self.table.item(i, 0) and self.table.item(i, 0).data(Qt.UserRole) == prev:
                    self.table.selectRow(i); break
        self.status_bar.showMessage(f"已追踪 {len(versions)} 个版本")

    def _populate_table(self, versions):
        self.table.setSortingEnabled(False)
        # fully clear old widgets and rows
        self.table.clearContents()
        self.table.setRowCount(0)
        self.table.setRowCount(len(versions))
        for i, v in enumerate(versions):
            ts, arts, data, other, video, apk, manifest, is_cur, dl, created, notes = v
            dt = ticks_to_date(ts); date_str = dt.strftime("%Y-%m-%d")
            label = date_str
            if is_cur: label += "  [最新]"
            vi = QTableWidgetItem(label); vi.setData(Qt.UserRole, ts)
            if is_cur: vi.setForeground(QColor("#f0a040")); f = vi.font(); f.setBold(True); vi.setFont(f)
            self.table.setItem(i, 0, vi)
            sub = db.get_sub_bundles(ts); total = len(sub) if sub else 0
            down = sum(1 for r in sub if r[2]) if sub else 0
            if total == 0: status = "无Bundle"
            elif down >= total: status = "已下载"
            elif down > 0: status = f"部分 ({down}/{total})"
            else: status = "未下载"
            si = QTableWidgetItem(status)
            if status == "已下载": si.setForeground(QColor(SUCCESS))
            elif "部分" in status: si.setForeground(QColor(WARNING))
            else: si.setForeground(QColor(TEXT_MUTED))
            self.table.setItem(i, 1, si)
            self.table.setItem(i, 2, QTableWidgetItem(f"{total:,}" if total else "-"))
            self.table.setItem(i, 3, QTableWidgetItem(notes or ""))
            # per-row buttons: delta, full, delete
            for col, (txt, clr, cb) in enumerate([
                ("增量下载", SUCCESS, lambda c, t=ts: self._download_version(t, True)),
                ("全量下载", INFO, lambda c, t=ts: self._download_version(t, False)),
                ("删除已下载", DANGER, lambda c, t=ts: self._delete_version(t)),
            ], start=4):
                btn = self._row_btn(txt, clr)
                btn.clicked.connect(cb)
                self.table.setCellWidget(i, col, btn)
        self.table.setSortingEnabled(True)

    def _get_selected_ts(self):
        rows = set(idx.row() for idx in self.table.selectedIndexes())
        if rows:
            r = list(rows)[0]
            it = self.table.item(r, 0)
            if it: return it.data(Qt.UserRole)
        return None

    def _on_row_select(self, current, prev):
        if current:
            it = self.table.item(current.row(), 0)
            if it and it.data(Qt.UserRole):
                self._sel_ts = it.data(Qt.UserRole)

    # ========== CHECK UPDATE ==========

    def _check_update(self):
        # 确保版本列表可见（隐藏图片预览和音频视图）
        self.table.setVisible(True)
        self.preview_container.setVisible(False)
        self.audio_container.setVisible(False)
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
        out_dir = os.path.join(BUNDLES_DIR, "current")
        self.thread = CheckUpdateThread(out_dir, oh)
        self.thread.finished.connect(self._on_update_checked)
        self.thread.error.connect(self._on_check_error)
        self.thread.start()

    def _on_update_checked(self, info, versions, new_hashes, delta):
        self._anim_timer.stop()
        self.btn_check.setEnabled(True)
        ts = versions["timestamp"]
        existing = {r[0] for r in db.get_all_versions()}
        if ts not in existing:
            self.version_mgr.register_version(ts, info, versions)
            db.save_sub_bundles(ts, new_hashes)
            notes = f"新增 {len(delta['added'])} | 移除 {len(delta['removed'])} | 未变 {delta['common']}"
            db.add_notes(ts, notes)
            cnt = len(delta['added'])
            self.status_bar.showMessage(f"发现新版本! 新增 {cnt} 个 bundle.")
            QMessageBox.information(self, "更新完成", f"发现新版本!\n\n{notes}")
        else:
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
        sub = db.get_sub_bundles(ts); ds = {r[0] for r in sub if r[2]}
        label = "增量下载"
        if delta_only:
            vs = self.version_mgr.get_versions(); ph = set()
            for vv in vs:
                if vv[0] < ts and "(delta" not in (vv[9] or ""):
                    ph = set(r[0] for r in db.get_sub_bundles(vv[0])); break
            target = set(ah) - ph
            if not target: target = set(ah)
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
        missing = sorted(target - ds)
        if not missing: QMessageBox.information(self, "已下载", "全部已下载."); return

        self._dl_ts = ts; self._dl_total = len(missing); self._dl_size = 0
        self._dl_count = 0; self._dl_label = label

        # update status cell for this version
        self._update_row_status(ts, f"下载中 0/{len(missing)}")

        self.dl_progress.setVisible(True)
        self.dl_progress.setMaximum(len(missing)); self.dl_progress.setValue(0)
        self.dl_progress.setFormat(f"{label}: 0/{len(missing)}")
        self.status_bar.showMessage(f"{label}: 准备下载 {len(missing)} 个文件...")
        logger.info(f"开始下载版本 {ts}，共 {len(missing)} 个文件")

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
            it = self.table.item(i, 0)
            if it and it.data(Qt.UserRole) == ts:
                si = QTableWidgetItem(text)
                si.setForeground(QColor(SUCCESS))
                self.table.setItem(i, 1, si)
                break
        self.table.blockSignals(False)

    def _dl_done(self, n, f, p, ts):
        try:
            c = db.get_conn()
            c.execute("UPDATE sub_bundles SET local_path=?, downloadable=1 WHERE hash=? AND version_timestamp=?",(p,n,ts))
            c.commit(); c.close()
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
        self.table.setVisible(True)
        self.preview_container.setVisible(False)
        self.audio_container.setVisible(False)
        ts = self._get_selected_ts()
        if not ts: QMessageBox.warning(self, "未选择", "请先选中一个版本."); return

        # 获取该版本所有 Bundle 的本地路径
        sub = db.get_sub_bundles(ts)
        fs = [r[2] for r in sub if r[2] and os.path.exists(r[2])] if sub else []
        if not fs:
            QMessageBox.information(self, "无文件", "此版本没有已下载的 bundle，请先下载.")
            return

        # 计算路径
        bundle_dir = os.path.dirname(fs[0])
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
        logger.info(f"开始导入AS: 版本 {ts}, 共 {len(fs)} 个 bundle 文件")

        # 启动后台工作线程
        self._import_worker = ImportASWorker(fs, bundle_dir, material_dir, as_cli, self)
        self._import_worker.progress_stage.connect(self._on_import_progress)
        self._import_worker.stage_finished.connect(self._on_import_stage_finished)
        self._import_worker.all_finished.connect(self._on_import_all_finished)
        self._import_worker.start()

    def _on_import_progress(self, stage_name, current, total):
        """导入AS进度更新"""
        if total > 0:
            self.dl_progress.setMaximum(total)
            self.dl_progress.setValue(current)
            self.dl_progress.setFormat(f"{stage_name}: {current}/{total}")
        else:
            self.dl_progress.setFormat(f"{stage_name}...")
        self.status_bar.showMessage(f"导入AS: {stage_name} {current}/{total}")

    def _on_import_stage_finished(self, stage_name):
        """导入AS阶段完成"""
        logger.info(f"导入AS阶段完成: {stage_name}")
        self.status_bar.showMessage(f"导入AS: {stage_name} 完成")

    def _on_import_all_finished(self, success, message):
        """导入AS全部完成"""
        self.btn_browse.setEnabled(True)
        self.dl_progress.setVisible(False)
        if success:
            self.status_bar.showMessage("导入AS: 文件已分类完成")
            QMessageBox.information(self, "完成", message)
        else:
            self.status_bar.showMessage("导入AS: 失败")
            QMessageBox.warning(
                self, "失败",
                f"{message}\n\n文件可能损坏，请点击【删除已下载】并重新下载。"
            )

    def _browse_version(self, ts):
        """备用方法：打开资源浏览器窗口（当前未使用，保留以备后用）"""
        sub = db.get_sub_bundles(ts)
        fs = [r[2] for r in sub if r[2] and os.path.exists(r[2])] if sub else []
        if not fs: QMessageBox.information(self, "无文件", "此版本没有已下载的 bundle，请先下载."); return
        from .asset_browser import AssetBrowser
        browser = AssetBrowser(self, fs, ts)
        browser.exec()
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
                subprocess.Popen([sv_exe], shell=True)
            self.status_bar.showMessage("SpineViewer 已启动")
        except Exception as e:
            logger.error(f"启动 SpineViewer 失败: {e}")
            QMessageBox.warning(self, "错误", f"启动 SpineViewer 失败:\n{e}")

    # ========== PREVIEW ==========

    def _preview_images(self):
        """预览图片：检查缓存，有则直接加载，无则导出后加载"""
        output_dir = os.path.join(get_base_dir(), "output", "character")

        os.makedirs(output_dir, exist_ok=True)

        # 检查已有图片
        existing_pngs = [f for f in os.listdir(output_dir) if f.lower().endswith(".png")]

        if existing_pngs:
            # 情况 A：已有图片，直接加载
            logger.info(f"预览目录已存在 {len(existing_pngs)} 张图片，直接加载")
            self._preview_source = "缓存"
            self._toggle_preview_mode(True)
            return

        # 情况 B：没有图片，启动后台线程导出
        logger.info("预览目录为空，开始导出 ...")
        self._start_preview_export(force=False)

    def _start_preview_export(self, force=False):
        """启动后台线程执行 .skel → PNG 导出（含配对合成 + 皮肤导出）

        force=True 时强制重新导出（跳过去重检查）。
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
            material_dir, output_dir, spine_cli, force=force, parent=self
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
        QMessageBox.information(self, "导出完成", summary)
        # 重新加载预览图片
        self._load_preview_images()

    def _on_preview_export_error(self, err_msg):
        """预览导出错误回调"""
        if hasattr(self, "preview_progress") and self.preview_progress:
            self.preview_progress.setVisible(False)
        self.status_bar.showMessage("导出失败")
        QMessageBox.warning(self, "错误", f"预览导出失败:\n{err_msg}")

    def _force_reload_preview(self):
        """重新加载预览图片（不清空目录，只补充缺失的图片）"""
        logger.info("重新加载预览图片，只补充缺失的 ...")
        self.status_bar.showMessage("重新加载预览图片，只补充缺失的 ...")

        # 启动后台线程，force=False 时利用去重逻辑跳过已存在的文件
        self._start_preview_export(force=False)

    # ========== IMAGE GALLERY PREVIEW ==========

    def _toggle_preview_mode(self, show_preview):
        """切换预览视图/版本列表"""
        self.table.setVisible(not show_preview)
        self.preview_container.setVisible(show_preview)
        self.character_container.setVisible(False)
        self._show_character = False
        if show_preview:
            self.audio_container.setVisible(False)
            # 切换到预览时取消音频解密线程
            self._cancel_audio_worker()
            self._load_preview_images()
        else:
            # 离开预览时取消导出线程
            self._cancel_preview_worker()

    # ==================== 音频管理器 ====================

    def _toggle_audio_mode(self, show_audio):
        """切换音频管理器视图"""
        self.table.setVisible(not show_audio)
        self.preview_container.setVisible(False)
        self.character_container.setVisible(False)
        self._show_character = False
        self.audio_container.setVisible(show_audio)
        if show_audio:
            # 切换到音频时取消预览导出线程
            self._cancel_preview_worker()
            self._init_audio_player()
            # 启动后台线程：转换 .bytes → 解密 .bank → 完成后加载列表
            self._start_audio_decrypt(force=False)
        else:
            # 离开音频时取消解密线程
            self._cancel_audio_worker()

    def _cancel_preview_worker(self):
        """取消预览导出线程"""
        if self._preview_worker is not None:
            self._preview_worker.cancel()
            self._preview_worker.wait(2000)
            self._preview_worker = None

    def _cancel_audio_worker(self):
        """取消音频解密线程"""
        if self._audio_worker is not None:
            self._audio_worker.cancel()
            self._audio_worker.wait(2000)
            self._audio_worker = None

    def _start_audio_decrypt(self, force=False):
        """启动后台线程执行音频解密（.bytes → .bank → 解密）"""
        material_dir = os.path.join(DATA_DIR, "material")
        debank_dir = os.path.join(get_tools_dir(), "epic7_debank_v1_0")
        audio_output_dir = os.path.join(get_base_dir(), "output", "audio")

        # 取消已有的解密线程
        self._cancel_audio_worker()

        self.status_bar.showMessage("正在处理音频文件...")

        self._audio_worker = AudioDecryptWorker(
            material_dir, audio_output_dir, debank_dir,
            force=force, parent=self
        )
        self._audio_worker.progress.connect(self._on_audio_decrypt_progress)
        self._audio_worker.finished_decrypt.connect(self._on_audio_decrypt_finished)
        self._audio_worker.error.connect(self._on_audio_decrypt_error)
        self._audio_worker.start()

    def _on_audio_decrypt_progress(self, msg):
        """音频解密进度更新"""
        self.status_bar.showMessage(msg)

    def _on_audio_decrypt_finished(self):
        """音频解密完成回调，自动加载音频列表"""
        self.status_bar.showMessage("音频处理完成")
        self._load_audio_list()

    def _on_audio_decrypt_error(self, err_msg):
        """音频解密错误回调"""
        self.status_bar.showMessage("音频处理失败")
        logger.error(f"音频解密失败: {err_msg}")
        # 即使解密失败也尝试加载已有文件
        self._load_audio_list()

    def _init_audio_player(self):
        """初始化音频播放器"""
        if self._audio_player is not None:
            return
        if not QT_MULTIMEDIA_AVAILABLE:
            logger.warning("QtMultimedia 不可用，音频播放功能受限")
            return
        self._audio_player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._audio_player.setAudioOutput(self._audio_output)
        self._audio_output.setVolume(0.8)
        self._audio_player.positionChanged.connect(self._update_audio_position)
        self._audio_player.durationChanged.connect(self._update_audio_duration)
        self._audio_player.playbackStateChanged.connect(self._on_audio_state_changed)

    def _load_audio_list(self, force_reload=False):
        """扫描 output/audio/ 目录，加载已解密的音频文件列表"""
        audio_output_dir = os.path.join(get_base_dir(), "output", "audio")
        audio_exts = {".wav", ".ogg", ".mp3"}

        self._audio_files = []

        if not os.path.isdir(audio_output_dir):
            self.audio_title.setText("🎵 音频管理器  共 0 个音频文件")
            self.audio_status.setText("已选: 0 个 | 共 0 个音频文件")
            self.audio_table.setRowCount(0)
            logger.info(f"音频输出目录不存在: {audio_output_dir}")
            return

        # 递归扫描 output/audio/ 目录中的音频文件（支持子目录结构）
        for root, dirs, files in os.walk(audio_output_dir):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                filepath = os.path.join(root, f)
                if ext in audio_exts:
                    size = os.path.getsize(filepath)
                    # 使用相对于 output/audio/ 的路径作为显示名，以区分同名文件
                    rel_name = os.path.relpath(filepath, audio_output_dir)
                    self._audio_files.append({
                        "path": filepath,
                        "name": rel_name,
                        "ext": ext.lstrip(".").upper(),
                        "size": size,
                        "duration": None,
                    })

        # 按文件名排序
        self._audio_files.sort(key=lambda x: x["name"])

        # 填充表格
        self.audio_table.setRowCount(len(self._audio_files))
        for i, info in enumerate(self._audio_files):
            # 复选框
            cb = QCheckBox()
            cb.setStyleSheet("background:transparent; border:none;")
            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self.audio_table.setCellWidget(i, 0, cb_widget)

            self.audio_table.setItem(i, 1, QTableWidgetItem(info["name"]))
            self.audio_table.setItem(i, 2, QTableWidgetItem("-"))
            self.audio_table.setItem(i, 3, QTableWidgetItem(info["ext"]))
            self.audio_table.setItem(i, 4, QTableWidgetItem(self._format_size(info["size"])))

        total = len(self._audio_files)
        self.audio_title.setText(f"🎵 音频管理器  共 {total} 个音频文件")
        self.audio_status.setText(f"已选: 0 个 | 共 {total} 个音频文件")
        logger.info(f"音频列表加载完成: 共 {total} 个文件")
        self.status_bar.showMessage(f"音频列表加载完成: {total} 个文件")

    # ========== 角色视图 ==========

    ELEMENT_MAP = {
        1: "火焰", 2: "水", 3: "木", 4: "光", 5: "暗",
    }

    def _toggle_character_mode(self, show_character):
        """切换角色视图显示/隐藏"""
        self.table.setVisible(not show_character)
        self.preview_container.setVisible(False)
        self.audio_container.setVisible(False)
        self.character_container.setVisible(show_character)
        self._show_character = show_character
        if show_character:
            # 隐藏其他视图时取消相关线程
            self._cancel_audio_worker()
            self._cancel_preview_worker()
            # 检查是否有待刷新的数据
            if self._pending_refresh:
                self._pending_refresh = False
                self._load_character_data()
            self.character_container.raise_()
            self.character_container.show()

    def _load_character_data(self):
        """从解密后的 Lua 文件中加载角色数据（UI 协调层）"""
        self._character_loading = True
        self.status_bar.showMessage("正在加载角色数据...")
        QApplication.processEvents()
        lua_dir = os.path.join(DATA_DIR, "material", "assets", "lua")
        logger.info(f"开始加载角色数据，lua_dir: {lua_dir}")

        # 清空旧数据
        self.character_base = []
        self.characters = []
        self.characters_full = {}
        self.word_map = {}

        def on_progress(prog, msg):
            self.dl_progress.setValue(prog)
            self.status_bar.showMessage(msg)
            QApplication.processEvents()

        # 实际解析委托给 app.core.character_loader.load_character_data（纯解析引擎）
        characters, characters_full, word_map = load_character_data(lua_dir, on_progress)
        self.word_map = word_map
        self.characters_full = characters_full
        self.characters = characters

        self.dl_progress.setValue(100)
        self._populate_character_table()
        self._character_data_loaded = len(self.characters) > 0
        self._character_loading = False

        if len(self.characters) > 0:
            self.status_bar.showMessage(f"角色数据加载完成: {len(self.characters)} 个角色")
        else:
            self.status_bar.showMessage("角色数据加载完成: 无匹配角色")

    def _populate_character_table(self):
        """填充角色表格"""
        self.character_table.setSortingEnabled(False)
        self.character_table.setRowCount(0)
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
        self._load_character_data()

    def _on_character_select(self):
        """角色表格选中行时更新详情卡片"""
        rows = self.character_table.selectionModel().selectedRows()
        if not rows:
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
            self._update_character_detail(char_data)

    def _update_character_detail(self, char):
        """更新右侧详情卡片（展示文图效果的全部内容，放入 QScrollArea）"""
        # 辅助函数
        def _nl(text):
            return text.replace("\n", "<br/>") if text else ""

        def _pct(v):
            if v and v != "未知":
                try:
                    n = int(v)
                    return f"{n // 100}%"
                except (ValueError, TypeError):
                    return str(v)
            return "-"

        name = char.get("name", "未知").split('/')[0] if '/' in str(char.get("name", "")) else char.get("name", "未知")
        self.character_detail_name.setText(name)

        # ---- 基础信息 ----
        star = char.get("star", "未知")
        profession = char.get("profession", "未知")
        element = char.get("element", "未知")
        birthday = char.get("birthday", "未知")
        height = char.get("height", "未知")
        faction = char.get("faction", "未知")
        cv = char.get("cv", "未知")
        description = _nl(char.get("description", "未知"))

        # ---- 战斗属性 ----
        init_hp = char.get("init_hp", 0)
        init_atk = char.get("init_atk", 0)
        init_def = char.get("init_def", 0)
        max_hp = char.get("max_hp", 0)
        max_atk = char.get("max_atk", 0)
        max_def = char.get("max_def", 0)

        crt = _pct(char.get("crt", "0"))
        blk = _pct(char.get("blk", "0"))
        crt_int = _pct(char.get("crt_int", "0"))
        blk_int = _pct(char.get("blk_int", "0"))
        spd_move = char.get("spd_move", "未知")
        spd_atk = char.get("spd_atk", "未知")
        range_atk = char.get("range_atk", "未知")

        # ---- 构建 HTML 内容 ----
        html_parts = []
        html_parts.append(f"<h2>基础信息</h2>")
        html_parts.append(f"<p><b>名称：</b>{name}</p>")
        html_parts.append(f"<p><b>星级：</b>{star}</p>")
        html_parts.append(f"<p><b>职业：</b>{profession}</p>")
        html_parts.append(f"<p><b>属性：</b>{element}</p>")
        html_parts.append(f"<p><b>生日：</b>{birthday}</p>")
        html_parts.append(f"<p><b>身高：</b>{height}</p>")
        html_parts.append(f"<p><b>阵营：</b>{faction}</p>")
        html_parts.append(f"<p><b>CV：</b>{cv}</p>")
        html_parts.append(f"<p><b>简介：</b><br/>{description}</p>")

        html_parts.append(f"<h2>战斗属性</h2>")
        html_parts.append(f"<p><b>初始生命：</b>{init_hp} → <b>满级生命：</b>{max_hp}</p>")
        html_parts.append(f"<p><b>初始攻击：</b>{init_atk} → <b>满级攻击：</b>{max_atk}</p>")
        html_parts.append(f"<p><b>初始防御：</b>{init_def} → <b>满级防御：</b>{max_def}</p>")
        html_parts.append(f"<p><b>暴击：</b>{crt} &nbsp; <b>格挡：</b>{blk}</p>")
        html_parts.append(f"<p><b>暴击伤害：</b>{crt_int} &nbsp; <b>格挡效果：</b>{blk_int}</p>")
        html_parts.append(f"<p><b>移动速度：</b>{spd_move} &nbsp; <b>攻击速度：</b>{spd_atk} &nbsp; <b>攻击范围：</b>{range_atk}</p>")

        # ---- 技能 ----
        html_parts.append(f"<h2>技能</h2>")
        skill_fields = [
            ("队长技能", "leader_skill"),
            ("普通技能", "normal_skill"),
            ("特殊技能", "special_skill"),
            ("爆发技能", "burst_skill"),
        ]
        for label, field in skill_fields:
            val = char.get(field, "未知")
            if val and val != "未知":
                html_parts.append(f"<p><b>{label}：</b><br/>{_nl(val)}</p>")

        # 被动技能
        for i in range(1, 4):
            val = char.get(f"passive_skill_{i}", "未知")
            if val and val != "未知":
                html_parts.append(f"<p><b>被动技能{i}：</b><br/>{_nl(val)}</p>")

        # 觉醒技能
        for i in range(1, 6):
            val = char.get(f"awakening_skill_{i}", "未知")
            if val and val != "未知":
                html_parts.append(f"<p><b>觉醒技能{i}：</b><br/>{_nl(val)}</p>")

        # ---- 徽章推荐 ----
        badge_info = char.get("badge_info", "")
        if badge_info:
            html_parts.append(f"<h2>徽章推荐</h2>")
            html_parts.append(f"<p>{_nl(badge_info)}</p>")

        # ---- 突破消耗 ----
        breakthrough_costs = char.get("breakthrough_costs", ["", "", "", ""])
        if any(breakthrough_costs):
            html_parts.append(f"<h2>突破消耗</h2>")
            labels = ["突破一", "突破二", "突破三", "突破四"]
            for i, cost in enumerate(breakthrough_costs):
                if cost:
                    html_parts.append(f"<p><b>{labels[i]}：</b>{cost}</p>")

        # ---- 技能升级消耗 ----
        normal_upgrade_costs = char.get("normal_skill_upgrade_costs", ["", "", ""])
        if any(normal_upgrade_costs):
            html_parts.append(f"<h2>普通技能升级消耗</h2>")
            for i, cost in enumerate(normal_upgrade_costs):
                if cost:
                    html_parts.append(f"<p><b>升级{i+1}：</b>{cost}</p>")

        passive_upgrade_costs = char.get("passive_skill_upgrade_costs", ["", "", ""])
        if any(passive_upgrade_costs):
            html_parts.append(f"<h2>被动技能升级消耗</h2>")
            for i, cost in enumerate(passive_upgrade_costs):
                if cost:
                    html_parts.append(f"<p><b>升级{i+1}：</b>{cost}</p>")

        # ---- 语音 ----
        html_parts.append(f"<h2>语音</h2>")
        voice_labels = [
            "成员报道", "问候", "闲谈1", "闲谈2", "闲谈3",
            "突破感悟1", "突破感悟2", "突破感悟3",
            "觉醒感悟1", "觉醒感悟2", "觉醒感悟3", "觉醒感悟4", "觉醒感悟5",
            "出战", "攻击1", "攻击2", "攻击3", "战技1", "战技2",
            "总攻技1", "总攻技2", "总攻技3", "受击1", "受击2", "受击3",
            "重伤", "退场", "作战胜利", "作战失败",
            "生日祝福", "新年祝福", "情人节祝福", "万圣节祝福", "圣诞节祝福"
        ]
        for i in range(34):
            voice_key = f"voice_{i+1}"
            voice_text = char.get(voice_key, "")
            if voice_text:
                html_parts.append(f"<p><b>{voice_labels[i]}：</b>{voice_text}</p>")

        # ---- 故事 ----
        html_parts.append(f"<h2>故事</h2>")
        story_fields = [
            ("个人情报", "personal_info"),
            ("风闻", "anecdote"),
            ("记录", "record"),
            ("逸事", "anecdote2"),
        ]
        for label, field in story_fields:
            val = char.get(field, "未知")
            if val and val != "未知":
                html_parts.append(f"<p><b>{label}：</b><br/>{_nl(val)}</p>")

        full_html = "<html><body>" + "<br/>".join(html_parts) + "</body></html>"
        self.character_detail_info.setText(full_html)

    def _export_characters_csv(self):
        """导出角色数据为 CSV 文件（直接从 self.characters_full 缓存写入）"""
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

        headers = [
            "ID", "Name", "Star", "Profession", "Element", "Birthday", "Height", "Faction", "CV", "Description",
            "Init_ATK", "Init_DEF", "Init_HP", "Max_ATK", "Max_DEF", "Max_HP",
            "CRT", "BLK", "CRT_INT", "BLK_INT", "SPD_MOVE", "SPD_ATK", "RANGE_ATK", "WEIGHT",
            "Leader_Skill", "Normal_Skill", "Special_Skill", "Burst_Skill",
            "Passive_Skill_1", "Passive_Skill_2", "Passive_Skill_3",
            "觉醒1", "觉醒2", "觉醒3", "觉醒4", "觉醒5",
            "成员报道", "问候", "闲谈1", "闲谈2", "闲谈3",
            "突破感悟1", "突破感悟2", "突破感悟3",
            "觉醒感悟1", "觉醒感悟2", "觉醒感悟3", "觉醒感悟4", "觉醒感悟5",
            "出战", "攻击1", "攻击2", "攻击3", "战技1", "战技2",
            "总攻技1", "总攻技2", "总攻技3", "受击1", "受击2", "受击3",
            "重伤", "退场", "作战胜利", "作战失败",
            "生日祝福", "新年祝福", "情人节祝福", "万圣节祝福", "圣诞节祝福",
            "个人情报", "风闻", "记录", "逸事",
            "推荐徽章",
            "突破一", "突破二", "突破三", "突破四",
            "普通技能升级一", "普通技能升级二", "普通技能升级三",
            "被动技能升级一", "被动技能升级二", "被动技能升级三"
        ]

        # 过滤 ID 范围 80100001~80101999
        filtered_full = {}
        for char_id, char_info in self.characters_full.items():
            raw_id = char_info.get("raw_id", 0)
            if 80100001 <= raw_id <= 80101999:
                filtered_full[char_id] = char_info

        if not filtered_full:
            QMessageBox.information(self, "提示", "未找到匹配的角色数据。")
            self.status_bar.showMessage("CSV 导出失败：无匹配角色")
            return

        import csv
        with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(headers)

            for char_id, char_info in sorted(filtered_full.items(), key=lambda x: x[1].get("raw_id", 0)):
                def _g(key, default=""):
                    v = char_info.get(key, default)
                    if v is None:
                        return default
                    return str(v)

                def _skill(field, default=""):
                    val = char_info.get(field, default)
                    if val and val != "未知":
                        return val.replace("\n", " | ").replace("\"", "'")
                    return default

                def _voice(idx):
                    v = char_info.get(f"voice_{idx}", "")
                    if v:
                        return v.strip('"')
                    return ""

                row = [
                    _g("raw_id"),
                    _g("name", "").split('/')[0] if '/' in str(_g("name", "")) else _g("name", ""),
                    _g("star"),
                    _g("profession"),
                    _g("element"),
                    _g("birthday"),
                    _g("height"),
                    _g("faction"),
                    _g("cv"),
                    _g("description", "").replace("\n", " "),
                    _g("init_atk"), _g("init_def"), _g("init_hp"),
                    _g("max_atk"), _g("max_def"), _g("max_hp"),
                    _g("crt"), _g("blk"), _g("crt_int"), _g("blk_int"),
                    _g("spd_move"), _g("spd_atk"), _g("range_atk"), _g("weight"),
                    _skill("leader_skill"),
                    _skill("normal_skill"),
                    _skill("special_skill"),
                    _skill("burst_skill"),
                    _skill("passive_skill_1"),
                    _skill("passive_skill_2"),
                    _skill("passive_skill_3"),
                    _skill("awakening_skill_1"),
                    _skill("awakening_skill_2"),
                    _skill("awakening_skill_3"),
                    _skill("awakening_skill_4"),
                    _skill("awakening_skill_5"),
                ]
                # 34条语音
                for i in range(1, 35):
                    row.append(_voice(i))
                # 故事
                row.append(_g("personal_info", "").replace("\n", " "))
                row.append(_g("anecdote", "").replace("\n", " "))
                row.append(_g("record", "").replace("\n", " "))
                row.append(_g("anecdote2", "").replace("\n", " "))
                # 徽章推荐
                row.append(_g("badge_info", "").replace("\n", " | "))
                # 突破消耗
                breakthrough_costs = char_info.get("breakthrough_costs", ["", "", "", ""])
                for i in range(4):
                    row.append(breakthrough_costs[i] if i < len(breakthrough_costs) else "")
                # 普通技能升级消耗
                normal_upgrade_costs = char_info.get("normal_skill_upgrade_costs", ["", "", ""])
                for i in range(3):
                    row.append(normal_upgrade_costs[i] if i < len(normal_upgrade_costs) else "")
                # 被动技能升级消耗
                passive_upgrade_costs = char_info.get("passive_skill_upgrade_costs", ["", "", ""])
                for i in range(3):
                    row.append(passive_upgrade_costs[i] if i < len(passive_upgrade_costs) else "")

                writer.writerow(row)

        logger.info(f"CSV 导出完成: {file_path} ({len(filtered_full)} 个角色)")
        self.status_bar.showMessage(f"CSV 导出完成: {len(filtered_full)} 个角色")

    # ========== Lua Decrypt ==========

    def _start_lua_decrypt(self):
        """点击【角色】按钮，切换角色视图显示/隐藏"""
        if self._show_character:
            # 当前已显示角色视图，切换到版本列表
            self._toggle_character_mode(False)
            return

        # 切换到角色视图
        self._toggle_character_mode(True)

        # 如果已加载过数据，直接显示
        if self._character_data_loaded:
            self._populate_character_table()
            return

        # 如果正在加载中，不重复启动
        if self._character_loading:
            return

        # 计算路径
        lua_dir = os.path.join(DATA_DIR, "material", "assets", "lua")
        tools_dir = get_tools_dir()
        unluac_path = os.path.join(tools_dir, "lua", "unluac.jar")
        opmap_path = os.path.join(tools_dir, "lua", "opmap")

        # 依赖检查
        if not os.path.isfile(unluac_path):
            logger.error(f"unluac.jar 不存在: {unluac_path}")
            QMessageBox.warning(self, "错误", f"unluac.jar 不存在:\n{unluac_path}")
            return
        if not os.path.isfile(opmap_path):
            logger.error(f"opmap 文件不存在: {opmap_path}")
            QMessageBox.warning(self, "错误", f"opmap 文件不存在:\n{opmap_path}")
            return

        if not os.path.isdir(lua_dir):
            logger.info(f"Lua 目录不存在: {lua_dir}")
            self.status_bar.showMessage("Lua 目录不存在，请先导入资源")
            return

        # 取消已有线程
        if self._lua_worker is not None:
            self._lua_worker.cancel()
            self._lua_worker.wait(2000)

        # 标记加载中
        self._character_loading = True
        self._character_data_loaded = False

        # 禁用按钮，显示进度
        self.btn_lua.setEnabled(False)
        self.dl_progress.setVisible(True)
        self.dl_progress.setValue(0)
        self.dl_progress.setFormat("Lua 解密准备中...")
        self.status_bar.showMessage("正在解密中...")
        logger.info(f"开始 Lua 解密: 目录 {lua_dir}")

        # 启动后台线程
        self._lua_worker = LuaDecryptWorker(lua_dir, unluac_path, opmap_path, self)
        self._lua_worker.progress.connect(self._on_lua_decrypt_progress)
        self._lua_worker.finished.connect(self._on_lua_decrypt_finished)
        self._lua_worker.error.connect(self._on_lua_decrypt_error)
        self._lua_worker.file_done.connect(self._on_lua_file_done)
        self._lua_worker.start()

    def _on_lua_decrypt_progress(self, msg):
        """Lua 解密进度更新"""
        self.status_bar.showMessage(msg)
        # 尝试从消息中解析进度数值
        m = re.search(r'(\d+)/(\d+)', msg)
        if m:
            self.dl_progress.setMaximum(int(m.group(2)))
            self.dl_progress.setValue(int(m.group(1)))
        self.dl_progress.setFormat(msg[:50])

    def _on_lua_decrypt_finished(self, success_count, fail_count):
        """Lua 解密完成 → 加载角色数据"""
        self.btn_lua.setEnabled(True)
        self.dl_progress.setVisible(False)
        self._character_loading = False
        total = success_count + fail_count
        if total == 0:
            self.status_bar.showMessage("Lua 解密: 无待处理文件")
            self._character_data_loaded = False
            return
        msg = f"Lua 解密完成: 成功 {success_count} 个, 失败 {fail_count} 个"
        self.status_bar.showMessage(msg)
        logger.info(msg)
        # 自动加载角色数据
        self._load_character_data()

    def _on_lua_decrypt_error(self, err_msg):
        """Lua 解密错误"""
        self.btn_lua.setEnabled(True)
        self.dl_progress.setVisible(False)
        self._character_loading = False
        logger.error(f"Lua 解密错误: {err_msg}")
        self.status_bar.showMessage(f"Lua 解密错误: {err_msg}")
        QMessageBox.warning(self, "Lua 解密错误", err_msg)

    def _on_lua_file_done(self, filename):
        """当 BaseWord_cn.lua 或 BaseCard.lua 解密完成时，立即加载角色数据"""
        logger.info(f"角色数据文件解密完成: {filename}，立即加载")
        # 如果当前角色视图可见且未在加载中，则刷新数据
        if self.character_container.isVisible() and not self._character_loading:
            self._load_character_data()
            # 强制刷新表格和状态
            self._populate_character_table()
            if len(self.characters) > 0:
                self.character_status.setText(f"共 {len(self.characters)} 个角色")
            else:
                self.character_status.setText("暂无角色数据")
        else:
            # 若视图不可见，设置待刷新标志，下次打开时自动加载
            self._pending_refresh = True

    def _export_selected_audio(self):
        """导出选中的音频文件"""
        selected = []
        for i in range(self.audio_table.rowCount()):
            cb_widget = self.audio_table.cellWidget(i, 0)
            if cb_widget:
                cb = cb_widget.findChild(QCheckBox)
                if cb and cb.isChecked():
                    selected.append(self._audio_files[i])

        if not selected:
            QMessageBox.information(self, "提示", "请先勾选要导出的音频文件")
            return

        dst_dir = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not dst_dir:
            return

        success = 0
        for info in selected:
            try:
                dst = os.path.join(dst_dir, info["name"])
                shutil.copy2(info["path"], dst)
                success += 1
            except (OSError, PermissionError) as e:
                logger.error(f"导出失败 {info['name']}: {e}")

        QMessageBox.information(self, "导出完成", f"成功导出 {success} 个音频文件到:\n{dst_dir}")
        logger.info(f"导出 {success} 个音频文件到 {dst_dir}")

    def _play_selected_audio(self):
        """播放选中的音频文件（取第一个）"""
        for i in range(self.audio_table.rowCount()):
            cb_widget = self.audio_table.cellWidget(i, 0)
            if cb_widget:
                cb = cb_widget.findChild(QCheckBox)
                if cb and cb.isChecked():
                    info = self._audio_files[i]
                    self._play_audio_file(info["path"], info["name"])
                    return

        # 如果没有勾选，尝试播放当前选中行
        row = self.audio_table.currentRow()
        if row >= 0 and row < len(self._audio_files):
            info = self._audio_files[row]
            self._play_audio_file(info["path"], info["name"])

    def _on_audio_double_click(self, index):
        """双击播放音频"""
        row = index.row()
        if row < 0 or row >= len(self._audio_files):
            return
        info = self._audio_files[row]
        self._play_audio_file(info["path"], info["name"])

    def _play_audio_file(self, filepath, filename):
        """播放指定音频文件"""
        if not QT_MULTIMEDIA_AVAILABLE or self._audio_player is None:
            QMessageBox.warning(self, "错误", "音频播放器不可用（QtMultimedia 未安装）")
            return

        if not os.path.exists(filepath):
            QMessageBox.warning(self, "错误", f"文件不存在: {filepath}")
            return

        self._audio_current_path = filepath
        self._audio_player.setSource(QUrl.fromLocalFile(filepath))
        self._audio_player.play()
        self.audio_now_playing.setText(f"▶ {filename}")
        self.audio_play_btn.setText("⏸")
        self.audio_play_btn.setEnabled(True)
        self.audio_slider.setEnabled(True)
        logger.info(f"播放音频: {filename}")

    def _toggle_play(self):
        """播放/暂停切换"""
        if not self._audio_player:
            return
        if self._audio_player.playbackState() == QMediaPlayer.PlayingState:
            self._audio_player.pause()
        else:
            self._audio_player.play()

    def _on_audio_state_changed(self, state):
        """播放状态变化"""
        if state == QMediaPlayer.PlayingState:
            self.audio_play_btn.setText("⏸")
        elif state in (QMediaPlayer.PausedState, QMediaPlayer.StoppedState):
            self.audio_play_btn.setText("▶")

    def _update_audio_position(self, position):
        """更新播放进度"""
        duration = self._audio_player.duration() if self._audio_player else 0
        if duration > 0:
            self.audio_slider.setRange(0, duration)
            self.audio_slider.setValue(position)
        pos_str = self._format_duration(position)
        dur_str = self._format_duration(duration)
        self.audio_position_label.setText(f"{pos_str} / {dur_str}")

    def _update_audio_duration(self, duration):
        """总时长变化"""
        if duration > 0:
            self.audio_slider.setRange(0, duration)

    def _on_audio_slider_moved(self, position):
        """拖动进度条跳转"""
        if self._audio_player:
            self._audio_player.setPosition(position)

    def _set_audio_volume(self, volume):
        """设置音量"""
        if self._audio_output:
            self._audio_output.setVolume(volume / 100.0)

    def _show_audio_context_menu(self, position):
        """音频列表右键菜单"""
        item = self.audio_table.itemAt(position)
        if not item:
            return
        row = item.row()
        if row < 0 or row >= len(self._audio_files):
            return
        info = self._audio_files[row]
        filepath = info["path"]

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background-color:{BG_SURFACE}; border:1px solid {BORDER}; border-radius:6px; padding:4px 0px; }}
            QMenu::item {{ padding:8px 24px; color:{TEXT_PRIMARY}; font-size:13px; border-radius:4px; }}
            QMenu::item:selected {{ background-color:{ACCENT}; color:#fff; }}
        """)

        act_open = menu.addAction("📂 打开文件所在目录")
        act_copy = menu.addAction("📋 复制文件")
        act_play = menu.addAction("🎵 播放")

        action = menu.exec(self.audio_table.mapToGlobal(position))
        if action == act_open:
            self._open_audio_file_location(filepath)
        elif action == act_copy:
            self._copy_audio_file(filepath)
        elif action == act_play:
            self._play_audio_file(filepath, info["name"])

    def _open_audio_file_location(self, file_path):
        """打开文件所在目录"""
        logger.info(f"打开文件所在目录: {file_path}")
        try:
            if sys.platform == "win32":
                subprocess.Popen(['explorer', '/select,', file_path],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                folder = os.path.dirname(file_path)
                subprocess.Popen(['xdg-open', folder] if sys.platform.startswith('linux') else ['open', folder])
        except Exception as e:
            logger.error(f"打开目录失败: {e}")

    def _copy_audio_file(self, file_path):
        """复制文件到剪贴板"""
        if not os.path.exists(file_path):
            return
        try:
            from PySide6.QtGui import QGuiApplication
            clipboard = QGuiApplication.clipboard()
            url = QUrl.fromLocalFile(file_path)
            data = QMimeData()
            data.setUrls([url])
            clipboard.setMimeData(data)
            logger.info(f"已复制文件到剪贴板: {file_path}")
        except Exception as e:
            logger.error(f"复制文件失败: {e}")

    def _get_audio_duration(self, filepath):
        """获取音频时长（毫秒），失败返回 None"""
        try:
            if not QT_MULTIMEDIA_AVAILABLE:
                return None
            # 使用 QMediaPlayer 临时探测（非阻塞方式不可行，这里用文件大小估算）
            # 对于 WAV: duration ≈ filesize / (sample_rate * channels * bits_per_sample/8) * 1000
            # 简化：返回 None，让用户播放时显示实际时长
            return None
        except Exception:
            return None

    @staticmethod
    def _format_duration(ms):
        """毫秒转 mm:ss"""
        if not ms or ms <= 0:
            return "00:00"
        seconds = int(ms / 1000)
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"

    @staticmethod
    def _format_size(size):
        """字节转可读大小"""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"

    def _load_preview_images(self):
        """异步加载预览图片"""
        preview_dir = os.path.join(get_base_dir(), "output", "character")
        material_dir = os.path.join(DATA_DIR, "material")

        logger.info(f"开始加载预览图片: {preview_dir}")

        if not os.path.isdir(preview_dir):
            os.makedirs(preview_dir, exist_ok=True)
            logger.warning(f"预览目录不存在，已创建: {preview_dir}")

        # 预扫描 material 目录，建立 文件名→skel路径 映射
        self._skel_map = {}  # base_name -> (skel_path, atlas_path)
        if os.path.isdir(material_dir):
            for root, dirs, files in os.walk(material_dir):
                for f in files:
                    if f.endswith(".skel"):
                        base = os.path.splitext(f)[0]
                        skel_p = os.path.join(root, f)
                        atlas_p = os.path.join(root, f"{base}.atlas")
                        self._skel_map[base] = (skel_p, atlas_p)
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
        self.preview_title.setText(f"🖼️ 角色预览器  共 {total} 张图片  (加载中 {current}/{total})")

    def _on_thumbnail_loaded(self, image_path, thumbnail):
        """添加缩略图到 QListWidget，同时存储 skel/atlas 路径"""
        self._thumb_cache[image_path] = thumbnail
        self._image_paths.append(image_path)

        # 从 PNG 文件名反查 skel/atlas 路径
        skel_path, atlas_path = self._find_skel_paths(image_path)

        item = QListWidgetItem(QPixmap(thumbnail), "")
        item.setData(Qt.UserRole, {
            "png": image_path,
            "skel": skel_path,
            "atlas": atlas_path,
        })

        fname = os.path.basename(image_path)
        display_name = fname if len(fname) <= 22 else fname[:19] + "..."
        item.setText(display_name)
        item.setToolTip(fname)

        self.image_list.addItem(item)

    def _find_skel_paths(self, png_path):
        """从 PNG 文件名反查对应的 .skel 和 .atlas 路径
        PNG 命名规则:
          - {base}.png              -> skel: {base}.skel
          - {base}_{anim}.png       -> skel: {base}.skel
          - {base}_bg.png           -> skel: {base}_bg.skel
          - {base}_composite.png    -> skel: {base}.skel (角色)
        """
        fname = os.path.splitext(os.path.basename(png_path))[0]

        # 处理 composite（合成图对应角色的 skel）
        if fname.endswith("_composite"):
            base = fname[:-len("_composite")]
        # 处理 _bg 后缀
        elif fname.endswith("_bg"):
            base = fname
        else:
            # 尝试去掉可能的动画后缀
            # 先直接匹配
            if fname in self._skel_map:
                base = fname
            else:
                # 尝试查找 {base}_{anim} 模式
                found_base = None
                for known_base in self._skel_map:
                    if fname.startswith(known_base + "_"):
                        found_base = known_base
                        break
                base = found_base if found_base else fname

        if base in self._skel_map:
            return self._skel_map[base]

        return None, None

    def _on_load_finished(self, loaded_paths):
        """加载完成回调"""
        self.preview_progress.setVisible(False)
        count = len(loaded_paths)
        self._image_paths = loaded_paths
        self.preview_title.setText(f"🖼️ 角色预览器  共 {count} 张图片")

        if count == 0:
            self.empty_label.setVisible(True)
            self.preview_status.setText("共 0 张图片")
            logger.warning("预览目录为空")
        else:
            self.empty_label.setVisible(False)
            self.preview_status.setText(f"共 {count} 张图片")

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
        menu.setStyleSheet(f"""
            QMenu {{ background-color:{BG_SURFACE}; border:1px solid {BORDER}; border-radius:6px;
                     padding:4px 0px; }}
            QMenu::item {{ padding:8px 24px; color:{TEXT_PRIMARY}; font-size:13px; border-radius:4px; }}
            QMenu::item:selected {{ background-color:{ACCENT}; color:#fff; }}
            QMenu::separator {{ height:1px; background-color:{BORDER}; margin:4px 8px; }}
        """)

        is_multi = len(selected_items) > 1

        if is_multi:
            # 多选
            if has_skel:
                act_batch_gif = menu.addAction(f"🎞️ 批量导出 GIF ({len(entries)} 个)")
                act_batch_video = menu.addAction(f"🎬 批量导出视频 ({len(entries)} 个)")
                if has_png:
                    menu.addSeparator()
            if has_png:
                act_open = menu.addAction("📂 打开文件所在目录")
                act_copy = menu.addAction("📋 复制文件")

            action = menu.exec(self.image_list.mapToGlobal(position))
            if has_skel and (action == act_batch_gif or action == act_batch_video):
                pass  # 需要在下面判断
            # 判断具体点击的 action
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

            act_open = menu.addAction("📂 打开文件所在目录")
            act_copy = menu.addAction("📋 复制文件")

            if has_skel:
                menu.addSeparator()
                skel_path, atlas_path, _ = entries[0]
                is_composite = is_composite_png(png_path)
                if is_composite:
                    act_export_gif = menu.addAction("🎞️ 合成 GIF 预览")
                    act_export_video = menu.addAction("🎬 合成视频")
                else:
                    act_export_gif = menu.addAction("🎞️ 导出 GIF 预览")
                    act_export_video = menu.addAction("🎬 导出为视频")

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
                ['explorer', '/select,', os.path.normpath(file_path)],
                shell=True
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
        sub = db.get_sub_bundles(ts); down = sum(1 for r in sub if r[2]) if sub else 0
        if down == 0: QMessageBox.information(self, "无文件", "没有已下载的 bundle."); return
        rp = QMessageBox.question(self, "确认删除", f"删除此版本 {down} 个文件?", QMessageBox.Yes|QMessageBox.No, QMessageBox.No)
        if rp != QMessageBox.Yes: return
        c = db.get_conn()
        for r in sub:
            if r[2]:
                try: os.remove(r[2])
                except: pass
                c.execute("UPDATE sub_bundles SET local_path=NULL, downloadable=0 WHERE hash=? AND version_timestamp=?",(r[0],ts))
        c.commit(); c.close()
        self._load_data()
        self.status_bar.showMessage(f"已删除 {down} 个文件.")
        QMessageBox.information(self, "完成", f"已删除 {down} 个文件.")

    # ========== SEED ==========

    def _seed_bundled_version(self):
        existing = {r[0] for r in db.get_all_versions()}
        ctx = ssl.create_default_context()
        CDN_DIR = os.path.join(BUNDLES_DIR, "seeds")
        HD = {"User-Agent": "UnityPlayer/2021.3.45f2c1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)","X-Unity-Version": "2021.3.45f2c1"}
        def dcat(n,h):
            fp=os.path.join(CDN_DIR,f"SEED_{n}_{h[:12]}.json")
            if not os.path.exists(fp):
                os.makedirs(CDN_DIR,exist_ok=True)
                d=urllib.request.urlopen(urllib.request.Request(f"https://elpis.17995cdn.com/Android/Bundles/{n.lower()}_{h}.json",headers=HD),context=ctx,timeout=15).read()
                with open(fp,"wb")as f:f.write(d)
            return fp
        def seed(ts,info,vd,notes,cats,ic=False):
            if ts in existing:return
            self.version_mgr.register_version(ts,info,vd,is_current=ic)
            db.add_notes(ts,notes)
            ah=set()
            for n,h in cats:
                try: ah|=extract_manifest_hashes(dcat(n,h))
                except: pass
            if ah: db.save_sub_bundles(ts,list(ah))
        seed(134091181056097516,{"timestamp":134091181056097516,"file":"versions_vA137.D342.O142.V6_134091181056097516.json","hash":"","size":0,"downloadURL":"https://elpis.17995cdn.com/Android/Bundles","playerURL":"https://xl.haoplay.com.cn/dl","latestVersion":"1.0","minVersion":"1.0"},{"timestamp":134091181056097516,"data":[{"name":"Arts","hash":"80d80e718768bdd7b35f5b9406d624ed","size":781708,"ver":137},{"name":"Data","hash":"48002d6e908b3fce2c4b61d8c34b9393","size":158685,"ver":342},{"name":"Other","hash":"fdf0f3b9cad9498a25f542c4c9ce0f8b","size":129635,"ver":142},{"name":"Video","hash":"a7fd12e3f78b95bcfb6c63be49247b5f","size":2738,"ver":6}]},"APK内置版本, 2025年12月",[("Arts","80d80e718768bdd7b35f5b9406d624ed"),("Data","48002d6e908b3fce2c4b61d8c34b9393"),("Other","fdf0f3b9cad9498a25f542c4c9ce0f8b")])
        seed(134239138473475084,{"timestamp":134239138473475084,"file":"versions_vA145.D378.O152.V6_134239138473475084.json","hash":"","size":0,"downloadURL":"https://elpis.17995cdn.com/Android/Bundles","playerURL":"https://xl.haoplay.com.cn/dl","latestVersion":"1.1","minVersion":"1.1"},{"timestamp":134239138473475084,"data":[{"name":"Arts","hash":"a1c72aae7f79b72bc763e63803362d01","size":836280,"ver":145},{"name":"Data","hash":"7bbfa67db42e024cb7124dddb8b91d2b","size":180385,"ver":378},{"name":"Other","hash":"d3632702f53de4ba0457b5e518aaf0f3","size":139791,"ver":152},{"name":"Video","hash":"01fe1717b1979689c37f26f6312ea23b","size":2738,"ver":6}]},"完整版本, 2026年5月26日",[("Arts","a1c72aae7f79b72bc763e63803362d01"),("Data","7bbfa67db42e024cb7124dddb8b91d2b"),("Other","d3632702f53de4ba0457b5e518aaf0f3")])
        seed(134272123703055311,{"timestamp":134272123703055311,"file":"versions_vA152.D386.O156.V6_134272123703055311.json","hash":"bb7d22dab4acab771f336ce53f6af261","size":367,"downloadURL":"https://elpis.17995cdn.com/Android/Bundles","playerURL":"https://xl.haoplay.com.cn/dl","latestVersion":"1.2","minVersion":"1.2"},{"timestamp":134272123703055311,"data":[{"name":"Arts","hash":"30ed8244781b44cacc3c5d1ea10a976e","size":844382,"ver":152},{"name":"Data","hash":"5d2c794364dfe22100547764f60689fe","size":185983,"ver":386},{"name":"Other","hash":"dd50845a464e1eda86b027103d2cce43","size":146765,"ver":156},{"name":"Video","hash":"ac0fb4b1842c88408eee708a5f394a8b","size":2744,"ver":6}]},"在线版本, 2026年6月29日",[("Arts","30ed8244781b44cacc3c5d1ea10a976e"),("Data","5d2c794364dfe22100547764f60689fe"),("Other","dd50845a464e1eda86b027103d2cce43")])

    def _check_auto(self):
        cur = self.version_mgr.get_current()
        if not cur:
            self.status_bar.showMessage("首次启动, 自动检查更新...")
            QTimer.singleShot(1500, self._check_update)
        else:
            QTimer.singleShot(500, lambda: self.status_bar.showMessage("就绪"))
