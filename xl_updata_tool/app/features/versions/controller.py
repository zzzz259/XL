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
from app.features.versions.page import DownloadProgressButton, VersionPage
from app.features.versions.service import VersionService
from app.features.versions.worker import CheckUpdateThread, DownloadWorker
from app.shared.qt.tokens import DANGER, INFO, SUCCESS, TEXT_MUTED, WARNING, get_color


class VersionController(QObject):
    """协调版本工作区、更新检查、下载和删除任务。"""

    status_changed = Signal(str)
    check_state_changed = Signal(bool)
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
        self._download_controls = {}
        self._delete_buttons = {}
        self._status_items = {}
        self._active_download_timestamp = None
        self._active_download_delta_only = None
        self._download_cancel_requested = False
        self._download_progress = (0, 0)
        self._download_current_name = None
        self._download_label = None
        self._download_failed = False
        self._check_thread = None
        self._closing = False
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
        self.page.checking_message_changed.connect(self._set_status)

    def _set_status(self, message: str):
        self.status_changed.emit(message)

    def load(self):
        if self._closing:
            return []
        active_download = self._has_active_download()
        self.service.sync_local()
        versions = self.service.refresh()
        self.populate_table(versions, self.service.delta_map(versions))
        if not active_download:
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
        self._download_controls = {}
        self._delete_buttons = {}
        self._status_items = {}
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
            self._status_items[ts] = status_item
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
                self._download_controls.setdefault(ts, {})[delta_only] = button
            delete_button = self._row_button("删除已下载", DANGER, width=100)
            delete_button.clicked.connect(
                lambda _checked=False, current_ts=ts: self.delete_version(current_ts)
            )
            table.setCellWidget(row, 7, delete_button)
            self._delete_buttons[ts] = delete_button
        table.setSortingEnabled(True)
        self._version_count = len(versions)
        self._downloaded_version_count = downloaded_versions
        self._update_summary()
        if self._has_active_download():
            self._restore_active_download_ui()

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

    def check_update(self, notify_errors=True):
        if self._closing:
            return
        if self._check_thread is not None and self._check_thread.isRunning():
            self._set_status("已有更新检查正在进行，请等待当前检查完成。")
            return
        if self._check_thread is not None:
            self._set_status("上一轮更新检查尚未完成清理，请稍候。")
            return
        current = self.service.current()
        old_hashes = []
        if current:
            old_hashes = [item[0] for item in (self.service.bundles(current[0]) or [])]
        self.page.set_checking(True)
        self.check_state_changed.emit(True)
        self._check_thread = CheckUpdateThread(str(self.service.bundles_dir / "current"), old_hashes)
        self._check_thread.finished.connect(self._on_update_checked)
        self._check_thread.error.connect(
            lambda error: self._on_check_error(error, notify_errors)
        )
        self._check_thread.start()

    def _on_update_checked(self, info, versions, new_hashes, delta):
        if self._closing:
            return
        self._finish_check()
        result = self.service.register_checked(info, versions, new_hashes, delta)
        if result:
            self._set_status(f"发现新版本! 新增 {result['added']} 个 bundle.")
            QMessageBox.information(self.page, "更新完成", f"发现新版本!\n\n{result['notes']}")
        else:
            self._set_status("已是最新版本.")
            QMessageBox.information(self.page, "已是最新", "当前已是最新版本，无需更新。")
        self.load()

    def _on_check_error(self, error, notify_errors=True):
        if self._closing:
            return
        self._finish_check()
        self._set_status(f"更新检查失败，可点击‘检查更新’重试：{error}")
        if notify_errors:
            QMessageBox.warning(self.page, "错误", f"检查更新失败:\n{error}")

    def _finish_check(self):
        if self._closing:
            return
        self._check_thread = None
        self.page.set_checking(False)
        self.check_state_changed.emit(False)

    def download_version(self, timestamp, delta_only=True):
        if self._closing or not timestamp:
            return
        if self._download_worker is not None:
            self._set_status("已有下载任务正在进行，请等待当前任务完成。")
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
        self._active_download_timestamp = timestamp
        self._active_download_delta_only = delta_only
        self._download_cancel_requested = False
        self._download_progress = (0, len(missing))
        self._download_current_name = None
        self._download_label = label
        self._download_failed = False
        self._replace_with_progress_button(timestamp, delta_only)
        self._set_download_controls_enabled(False)
        progress_button = self._download_controls[timestamp][delta_only]
        progress_button.setEnabled(True)
        self._download_worker = DownloadWorker(missing, str(self.service.bundles_dir / str(timestamp)))
        self._download_worker.progress.connect(
            lambda name, done, total: self._on_download_progress(
                timestamp, label, name, done, total
            )
        )
        self._download_worker.item_done.connect(
            lambda name, _filename, path: self._record_download(timestamp, name, path)
        )
        self._download_worker.item_fail.connect(
            self._on_download_item_fail
        )
        self._download_worker.finished.connect(self._download_complete)
        self._download_worker.error.connect(self._on_download_error)
        self._set_status(f"{label}: 准备下载 {len(missing)} 个文件...")
        self._download_worker.start()

    def _replace_with_progress_button(self, timestamp, delta_only):
        button = DownloadProgressButton()
        button.clicked.connect(self.cancel_download)
        row = self._row_for_timestamp(timestamp)
        if row < 0:
            return
        self.page.table.setCellWidget(row, 5 if delta_only else 6, button)
        self._download_controls[timestamp][delta_only] = button

    def _row_for_timestamp(self, timestamp):
        table = self.page.table
        for row in range(table.rowCount()):
            item = table.item(row, 1)
            if item is not None and item.data(Qt.UserRole) == timestamp:
                return row
        return -1

    def _has_active_download(self):
        return (
            not self._closing
            and self._download_worker is not None
            and self._active_download_timestamp is not None
        )

    def _restore_active_download_ui(self):
        timestamp = self._active_download_timestamp
        delta_only = self._active_download_delta_only
        if timestamp is None or delta_only is None:
            return
        self._replace_with_progress_button(timestamp, delta_only)
        button = self._download_controls.get(timestamp, {}).get(bool(delta_only))
        if isinstance(button, DownloadProgressButton):
            button.set_progress(*self._download_progress)
        status_item = self._status_items.get(timestamp)
        if status_item:
            done, total = self._download_progress
            status_item.setText(f"下载中 ({done}/{total})")
        self._set_download_controls_enabled(False)
        if isinstance(button, DownloadProgressButton):
            button.setEnabled(not self._download_cancel_requested)
        if self._download_cancel_requested:
            self._set_status("正在取消下载…")
        elif self._download_current_name:
            done, total = self._download_progress
            self._set_status(
                f"{self._download_label} {done}/{total} · {self._download_current_name}"
            )

    def _set_download_controls_enabled(self, enabled):
        for controls in self._download_controls.values():
            for button in controls.values():
                button.setEnabled(enabled)
        for button in self._delete_buttons.values():
            button.setEnabled(enabled)

    def _on_download_progress(self, timestamp, label, name, done, total):
        if self._closing or timestamp != self._active_download_timestamp:
            return
        self._download_progress = (done, total)
        display_name = name if str(name).endswith(".bundle") else f"{name}.bundle"
        self._download_current_name = display_name
        progress_button = self._download_controls.get(timestamp, {}).get(
            bool(self._active_download_delta_only)
        )
        if isinstance(progress_button, DownloadProgressButton):
            progress_button.set_progress(done, total)
        status_item = self._status_items.get(timestamp)
        if status_item:
            status_item.setText(f"下载中 ({done}/{total})")
        label_text = "增量下载" if self._active_download_delta_only else "全量下载"
        self.progress_changed.emit(
            done, total, f"{label_text}: {done}/{total} · {display_name}"
        )
        self._set_status(f"{label_text} {done}/{total} · {display_name}")

    def cancel_download(self):
        if (
            self._closing
            or self._download_worker is None
            or not self._download_worker.isRunning()
        ):
            return
        self._download_cancel_requested = True
        self._download_worker.stop()
        self._set_status("正在取消下载…")

    def _record_download(self, timestamp, name, path):
        if self._closing:
            return
        from .version_update import record_downloaded_bundle

        record_downloaded_bundle(timestamp, name, path)

    def _on_download_item_fail(self, bundle_hash, message):
        if self._closing:
            return
        self._download_failed = True
        logger.error("文件下载失败: %s - %s", bundle_hash[:16], message)

    def _on_download_error(self, message):
        if self._closing:
            return
        self._download_failed = True
        self._set_status(f"下载出错: {message}")

    def _download_complete(self):
        if self._closing:
            return
        worker = self._download_worker
        outcome = getattr(worker, "outcome", None)
        if outcome not in {"success", "cancelled", "failed", "aborted"}:
            if self._download_cancel_requested:
                outcome = "cancelled"
            elif self._download_failed:
                outcome = "failed"
            else:
                outcome = "success"
        done, total = self._download_progress
        self._download_worker = None
        self._active_download_timestamp = None
        self._active_download_delta_only = None
        self._download_cancel_requested = False
        self._download_progress = (0, 0)
        self._download_current_name = None
        self._download_label = None
        self._download_failed = False
        self.progress_changed.emit(0, 0, "")
        self.load()
        if outcome == "cancelled":
            self._set_status(f"下载已取消 ({done}/{total})")
            return
        if outcome == "failed":
            self._set_status(f"下载失败 ({done}/{total})")
            return
        if outcome == "aborted":
            self._set_status(f"下载中止 ({done}/{total})")
            return
        self._set_status("下载完成!")
        QMessageBox.information(self.page, "完成", "下载完毕!")

    def close(self):
        """停止版本域线程，避免窗口销毁时 QThread 仍在运行。"""
        if self._closing:
            return
        self._closing = True
        self.page.set_checking(False)
        self.check_state_changed.emit(False)
        for attribute in ("_download_worker", "_check_thread"):
            worker = getattr(self, attribute, None)
            if worker is None:
                continue
            if not worker.isRunning():
                setattr(self, attribute, None)
                continue
            if attribute == "_check_thread":
                worker.requestInterruption()
            else:
                stopper = getattr(worker, "stop", None)
                if stopper is not None:
                    stopper()
                else:
                    worker.requestInterruption()
            if worker.wait(30000) or worker.wait():
                setattr(self, attribute, None)
            else:
                logger.error("版本线程未能在关闭超时内退出: %s", attribute)
        self._active_download_timestamp = None
        self._active_download_delta_only = None
        self._download_cancel_requested = False
        self._download_progress = (0, 0)
        self._download_current_name = None
        self._download_label = None
        self._download_failed = False

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
