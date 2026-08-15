"""Asset browser - flat table with drag-reorder columns, wide preview"""
import os, json, subprocess, sys, shutil

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QLabel, QTextEdit,
    QProgressBar, QMessageBox, QWidget, QAbstractItemView, QHeaderView,
    QListWidget, QListWidgetItem,
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread
from PySide6.QtGui import QPixmap

from .theme import (
    ACCENT, BG_SURFACE, BG_ELEVATED, BG_DARK, BG_HOVER, BORDER,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, SUCCESS, INFO, WARNING,
)
from app.core.bundle_parser import fix_bundle_inplace
from app.core.logger import logger
from app.core.path_utils import get_base_dir, get_tools_dir

_PROJ = get_base_dir()
AS_CLI = os.path.join(get_tools_dir(), "AssetStudio", "AssetStudio.CLI.exe")

COLUMNS = ["Name", "Type", "Path", "Size", "Hash"]


class _MultiSelectComboBox(QPushButton):
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_types = set()
        self._popup = None
        self.setStyleSheet(f"""
            QPushButton {{
                background-color:{BG_ELEVATED};
                border:1px solid {BORDER};
                border-radius:6px;
                padding:6px 12px;
                color:{TEXT_PRIMARY};
                font-size:13px;
                text-align:left;
                min-width:120px;
            }}
            QPushButton:hover {{
                background-color:{BG_HOVER};
            }}
            QPushButton::menu-indicator {{
                subcontrol-origin: padding;
                subcontrol-position: right center;
                image: none;
            }}
        """)
        self._update_display()

    def _update_display(self):
        if not self._selected_types:
            self.setText("全部类型")
        elif len(self._selected_types) == 1:
            self.setText(list(self._selected_types)[0])
        else:
            self.setText(f"已选 {len(self._selected_types)} 种类型")

    def add_type_item(self, type_name):
        if not type_name or not isinstance(type_name, str):
            return
        if self._popup is None:
            self._popup = _TypePopup(self)
            self._popup.selection_changed.connect(self._on_popup_selection_changed)
        self._popup.add_item(type_name)

    def clear_types(self):
        self._selected_types.clear()
        if self._popup:
            self._popup.clear()
        self._update_display()

    def get_selected_types(self):
        return self._selected_types

    def _on_popup_selection_changed(self, selected):
        self._selected_types = selected
        self._update_display()
        if selected:
            logger.info(f"类型筛选: 已选择 {len(selected)} 种类型 - {', '.join(sorted(selected))}")
        self.selection_changed.emit()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if self._popup:
            self._popup.show()
            self._popup.move(self.mapToGlobal(self.rect().bottomLeft()))


class _TypePopup(QWidget):
    selection_changed = Signal(set)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self._items = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.NoSelection)
        self._list.setStyleSheet(f"""
            QListWidget {{
                background-color:{BG_SURFACE};
                border:1px solid {BORDER};
                border-radius:6px;
                padding:4px;
            }}
            QListWidget::item {{
                padding:4px 8px;
                border-radius:4px;
            }}
            QListWidget::item:hover {{
                background-color:{BG_HOVER};
            }}
        """)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)
        self.setFixedWidth(200)

    def add_item(self, type_name):
        if type_name in self._items:
            return
        item = QListWidgetItem(type_name)
        item.setData(Qt.UserRole, type_name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Unchecked)
        self._items[type_name] = item
        self._list.addItem(item)

    def clear(self):
        self._items.clear()
        self._list.clear()

    def _on_item_clicked(self, item):
        type_name = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            item.setCheckState(Qt.Unchecked)
        else:
            item.setCheckState(Qt.Checked)
        selected = {name for name, it in self._items.items() if it.checkState() == Qt.Checked}
        self.selection_changed.emit(selected)

    def show(self):
        self._list.setMinimumHeight(min(self._list.count() * 24 + 8, 300))
        super().show()

    def focusOutEvent(self, event):
        self.hide()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)


