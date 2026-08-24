"""版本功能域控制器。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QWidget,
)

from app.platform.diagnostics import logger
from app.ui.panels import ticks_to_date
from app.features.versions.page import VersionPage
from app.features.versions.service import VersionService
from app.features.versions.worker import CheckUpdateThread, DownloadWorker
from app.shared.qt.tokens import DANGER, INFO, SUCCESS, TEXT_MUTED, WARNING, get_color


class VersionController(QObject):
    """协调版本工作区、更新检查、下载和删除任务。"""

    status_changed = Signal(str)
    progress_changed = Signal(int, int, str)
    versions_changed = Signal()
    selection_changed = Signal(object)

    def __init__(self, page: VersionPage, service: VersionService, parent=None):
        super().__init__(parent)
        self.page = page
        self.service = service
        self._version_checkboxes = {}
        self._checked_ts = set()
        self._checkbox_containers = {}
        self._hover_row = -1
        self._download_worker = None
        self._check_thread = None
        self._connect_page()

    @property
    def selected_versions(self) -> list[int]:
        return [ts for ts, checkbox in self._version_checkboxes.values() if checkbox.isChecked()]

    @property
    def selected_version(self):
        selected = self.selected_versions
        return selected[0] if selected else None

    def _connect_page(self):
        self.page.cell_clicked.connect(self._on_cell_clicked)
        self.page.row_selected.connect(self._on_row_select)
        self.page.hover_row_changed.connect(self._highlight_row)

    def _set_status(self, message: str):
        self.status_changed.emit(message)

    def load(self):
        self.service.sync_local()
        versions = self.service.refresh()
        self.populate_table(versions, self.service.delta_map(versions))
        self._set_status(f"已追踪 {len(versions)} 个版本")
        self.versions_changed.emit()
        return versions

    def populate_table(self, versions, delta_map=None):
        table = self.page.table
        table.setSortingEnabled(False)
        self._checked_ts = set(self.selected_versions)
        table.clearContents()
        table.setRowCount(len(versions))
        self._version_checkboxes = {}
        self._checkbox_containers = {}
        self._hover_row = -1
        downloaded_versions = 0
        for row, version in enumerate(versions):
            ts, _arts, _data, _other, _video, _apk, _manifest, is_current, _dl, _created, notes = version
            checkbox = QCheckBox()
            checkbox.setStyleSheet("background:transparent; border:none;")
            checkbox.setChecked(ts in self._checked_ts)
            checkbox.clicked.connect(lambda checked, current_row=row: self._set_version_checked(current_row, checked))
            checkbox_container = QWidget()
            checkbox_container.setAttribute(Qt.WA_StyledBackground, True)
            checkbox_layout = QHBoxLayout(checkbox_container)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            table.setCellWidget(row, 0, checkbox_container)
            self._version_checkboxes[row] = (ts, checkbox)
            self._checkbox_containers[row] = checkbox_container

            label = ticks_to_date(ts).strftime("%Y-%m-%d")
            if is_current:
                label += "  [最新]"
            version_item = QTableWidgetItem(label)
            version_item.setData(Qt.UserRole, ts)
            if is_current:
                version_item.setForeground(QColor("#f0a040"))
                font = version_item.font()
                font.setBold(True)
                version_item.setFont(font)
            table.setItem(row, 1, version_item)

            sub_bundles = self.service.bundles(ts)
            total = len(sub_bundles) if sub_bundles else 0
            downloaded = sum(1 for item in sub_bundles if item[2]) if sub_bundles else 0
            if total == 0:
                status = "无Bundle"
            elif downloaded >= total:
                status = "已下载"
                downloaded_versions += 1
            elif downloaded:
                status = f"部分 ({downloaded}/{total})"
            else:
                status = "未下载"
            status_item = QTableWidgetItem(status)
            color = SUCCESS if status == "已下载" else WARNING if "部分" in status else TEXT_MUTED
            status_item.setForeground(QColor(color))
            table.setItem(row, 2, status_item)
            table.setItem(row, 3, QTableWidgetItem(f"{total:,}" if total else "-"))
            if delta_map and ts in delta_map:
                added, removed, common = delta_map[ts]
                display_notes = f"新增 {added} | 移除 {removed} | 未变 {common}"
            else:
                display_notes = notes or ""
            table.setItem(row, 4, QTableWidgetItem(display_notes))
            for column, (text, color, delta_only) in enumerate(
                (("增量下载", SUCCESS, True), ("全量下载", INFO, False)), start=5
            ):
                button = self._row_button(text, color)
                button.clicked.connect(
                    lambda _checked=False, current_ts=ts, is_delta=delta_only:
                    self.download_version(current_ts, is_delta)
                )
                table.setCellWidget(row, column, button)
            delete_button = self._row_button("删除已下载", DANGER, width=100)
            delete_button.clicked.connect(
                lambda _checked=False, current_ts=ts: self.delete_version(current_ts)
            )
            table.setCellWidget(row, 7, delete_button)
        table.setSortingEnabled(True)
        self._version_count = len(versions)
        self._downloaded_version_count = downloaded_versions
        self._update_summary()

    @staticmethod
    def _row_button(text, color, width=None):
        button = QPushButton(text)
        button.setFixedHeight(30)
        button.setFixedWidth(width or 78)
        button.setStyleSheet(
            f"QPushButton {{ background-color:transparent; border:1px solid {color}; "
            f"border-radius:6px; padding:2px 8px; color:{color}; font-size:12px; font-weight:600; }}"
            f"QPushButton:hover {{ background-color:{color}; color:#fff; }}"
        )
        return button

    def _update_summary(self):
        self.page.version_summary.setText(
            f"{getattr(self, '_version_count', 0)} 个版本 · "
            f"已下载 {getattr(self, '_downloaded_version_count', 0)} · "
            f"已选择 {len(self.selected_versions)}"
        )

    def _set_version_checked(self, row, checked):
        if row not in self._version_checkboxes:
            return
        ts, checkbox = self._version_checkboxes[row]
        ctrl = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
        if checked and not ctrl:
            for other_row, (_other_ts, other) in self._version_checkboxes.items():
                if other_row != row and other.isChecked():
                    other.blockSignals(True)
                    other.setChecked(False)
                    other.blockSignals(False)
        checkbox.blockSignals(True)
        checkbox.setChecked(checked)
        checkbox.blockSignals(False)
        if checked:
            self._checked_ts.add(ts)
        else:
            self._checked_ts.discard(ts)
        self._update_summary()
        self.selection_changed.emit(self.selected_version)

    def _on_cell_clicked(self, row, column):
        if column == 0 or column >= 5:
            return
        if row in self._version_checkboxes:
            _ts, checkbox = self._version_checkboxes[row]
            self._set_version_checked(row, not checkbox.isChecked())

    def _on_row_select(self, current, _previous):
        if current:
            item = self.page.table.item(current.row(), 1)
            if item and item.data(Qt.UserRole):
                self.selection_changed.emit(item.data(Qt.UserRole))

    def _highlight_row(self, row):
        if row == self._hover_row:
            return
        self._clear_row_hover()
        self._hover_row = row
        if row < 0:
            return
        hover = QColor(get_color("BG_HOVER"))
        for column in range(self.page.table.columnCount()):
            item = self.page.table.item(row, column)
            if item:
                item.setBackground(hover)
        if row in self._checkbox_containers:
            self._checkbox_containers[row].setStyleSheet(f"background-color:{hover.name()};")

    def _clear_row_hover(self):
        if self._hover_row < 0:
            return
        for column in range(self.page.table.columnCount()):
            item = self.page.table.item(self._hover_row, column)
            if item:
                item.setBackground(QBrush())
        if self._hover_row in self._checkbox_containers:
            self._checkbox_containers[self._hover_row].setStyleSheet("")

    def check_update(self):
        current = self.service.current()
        old_hashes = []
        if current:
            old_hashes = [item[0] for item in (self.service.bundles(current[0]) or [])]
        self._set_status("正在检查更新...")
        self._check_thread = CheckUpdateThread(str(self.service.bundles_dir / "current"), old_hashes)
        self._check_thread.finished.connect(self._on_update_checked)
        self._check_thread.error.connect(self._on_check_error)
        self._check_thread.start()

    def _on_update_checked(self, info, versions, new_hashes, delta):
        result = self.service.register_checked(info, versions, new_hashes, delta)
        if result:
            self._set_status(f"发现新版本! 新增 {result['added']} 个 bundle.")
            QMessageBox.information(self.page, "更新完成", f"发现新版本!\n\n{result['notes']}")
        else:
            self._set_status("已是最新版本.")
            QMessageBox.information(self.page, "已是最新", "当前已是最新版本，无需更新。")
        self.load()

    def _on_check_error(self, error):
        self._set_status(f"错误: {error}")
        QMessageBox.warning(self.page, "错误", f"检查更新失败:\n{error}")

    def download_version(self, timestamp, delta_only=True):
        if not timestamp:
            return
        sub_bundles, missing = self.service.missing_downloads(timestamp, delta_only)
        if not sub_bundles:
            QMessageBox.information(self.page, "无Bundle", "此版本无 bundle。")
            return
        label = "增量下载" if delta_only else "全量下载"
        if not delta_only:
            answer = QMessageBox.question(
                self.page,
                "全量下载确认",
                f"全量下载将下载此版本的全部 {len(sub_bundles)} 个 bundle 文件。\n\n是否仍然进行全量下载?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        if not missing:
            QMessageBox.information(self.page, "已下载", "全部已下载。")
            return
        self._download_worker = DownloadWorker(missing, str(self.service.bundles_dir / str(timestamp)))
        self._download_worker.progress.connect(
            lambda name, done, total: self.progress_changed.emit(done, total, f"{label}: {done}/{total}")
        )
        self._download_worker.item_done.connect(
            lambda name, _filename, path: self._record_download(timestamp, name, path)
        )
        self._download_worker.item_fail.connect(
            lambda bundle_hash, message: logger.error("文件下载失败: %s - %s", bundle_hash[:16], message)
        )
        self._download_worker.all_done.connect(self._download_complete)
        self._download_worker.error.connect(lambda message: self._set_status(f"下载出错: {message}"))
        self._set_status(f"{label}: 准备下载 {len(missing)} 个文件...")
        self._download_worker.start()

    def _record_download(self, timestamp, name, path):
        from app.core.version_update import record_downloaded_bundle

        record_downloaded_bundle(timestamp, name, path)

    def _download_complete(self):
        self.progress_changed.emit(0, 0, "")
        self.load()
        self._set_status("下载完成!")
        QMessageBox.information(self.page, "完成", "下载完毕!")

    def delete_version(self, timestamp):
        count = self.service.downloaded_count(timestamp)
        if count == 0:
            QMessageBox.information(self.page, "无文件", "没有已下载的 bundle。")
            return
        answer = QMessageBox.question(
            self.page,
            "确认删除",
            f"删除此版本 {count} 个文件?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        deleted = self.service.delete_version(timestamp)
        self.load()
        self._set_status(f"已删除 {deleted} 个文件。")
        QMessageBox.information(self.page, "完成", f"已删除 {deleted} 个文件。")
