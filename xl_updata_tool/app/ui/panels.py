import json
from datetime import datetime, timedelta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLineEdit, QTextEdit, QFrame, QComboBox,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QAction
from PySide6.QtWidgets import QAbstractItemView, QMenu, QApplication

from .theme import (
    ACCENT, BG_SURFACE, BG_ELEVATED, BG_HOVER, BORDER, TEXT_PRIMARY,
    TEXT_SECONDARY, TEXT_MUTED, SUCCESS
)
from app.core import database as db
from app.core.audio_library import format_size


def ticks_to_date(ticks):
    """将 .NET DateTime ticks 转为可读日期，处理年份偏移"""
    try:
        # .NET ticks: 100ns intervals from 0001-01-01
        seconds = int(ticks) / 10_000_000
        dt = datetime(1, 1, 1) + timedelta(seconds=seconds)
        # 年份校准：.NET year 1 = 现实中 year 1，但这个游戏显然不是公元425年
        # 年份偏移匹配到 2025-2026 范围
        if dt.year < 2000:
            dt = dt.replace(year=dt.year + 1600)
        return dt
    except Exception:
        return datetime.now()


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, ev: QMouseEvent):
        self.clicked.emit()
        super().mousePressEvent(ev)


class StatCard(QFrame):
    def __init__(self, title, value, subtitle="", color=ACCENT):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_ELEVATED};
                border: 1px solid {BORDER};
                border-radius: 10px;
                padding: 16px;
            }}
        """)
        self.setFixedHeight(100)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color:{TEXT_MUTED};font-size:11px;font-weight:600;letter-spacing:1px;border:none;")
        layout.addWidget(title_lbl)
        val = QLabel(value)
        val.setStyleSheet(f"color:{color};font-size:26px;font-weight:700;border:none;")
        layout.addWidget(val)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;border:none;")
            layout.addWidget(sub)


class VersionListPanel(QWidget):
    version_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header_lbl = QLabel("版本列表")
        header_lbl.setProperty("subheading", True)
        layout.addWidget(header_lbl)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["版本"])
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setAlternatingRowColors(True)
        self.tree.setAnimated(True)
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnCount(2)
        self.tree.setColumnWidth(0, 30)
        self.tree.header().setVisible(False)
        self.tree.header().setStretchLastSection(True)
        self.tree.setStyleSheet(f"""
            QTreeWidget {{
                background-color: {BG_SURFACE};
                border: 1px solid {BORDER};
                border-radius: 8px;
                font-size: 13px;
            }}
            QTreeWidget::item {{
                padding: 10px 12px;
                border-radius: 6px;
                margin: 2px 4px;
            }}
            QTreeWidget::item:selected {{
                background-color: {ACCENT};
                color: #ffffff;
                font-weight: 600;
            }}
            QTreeWidget::item:hover:!selected {{
                background-color: {BG_HOVER};
            }}
            QTreeWidget::indicator {{
                width: 18px; height: 18px;
                border: 2px solid {TEXT_MUTED};
                border-radius: 4px;
                background-color: {BG_ELEVATED};
            }}
            QTreeWidget::indicator:checked {{
                background-color: {SUCCESS};
                border-color: {SUCCESS};
            }}
            QTreeWidget::indicator:hover {{
                border-color: {TEXT_PRIMARY};
            }}
        """)
        self.tree.currentItemChanged.connect(self._on_select)
        layout.addWidget(self.tree)

    def get_selected_versions(self):
        """返回所有勾选的版本 timestamp 列表"""
        result = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                result.append(item.data(1, Qt.UserRole))
        return result

    def load_versions(self, versions):
        self.tree.clear()
        for v in versions:
            ts, arts, data, other, video, apk_ver, manifest, is_cur, dl, created, notes = v
            dt = ticks_to_date(ts)
            date_str = dt.strftime("%Y-%m-%d %H:%M")
            label = date_str
            if is_cur:
                label += "  ● 最新"
            item = QTreeWidgetItem(["", label])
            item.setCheckState(0, Qt.Checked if is_cur else Qt.Unchecked)
            item.setData(1, Qt.UserRole, ts)
            item.setToolTip(1, (
                f"更新时间: {date_str}\n"
                f"版本代号: vA{arts}.D{data}.O{other}.V{video}\n"
                f"APK版本: v{apk_ver}\n"
                f"备注: {notes or '无'}"
            ))
            if is_cur:
                item.setForeground(1, QColor("#f0a040"))
                f = item.font(1)
                f.setBold(True)
                item.setFont(1, f)
            self.tree.addTopLevelItem(item)
            if is_cur:
                self.tree.setCurrentItem(item)

    def _on_select(self, current, previous):
        if current:
            ts = current.data(1, Qt.UserRole)
            if ts:
                self.version_selected.emit(ts)


class BundleBrowserPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        from .bundle_model import BundleTableModel
        from PySide6.QtWidgets import QTableView

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("资源包列表")
        title.setProperty("subheading", True)
        top.addWidget(title)
        top.addStretch()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索资源包...")
        self.search_input.setFixedWidth(240)
        self.search_input.setClearButtonEnabled(True)
        top.addWidget(self.search_input)
        layout.addLayout(top)

        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft)
        for i, w in enumerate([280, 140, 90, 70, 70]):
            self.table.setColumnWidth(i, w)
        self.table.setSortingEnabled(False)
        layout.addWidget(self.table)
        self.model = BundleTableModel(self)
        self.table.setModel(self.model)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.search_input.textChanged.connect(self._on_search)
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._do_search)

    def load_version(self, timestamp):
        self.model.set_version(timestamp)

    def _on_search(self, text):
        self._search_timer.start()

    def _do_search(self):
        self.model.search(self.search_input.text())

    def _context_menu(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        menu = QMenu(self)
        menu.setStyleSheet(self.styleSheet())
        row_data = self.model.get_row(idx.row())
        if row_data:
            name, full_hash, size, ver, local = row_data
            copy_name = QAction("复制资源包名称", self)
            copy_hash = QAction("复制哈希值", self)
            copy_all = QAction("复制完整信息", self)
            menu.addAction(copy_name)
            menu.addAction(copy_hash)
            menu.addSeparator()
            menu.addAction(copy_all)
            cb = QApplication.clipboard()
            copy_name.triggered.connect(lambda: cb.setText(name))
            copy_hash.triggered.connect(lambda: cb.setText(full_hash))
            copy_all.triggered.connect(
                lambda: cb.setText(f"{name}  v{ver}  hash={full_hash}  size={format_size(size)}")
            )
        menu.exec(self.table.viewport().mapToGlobal(pos))


class VersionInfoPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header = QLabel("版本详情")
        header.setProperty("subheading", True)
        layout.addWidget(header)
        self.content = QTextEdit()
        self.content.setReadOnly(True)
        self.content.setStyleSheet(f"""
            QTextEdit {{
                background-color: {BG_ELEVATED};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 12px;
                font-size: 12px;
                font-family: "Cascadia Code", "Consolas", "Menlo", monospace;
            }}
        """)
        layout.addWidget(self.content)

    def show_version(self, timestamp):
        v = db.get_version(timestamp)
        if not v:
            self.content.setPlainText("未选择版本")
            return
        ts, arts, data, other, video, apk, manifest, update_json, versions_json, notes = v
        sub_count = db.get_sub_bundle_count(ts)
        lines = [
            f"时间戳:     {ts}",
            f"APK 版本:   v{apk}",
            f"Arts  版本:  {arts}",
            f"Data  版本:  {data}",
            f"Other 版本:  {other}",
            f"Video 版本:  {video}",
            f"Bundle 数:   {sub_count}",
            f"清单文件:   {manifest}",
            f"备注:       {notes or '-'}",
            "",
            "--- updateinfo.json ---",
        ]
        if update_json:
            try:
                info = json.loads(update_json)
                for k, v2 in info.items():
                    lines.append(f"  {k}: {v2}")
            except Exception:
                lines.append(update_json[:500])
        self.content.setPlainText("\n".join(lines))


class ComparePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header = QLabel("版本对比")
        header.setProperty("subheading", True)
        layout.addWidget(header)
        sel = QHBoxLayout()
        sel.setSpacing(8)
        self.combo_a = QComboBox()
        self.combo_b = QComboBox()
        arrow = QLabel("对比")
        arrow.setStyleSheet(f"color:{TEXT_MUTED};font-weight:700;")
        arrow.setAlignment(Qt.AlignCenter)
        sel.addWidget(self.combo_a)
        sel.addWidget(arrow)
        sel.addWidget(self.combo_b)
        btn = QPushButton("开始对比")
        btn.setProperty("accent", True)
        sel.addWidget(btn)
        layout.addLayout(sel)
        self.result = QTextEdit()
        self.result.setReadOnly(True)
        self.result.setStyleSheet(f"""
            QTextEdit {{
                background-color: {BG_ELEVATED};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 12px;
                font-size: 12px;
                font-family: "Cascadia Code", "Consolas", "Menlo", monospace;
            }}
        """)
        layout.addWidget(self.result)
        btn.clicked.connect(self._compare)

    def load_versions(self, versions):
        self.combo_a.clear()
        self.combo_b.clear()
        for v in versions:
            ts = v[0]
            arts, data, other, video = v[1], v[2], v[3], v[4]
            dt = ticks_to_date(ts)
            label = f"{dt.strftime('%Y-%m-%d')} (vA{arts}.D{data}.O{other}.V{video})"
            self.combo_a.addItem(label, ts)
            self.combo_b.addItem(label, ts)
        if self.combo_b.count() >= 2:
            self.combo_b.setCurrentIndex(1)

    def _compare(self):
        ts1 = self.combo_a.currentData()
        ts2 = self.combo_b.currentData()
        if not ts1 or not ts2:
            self.result.setPlainText("请选择两个版本进行对比。")
            return
        if ts1 == ts2:
            self.result.setPlainText("请选择两个不同的版本进行对比。")
            return

        old_hashes = set(r[0] for r in db.get_sub_bundles(min(ts1, ts2)))
        new_hashes = set(r[0] for r in db.get_sub_bundles(max(ts1, ts2)))

        added = sorted(new_hashes - old_hashes)
        removed = sorted(old_hashes - new_hashes)
        common = len(old_hashes & new_hashes)

        lines = [
            "=== 版本变化摘要 ===",
            "",
            f"旧版 bundle: {len(old_hashes):,}",
            f"新版 bundle: {len(new_hashes):,}",
            f"未变化:     {common:,}",
            f"新增:       {len(added):,}",
            f"移除:       {len(removed):,}",
        ]

        if added:
            lines.append(f"\n--- 新增 ({len(added)}) ---")
            for h in added[:50]:
                lines.append(f"  + {h}")
            if len(added) > 50:
                lines.append(f"  ... 共 {len(added)} 个")

        if removed:
            lines.append(f"\n--- 移除 ({len(removed)}) ---")
            for h in removed[:50]:
                lines.append(f"  - {h}")
            if len(removed) > 50:
                lines.append(f"  ... 共 {len(removed)} 个")

        if not added and not removed:
            lines.append("\n两个版本之间没有变化。")

        self.result.setPlainText("\n".join(lines))


def _fs(s):
    s = int(s) if s else 0
    if s >= 1048576:
        return f"{s/1048576:.1f}MB"
    if s >= 1024:
        return f"{s/1024:.1f}KB"
    return f"{s}B"