class _FixWorker(QThread):
    progress = Signal(int, int)
    done = Signal(str, int, int)   # bundle_dir, success_count, fail_count
    error = Signal(str)

    def __init__(self, parent, files, bundle_dir):
        super().__init__(parent)
        self._files = files
        self._bundle_dir = bundle_dir

    def run(self):
        try:
            success_count = 0
            fail_count = 0
            total = len(self._files)
            logger.info(f"开始修复文件头（直接修复原始文件），共 {total} 个文件")
            for i, f in enumerate(self._files):
                h = os.path.basename(f).replace(".bundle", "")
                try:
                    # 直接对原始 .bundle 文件调用 fix_bundle_inplace（不再复制到临时目录）
                    fix_bundle_inplace(f)
                    success_count += 1
                    logger.debug(f"修复完成: {h[:16]}...")
                except Exception as e:
                    logger.error(f"修复失败: {h[:16]}... - {e}")
                    fail_count += 1
                self.progress.emit(i + 1, total)
            logger.info(f"文件头修复完成，成功 {success_count}/{total}, 失败 {fail_count}")
            self.done.emit(self._bundle_dir, success_count, fail_count)
        except Exception as e:
            logger.error(f"文件修复线程致命错误: {e}", exc_info=True)
            self.error.emit(str(e))


class _MapWorker(QThread):
    progress = Signal(int, int)
    done = Signal(list)
    error = Signal(str)

    def __init__(self, p, bd):
        super().__init__(p)
        self._bd = bd

    def run(self):
        try:
            if not os.path.exists(AS_CLI):
                logger.error(f"AssetStudio.CLI.exe 不存在: {AS_CLI}")
                self.error.emit(f"AssetStudio CLI 不存在: {AS_CLI}")
                return
            md = os.path.join(self._bd, "_map")
            os.makedirs(md, exist_ok=True)
            total_bundles = sum(1 for f in os.listdir(self._bd) if f.lower().endswith(".bundle")) if os.path.isdir(self._bd) else 0
            logger.info(f"[资源浏览器] 解析资源，{total_bundles} 个 bundle")
            proc = subprocess.Popen(
                [AS_CLI, self._bd, md, "--game", "UnityCN", "--key_index", "23",
                 "--map_op", "Both", "--map_type", "JSON"],
                cwd=os.path.dirname(AS_CLI), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            loaded = 0
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if "Loading" in line and ".bundle" in line:
                    loaded += 1
                    self.progress.emit(loaded, total_bundles)
                else:
                    logger.debug(f"[资源浏览器] CLI: {line}")
            proc.wait()
            mf = os.path.join(md, "assets_map.json")
            if os.path.exists(mf) and os.path.getsize(mf) > 100:
                with open(mf, "r", encoding="utf-8") as f:
                    self.done.emit(json.load(f))
            else:
                self.error.emit("Map generation failed")
        except Exception as e:
            self.error.emit(str(e))


class _ExtractWorker(QThread):
    result = Signal(bool, str, int)

    def __init__(self, p, bd, od, tp):
        super().__init__(p)
        self._bd = bd
        self._od = od
        self._tp = tp

    def _count_files(self, directory):
        count = 0
        if not os.path.isdir(directory):
            return 0
        for root, dirs, files in os.walk(directory):
            count += len(files)
        return count

    def run(self):
        try:
            if not os.path.exists(AS_CLI):
                logger.error(f"AssetStudio.CLI.exe 不存在: {AS_CLI}")
                self.result.emit(False, f"AssetStudio CLI 不存在: {AS_CLI}", 0)
                return
            os.makedirs(self._od, exist_ok=True)
            cmd = [AS_CLI, self._bd, self._od, "--game", "UnityCN", "--key_index", "23",
                   "--types", ",".join(self._tp), "--group_assets", "ByContainer",
                   "--export_type", "Convert"]
            logger.debug(f"CLI 命令: {' '.join(cmd)}")
            logger.info(f"开始导出资源，类型: {self._tp}")
            proc = subprocess.run(
                cmd, cwd=os.path.dirname(AS_CLI),
                capture_output=True, text=True, timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            logger.info(f"CLI 退出码: {proc.returncode}")
            if proc.stdout:
                logger.debug(f"CLI stdout: {proc.stdout[:500]}")
            if proc.stderr:
                logger.warning(f"CLI stderr: {proc.stderr[:500]}")
            file_count = self._count_files(self._od)
            logger.info(f"导出完成，输出目录: {self._od}, 文件数: {file_count}")
            if file_count > 0:
                logger.info(f"导出成功: {self._od}, 共 {file_count} 个文件")
                self.result.emit(True, self._od, file_count)
            else:
                err_msg = proc.stderr[:500] if proc.stderr else "CLI 执行完成但输出目录为空"
                logger.error(f"导出失败: {err_msg}")
                self.result.emit(False, err_msg, 0)
        except subprocess.TimeoutExpired:
            logger.error("导出超时（300秒），已终止 CLI 进程")
            self.result.emit(False, "导出超时，进程已终止", 0)
        except Exception as e:
            logger.error(f"导出线程异常: {e}", exc_info=True)
            self.result.emit(False, str(e), 0)


class AssetBrowser(QDialog):
    def __init__(self, parent, files, ts):
        super().__init__(parent)
        self.files = files
        self.ts = ts
        self.bundle_dir = ""
        self.assets = []
        self._filtered = []
        self.setWindowTitle("资源浏览器")
        self.resize(1300, 750)
        self.setMinimumSize(800, 500)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self._setup_ui()
        self._start_fix()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)

        self.fix_bar = QProgressBar()
        self.fix_bar.setFixedHeight(24)
        self.fix_bar.setFormat("修复文件头: 已完成 0/%v")
        self.fix_bar.setStyleSheet(f"""
            QProgressBar {{ background-color:{BG_ELEVATED}; border:none; border-radius:4px;
                           text-align:center; color:{TEXT_PRIMARY}; font-size:13px; }}
            QProgressBar::chunk {{ background-color:{ACCENT}; border-radius:4px; }}""")
        root.addWidget(self.fix_bar)

        self.load_bar = QProgressBar()
        self.load_bar.setFixedHeight(24)
        self.load_bar.setFormat("正在加载资源...")
        self.load_bar.setVisible(False)
        self.load_bar.setStyleSheet(f"""
            QProgressBar {{ background-color:{BG_ELEVATED}; border:none; border-radius:4px;
                           text-align:center; color:{TEXT_PRIMARY}; font-size:13px; }}
            QProgressBar::chunk {{ background-color:{ACCENT}; border-radius:4px; }}""")
        root.addWidget(self.load_bar)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("筛选...")
        self.search.setFixedWidth(200)
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search)
        self.type_filter = _MultiSelectComboBox()
        self.type_filter.setFixedWidth(150)
        bar.addWidget(self.type_filter)
        bar.addStretch()
        self.count_lbl = QLabel("加载中...")
        self.count_lbl.setStyleSheet(f"color:{TEXT_MUTED};font-size:12px;background:transparent;border:none;")
        bar.addWidget(self.count_lbl)
        self.btn_all = QPushButton("导出全部")
        self.btn_all.setStyleSheet(self._bs(SUCCESS))
        self.btn_sel = QPushButton("导出筛选")
        self.btn_sel.setStyleSheet(self._bs(INFO))
        bar.addWidget(self.btn_all)
        bar.addWidget(self.btn_sel)
        root.addLayout(bar)

        splitter = QSplitter(Qt.Horizontal)

        tbl_w = QWidget()
        tbl_l = QVBoxLayout(tbl_w)
        tbl_l.setContentsMargins(0, 0, 0, 0)
        tbl_l.setSpacing(0)
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionsMovable(True)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 200)
        self.table.setColumnWidth(3, 70)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(False)
        self.table.setStyleSheet(f"""
            QTableWidget {{ background-color:{BG_SURFACE}; border:1px solid {BORDER};
                           border-radius:6px; gridline-color:transparent; }}
            QTableWidget::item {{ padding:4px 8px; }}
            QTableWidget::item:selected {{ background-color:{ACCENT}; color:#fff; }}""")
        tbl_l.addWidget(self.table)
        splitter.addWidget(tbl_w)

        right = QWidget()
        right.setMinimumWidth(380)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        self.preview_img = QLabel()
        self.preview_img.setAlignment(Qt.AlignCenter)
        self.preview_img.setMinimumHeight(320)
        self.preview_img.setStyleSheet(f"background-color:{BG_ELEVATED};border-radius:6px;border:1px solid {BORDER};")
        rl.addWidget(self.preview_img, 1)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setStyleSheet(f"""
            QTextEdit {{ background-color:{BG_ELEVATED}; border:1px solid {BORDER};
                        border-radius:6px; padding:8px; font-family:Consolas,monospace;
                        font-size:12px; color:{TEXT_PRIMARY}; }}""")
        self.preview_text.setVisible(False)
        rl.addWidget(self.preview_text, 1)
        ex_btn = QPushButton("导出选中")
        ex_btn.setStyleSheet(f"""
            QPushButton {{ background-color:{ACCENT}; border:none; border-radius:6px;
                          padding:8px; color:#fff; font-size:13px; font-weight:600; }}
            QPushButton:hover {{ background-color:#a78bfa; }}""")
        ex_btn.clicked.connect(self._extract_selected)
        rl.addWidget(ex_btn)
        splitter.addWidget(right)
        splitter.setSizes([750, 450])
        root.addWidget(splitter, 1)

        self.search.textChanged.connect(self._debounce)
        self.type_filter.selection_changed.connect(self._apply_filter)
        self.table.currentItemChanged.connect(self._on_asset_select)
        self.btn_all.clicked.connect(lambda: self._extract("all"))
        self.btn_sel.clicked.connect(lambda: self._extract("filtered"))
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._apply_filter)

    def _bs(self, c):
        return f"QPushButton{{background-color:{c};border:none;border-radius:6px;padding:8px 16px;color:#fff;font-size:13px;font-weight:600;}}QPushButton:hover{{opacity:0.85;}}"

    def _start_fix(self):
        self.fix_bar.setMaximum(len(self.files))
        self.fix_bar.setValue(0)
        if not self.files:
            QMessageBox.warning(self, "错误", "没有可用的 Bundle 文件")
            self.close()
            return
        # 直接使用原始 bundles 目录，不再复制到临时目录
        bundle_dir = os.path.dirname(self.files[0])
        self._fix_worker = _FixWorker(self, self.files, bundle_dir)
        self._fix_worker.progress.connect(self._on_fix_progress)
        self._fix_worker.done.connect(self._on_fix_done)
        self._fix_worker.error.connect(self._on_fix_error)
        self._fix_worker.start()

    def _on_fix_progress(self, cur, total):
        self.fix_bar.setValue(cur)
        self.fix_bar.setFormat(f"修复文件头: 已完成 {cur}/{total}")

    def _on_fix_done(self, bundle_dir, success_count, fail_count):
        self.bundle_dir = bundle_dir
        self.fix_bar.setVisible(False)
        if success_count == 0:
            # 所有文件修复失败
            QMessageBox.warning(
                self, "失败",
                "所有文件修复失败，请删除该版本并重新下载。\n\n"
                "（请在主界面的版本列表中使用【删除已下载】按钮）"
            )
            self.close()
            return
        if fail_count > 0:
            # 部分文件修复失败
            QMessageBox.warning(
                self, "部分文件修复失败",
                f"部分文件修复失败（{fail_count} 个），这些文件可能已损坏。\n\n"
                f"建议删除该版本的所有文件并重新下载（请在主界面的版本列表中使用【删除已下载】按钮）。"
            )
        self._start_map()

    def _on_fix_error(self, err):
        logger.error(f"文件修复错误: {err}")
        self.fix_bar.setFormat(f"修复错误: {err}")
        self.fix_bar.setStyleSheet(f"""
            QProgressBar {{ background-color:#4a2020; border:none; border-radius:4px;
                           text-align:center; color:#ff8888; font-size:13px; }}""")

    def _start_map(self):
        self.load_bar.setVisible(True)
        self.load_bar.setFormat("正在加载资源...")
        self._worker = _MapWorker(self, self.bundle_dir)
        self._worker.progress.connect(self._on_map_progress)
        self._worker.done.connect(self._on_map_done)
        self._worker.error.connect(self._on_map_error)
        self._worker.start()

    def _on_map_progress(self, cur, total):
        self.load_bar.setMaximum(total)
        self.load_bar.setValue(cur)
        self.load_bar.setFormat(f"加载中... {cur}/{total} 个bundle")

    def _on_map_done(self, assets):
        self.assets = assets
        self._filtered = list(assets)
        self.load_bar.setVisible(False)
        types = sorted(set(a.get("Type", "?") for a in assets))
        self.type_filter.clear_types()
        for t in types:
            self.type_filter.add_type_item(t)
        self._apply_filter()
        self._auto_export_to_material()

    def _on_map_error(self, err):
        logger.error(f"资源加载错误: {err}")
        self.load_bar.setFormat(f"错误: {err}")
        self.load_bar.setStyleSheet(f"""
            QProgressBar {{ background-color:#4a2020; border:none; border-radius:4px;
                           text-align:center; color:#ff8888; font-size:13px; }}""")

    def _debounce(self):
        self._timer.start()

    def _apply_filter(self):
        text = self.search.text().lower()
        selected_types = self.type_filter.get_selected_types()
        if selected_types:
            logger.info(f"类型筛选: 已选择 {len(selected_types)} 种类型")
        self._filtered = [a for a in self.assets
                          if (not selected_types or a.get("Type", "") in selected_types)
                          and (not text or text in a.get("Name", "").lower() or text in a.get("Container", "").lower())]
        self.count_lbl.setText(f"{len(self._filtered)} / {len(self.assets)}")
        self._load_table()

    def _load_table(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._filtered))
        for i, a in enumerate(self._filtered):
            name = a.get("Name", "?")
            atype = a.get("Type", "?")
            container = a.get("Container", "")
            size = a.get("Size", 0)
            path_id = str(a.get("PathID", ""))
            hash_val = ""
            vals = {"Name": name, "Type": atype, "Path": container, "Size": f"{size / 1024:.0f}KB" if size else "-", "Hash": hash_val or "-"}
            hdr = self.table.horizontalHeader()
            for col in range(5):
                key = COLUMNS[hdr.logicalIndex(col)]
                self.table.setItem(i, col, QTableWidgetItem(vals.get(key, "")))
            self.table.item(i, 0).setData(Qt.UserRole, a)
        self.table.setSortingEnabled(True)

    def _on_asset_select(self, current, prev):
        if not current:
            return
        a = current.data(Qt.UserRole) if current else None
        if not a:
            self.preview_img.clear()
            self.preview_text.setVisible(False)
            return
        atype = a.get("Type", "")
        name = a.get("Name", "")
        container = a.get("Container", "")
        extracted_root = os.path.join(_PROJ, "data", "extracted")
        preview_path = None
        if os.path.isdir(extracted_root):
            for root, dirs, files in os.walk(extracted_root):
                for f in files:
                    if name in f:
                        preview_path = os.path.join(root, f)
                        break
                if preview_path:
                    break
        self.preview_img.clear()
        self.preview_text.setVisible(False)
        if preview_path:
            ext = os.path.splitext(preview_path)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tga"):
                pix = QPixmap(preview_path)
                if not pix.isNull():
                    pw = self.preview_img.width() - 16
                    ph = self.preview_img.height() - 16
                    self.preview_img.setPixmap(pix.scaled(pw, ph, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    return
            try:
                with open(preview_path, "rb") as fp:
                    raw = fp.read()[:16000]
                try:
                    text = raw.decode("utf-8")
                except:
                    text = raw.decode("latin-1", errors="replace")
                self.preview_text.setVisible(True)
                self.preview_text.setPlainText(text)
                return
            except:
                pass
        self.preview_img.setText(f"[{atype}]\n\n{name}\n\n{container}")
        self.preview_img.setStyleSheet(
            f"background-color:{BG_ELEVATED};border-radius:6px;border:1px solid {BORDER};"
            f"color:{TEXT_SECONDARY};font-size:12px;padding:16px;")

    def _extract_selected(self):
        rows = set(it.row() for it in self.table.selectedItems())
        if not rows:
            QMessageBox.information(self, "提示", "请先选中资源.")
            return
        out = os.path.join(_PROJ, "data", "extracted")
        os.makedirs(out, exist_ok=True)
        types = set()
        for r in rows:
            it = self.table.item(r, 0)
            if it:
                a = it.data(Qt.UserRole)
                if a:
                    types.add(a.get("Type", ""))
        self._run_extract(list(types), out)

    def _extract(self, mode):
        out = os.path.join(_PROJ, "data", "extracted")
        os.makedirs(out, exist_ok=True)
        assets = self.assets if mode == "all" else self._filtered
        types = list(set(a.get("Type", "") for a in assets if a.get("Type")))
        self._run_extract(types, out)

    def _run_extract(self, types, out_dir, silent=False, auto_export=False):
        if not types:
            logger.error("导出失败: 没有可导出的资源类型")
            if not silent:
                QMessageBox.warning(self, "错误", "没有可导出的资源类型")
            return
        if not self.bundle_dir or not os.path.isdir(self.bundle_dir):
            logger.error(f"导出失败: bundle 目录无效: {self.bundle_dir}")
            if not silent:
                QMessageBox.warning(self, "错误", f"Bundle 目录不存在: {self.bundle_dir}")
            return
        logger.info(f"准备导出: 类型={types}, 输出目录={out_dir}")
        if not silent:
            self.btn_all.setEnabled(False)
            self.btn_sel.setEnabled(False)
        self._ex = _ExtractWorker(self, self.bundle_dir, out_dir, types)
        self._ex.result.connect(
            lambda ok, msg, cnt: self._on_extract_done(ok, msg, out_dir, silent, cnt, auto_export)
        )
        self._ex.start()

    def _on_extract_done(self, ok, msg, out_dir, silent=False, file_count=0, auto_export=False):
        if not silent:
            self.btn_all.setEnabled(True)
            self.btn_sel.setEnabled(True)
            if ok:
                logger.info(f"手动导出完成: {out_dir}, 文件数: {file_count}")
                QMessageBox.information(self, "完成", f"已导出到:\n{out_dir}\n共 {file_count} 个文件")
            else:
                logger.error(f"手动导出失败: {msg}")
                QMessageBox.warning(self, "失败", f"导出失败:\n{msg}")
        elif auto_export:
            # 自动导出模式：统计结果并继续下一个类型
            if ok and file_count > 0:
                self._auto_success_count += 1
                self._auto_total_files += file_count
                logger.info(f"类型导出成功: {msg}, 新增 {file_count} 个文件")
            else:
                logger.error(f"类型导出失败: {msg}")
            self._auto_export_next_type()
        else:
            if ok and file_count > 0:
                logger.info(f"导出完成: {out_dir}, 文件数: {file_count}")
            else:
                logger.error(f"导出失败: {msg}, 文件数: {file_count}")

    def _auto_export_to_material(self):
        try:
            self._auto_material_dir = os.path.join(_PROJ, "data", "material")
            logger.info(f"开始自动导出资源到 {self._auto_material_dir} ...")
            if os.path.exists(self._auto_material_dir):
                shutil.rmtree(self._auto_material_dir, ignore_errors=True)
            os.makedirs(self._auto_material_dir, exist_ok=True)
            # 提取所有可用类型，逐个类型导出
            all_types = sorted(set(a.get("Type", "") for a in self.assets if a.get("Type")))
            self._auto_type_queue = list(all_types)
            self._auto_success_count = 0
            self._auto_total_files = 0
            self._auto_total_types = len(all_types)
            logger.info(f"待导出类型列表（共 {self._auto_total_types} 种）: {all_types}")
            self._auto_export_next_type()
        except Exception as e:
            logger.error(f"自动导出初始化失败: {e}", exc_info=True)

    def _auto_export_next_type(self):
        if not self._auto_type_queue:
            # 所有类型导出完成
            logger.info(
                f"自动导出完成：共 {self._auto_total_types} 种类型，"
                f"成功 {self._auto_success_count} 种，"
                f"总文件数 {self._auto_total_files}"
            )
            if self._auto_total_files > 0:
                # 清理文件名中多余的 .prefab 后缀
                self._cleanup_material_filenames(self._auto_material_dir)
                logger.info(f"所有文件已输出到: {self._auto_material_dir}")
            else:
                logger.error("自动导出完成但文件数为 0")
            return
        current_type = self._auto_type_queue.pop(0)
        logger.info(f"导出类型 {current_type} ...")
        self._run_extract([current_type], self._auto_material_dir, silent=True, auto_export=True)

    def _cleanup_material_filenames(self, out_dir):
        """清理文件名末尾多余的 .prefab 后缀（AssetStudio CLI 错误添加）"""
        try:
            logger.info(f"开始清理文件名: {out_dir}")
            if not os.path.isdir(out_dir):
                logger.warning(f"目录不存在，跳过清理: {out_dir}")
                return
            # 调试：列出目录结构
            logger.debug(f"目录结构: {out_dir}")
            for item in os.listdir(out_dir):
                item_path = os.path.join(out_dir, item)
                if os.path.isdir(item_path):
                    logger.debug(f"  [DIR] {item}")
                else:
                    logger.debug(f"  [FILE] {item}")
            renamed_count = 0
            skipped_count = 0
            scanned_files = 0
            # 递归扫描所有子目录，因为 CLI 可能创建子文件夹
            for root, dirs, files in os.walk(out_dir):
                for f in files:
                    scanned_files += 1
                    if f.endswith(".prefab"):
                        logger.debug(f"发现 .prefab 文件: {os.path.join(root, f)}")
                    if not f.endswith(".prefab"):
                        continue
                    src = os.path.join(root, f)
                    if not os.path.isfile(src):
                        logger.warning(f"路径不是文件: {src}")
                        continue
                    new_name = f[:-len(".prefab")]
                    dst = os.path.join(root, new_name)
                    if os.path.exists(dst):
                        logger.warning(f"目标文件已存在，跳过: {src} -> {dst}")
                        skipped_count += 1
                        continue
                    try:
                        os.rename(src, dst)
                        renamed_count += 1
                        logger.info(f"重命名文件: {src} -> {dst}")
                    except Exception as e:
                        logger.error(f"重命名文件失败 {src}: {e}")
            logger.info(f"扫描完成，共扫描 {scanned_files} 个文件")
            if renamed_count > 0:
                logger.info(f"清理完成，共重命名 {renamed_count} 个文件")
            else:
                logger.info("没有需要清理的 .prefab 后缀文件")
            if skipped_count > 0:
                logger.warning(f"跳过 {skipped_count} 个文件（目标已存在）")
        except Exception as e:
            logger.error(f"清理文件名失败: {e}", exc_info=True)