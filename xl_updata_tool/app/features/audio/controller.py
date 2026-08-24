"""音频功能域控制器。

AudioController 持有音频列表、树选择、播放器和后处理任务状态。MainWindow
只负责页面显隐、导入流程的总协调和全局状态栏，不再直接处理音频私有回调。
"""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QMimeData, QObject, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QMenu, QProgressDialog

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    QT_MULTIMEDIA_AVAILABLE = True
except ImportError:
    QAudioOutput = None
    QMediaPlayer = None
    QT_MULTIMEDIA_AVAILABLE = False

from app.platform.diagnostics import logger
from app.features.audio.page import AudioPage
from app.features.audio.service import AudioService
from app.features.audio.tree import (
    LOADED_ROLE,
    directory_path,
    iter_audio_leaves,
    populate_audio_directory,
    populate_audio_tree_roots,
    refresh_audio_tree_unread,
)
from app.features.audio.worker import AudioCatalogWorker, AudioDecryptWorker


class AudioController(QObject):
    """协调 AudioPage、AudioService、播放器和 AudioDecryptWorker。"""

    status_changed = Signal(str)
    unread_changed = Signal()
    processing_progress = Signal(str)
    processing_progress_value = Signal(int, int, str)
    processing_finished = Signal(bool)
    processing_cancelled = Signal(bool)
    processing_error = Signal(str, bool)

    def __init__(
        self,
        page: AudioPage,
        material_dir: str,
        debank_dir: str,
        lua_output_dir: str,
        output_dir: str | None = None,
        parent=None,
        service: AudioService | None = None,
    ) -> None:
        super().__init__(parent)
        self.page = page
        if service is None and output_dir is None:
            raise ValueError("AudioController requires output_dir or service")
        self.service = service or AudioService(output_dir)
        self.material_dir = material_dir
        self.debank_dir = debank_dir
        self.lua_output_dir = lua_output_dir

        self._audio_files: list[dict] = []
        self._audio_file_items = []
        self._pressed_check_state = None
        self._list_loaded = False
        self._slider_dragging = False
        self._current_path = None
        self._audio_player = None
        self._audio_output = None
        self._audio_worker = None
        self._catalog_worker = None
        self._catalog_index = None
        self._selected_names: set[str] = set()
        self._progress_dialog = None
        self._processing_shared = False

        self._connect_page()

    @property
    def processing_shared(self) -> bool:
        return self._processing_shared

    @property
    def audio_files(self) -> list[dict]:
        return list(self._audio_files)

    @property
    def has_unread(self) -> bool:
        """向 Shell 暴露领域无关的未读状态，不暴露仓库实现。"""
        if self._audio_files:
            return any(bool(info.get("unread")) for info in self._audio_files)
        return self.service.has_unread

    def _connect_page(self) -> None:
        self.page.refresh_requested.connect(lambda: self.load_catalog(force_reload=True))
        self.page.export_requested.connect(self.export_selected)
        self.page.play_selected_requested.connect(self.play_selected)
        self.page.mark_all_read_requested.connect(self.mark_all_read)
        self.page.context_menu_requested.connect(self.show_context_menu)
        self.page.item_pressed.connect(self.on_item_pressed)
        self.page.item_clicked.connect(self.on_item_clicked)
        self.page.item_double_clicked.connect(self.on_item_double_clicked)
        self.page.item_expanded.connect(self.on_item_expanded)
        self.page.play_toggled.connect(self.toggle_play)
        self.page.slider_moved.connect(self.on_slider_moved)
        self.page.slider_pressed.connect(self.on_slider_pressed)
        self.page.slider_released.connect(self.on_slider_released)
        self.page.volume_changed.connect(self.set_volume)

    def initialize_player(self) -> None:
        if self._audio_player is not None:
            return
        if not QT_MULTIMEDIA_AVAILABLE:
            logger.warning("QtMultimedia 不可用，音频播放功能受限")
            return
        self._audio_player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._audio_player.setAudioOutput(self._audio_output)
        self._audio_output.setVolume(0.8)
        self._audio_player.positionChanged.connect(self._update_position)
        self._audio_player.durationChanged.connect(self._update_duration)
        self._audio_player.playbackStateChanged.connect(self._on_playback_state_changed)

    def preload_catalog(self, force_reload: bool = False) -> None:
        """后台预热音频目录，不触碰 Qt 树，避免阻塞版本列表。"""
        if self.service.catalog_loaded and not force_reload:
            return
        self._start_catalog_worker(force_reload)

    def _start_catalog_worker(self, force_reload: bool = False) -> None:
        if self._catalog_worker is not None and self._catalog_worker.isRunning():
            return
        self._catalog_worker = AudioCatalogWorker(self.service, force=force_reload, parent=self)
        self._catalog_worker.loaded.connect(self._on_catalog_loaded)
        self._catalog_worker.failed.connect(self._on_catalog_failed)
        self._catalog_worker.finished.connect(self._on_catalog_worker_finished)
        self._catalog_worker.start()

    def load_catalog(self, force_reload: bool = False) -> None:
        """加载音频目录；优先复用预热结果，未完成时异步等待。"""
        if self._list_loaded and not force_reload:
            return
        if self.service.catalog_loaded and not force_reload:
            self._on_catalog_loaded(self.service.load_catalog())
            return
        self.page.audio_table.setEnabled(False)
        self.page.audio_title.setText("音频管理器 · 正在加载音频列表…")
        self.page.audio_status.setText("正在加载音频列表…")
        self.status_changed.emit("正在后台加载音频列表…")
        self._start_catalog_worker(force_reload)

    def _on_catalog_loaded(self, audio_files: list[dict]) -> None:
        self._audio_files = audio_files
        self._catalog_index = self.service.catalog_index
        if self._catalog_index is None:
            self._on_catalog_failed("音频目录索引未生成")
            return
        self._selected_names.clear()
        self._audio_file_items = populate_audio_tree_roots(
            self.page.audio_table,
            self._catalog_index,
            self.service.format_size,
        )
        total = len(self._audio_files)
        self.page.audio_title.setText(f"音频管理器 · 共 {total} 个音频文件")
        self.page.audio_status.setText(f"已选: 0 个 | 共 {total} 个音频文件")
        self.page.audio_empty.setVisible(total == 0)
        self.page.audio_table.setEnabled(True)
        self._list_loaded = True
        self.unread_changed.emit()
        logger.info(f"音频列表加载完成: 共 {total} 个文件")
        self.status_changed.emit(f"音频列表加载完成: {total} 个文件")

    def _on_catalog_failed(self, message: str) -> None:
        logger.error(f"音频列表加载失败: {message}")
        self.page.audio_table.setEnabled(True)
        self.page.audio_title.setText("音频管理器 · 加载失败")
        self.page.audio_status.setText(f"音频列表加载失败：{message}")
        self.status_changed.emit("音频列表加载失败，可点击刷新列表重试")

    def _on_catalog_worker_finished(self) -> None:
        worker = self._catalog_worker
        if worker is not None:
            worker.deleteLater()
        self._catalog_worker = None

    def on_item_expanded(self, item) -> None:
        if self._catalog_index is None or directory_path(item) is None:
            return
        if item.data(0, LOADED_ROLE):
            return
        populate_audio_directory(item, self._catalog_index, self.service.format_size)
        self._apply_visible_selection()
        refresh_audio_tree_unread(self.page.audio_table, self._catalog_index)

    def invalidate_catalog(self) -> None:
        self.service.invalidate()
        self._catalog_index = None
        self._list_loaded = False

    def on_item_pressed(self, item, _column: int) -> None:
        # 保留语义信号兼容；实际点击前状态由逻辑选择集提供，避免 Qt
        # 复选框默认切换与 itemClicked 的事件顺序竞争。
        self._pressed_check_state = None

    def on_item_clicked(self, item, _column: int) -> None:
        self._pressed_check_state = None
        target_names = self._target_names(item)
        if not target_names:
            return

        ctrl = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
        before_checked = target_names.issubset(self._selected_names)
        # 延迟到 Qt 完成复选框默认切换后再提交逻辑状态，兼容点击行文本
        # 和点击复选框指示器两条事件路径。
        QTimer.singleShot(
            0,
            lambda: self._apply_item_selection(target_names, not before_checked, ctrl),
        )

    def _target_names(self, item) -> set[str]:
        if self._catalog_index is not None:
            directory = directory_path(item)
            if directory is not None:
                return {
                    str(info.get("name", "")).replace("\\", "/")
                    for info in self._catalog_index.files_under(directory)
                }
        return {
            str(leaf.data(0, Qt.UserRole).get("name", "")).replace("\\", "/")
            for leaf in iter_audio_leaves(item)
            if leaf.data(0, Qt.UserRole)
        }

    def _apply_item_selection(self, target_names: set[str], checked: bool, ctrl: bool) -> None:
        if not ctrl:
            self._selected_names.clear()
        if checked:
            self._selected_names.update(target_names)
        else:
            self._selected_names.difference_update(target_names)
        self._apply_visible_selection()
        self.page.audio_table.clearSelection()
        self._update_selection_status()

    def _apply_visible_selection(self) -> None:
        table = self.page.audio_table
        table.blockSignals(True)
        try:
            def update(item) -> None:
                directory = directory_path(item)
                if directory is not None and self._catalog_index is not None:
                    names = {
                        str(info.get("name", "")).replace("\\", "/")
                        for info in self._catalog_index.files_under(directory)
                    }
                    if not names or not self._selected_names.intersection(names):
                        state = Qt.Unchecked
                    elif names.issubset(self._selected_names):
                        state = Qt.Checked
                    else:
                        state = Qt.PartiallyChecked
                    item.setCheckState(0, state)
                else:
                    info = item.data(0, Qt.UserRole) or {}
                    name = str(info.get("name", "")).replace("\\", "/")
                    item.setCheckState(0, Qt.Checked if name in self._selected_names else Qt.Unchecked)
                for index in range(item.childCount()):
                    child = item.child(index)
                    if child.data(0, Qt.UserRole + 2):
                        continue
                    update(child)

            for index in range(table.topLevelItemCount()):
                update(table.topLevelItem(index))
        finally:
            table.blockSignals(False)

    def _update_selection_status(self) -> None:
        checked = len(self._selected_names)
        self.page.audio_status.setText(f"已选: {checked} 个 | 共 {len(self._audio_files)} 个音频文件")

    def _selected_infos(self) -> list[dict]:
        selected = {
            str(name).replace("\\", "/")
            for name in self._selected_names
        }
        if selected:
            return [
                info for info in self._audio_files
                if str(info.get("name", "")).replace("\\", "/") in selected
            ]
        # 兼容迁移期外部直接构造叶节点的调用方。
        return [
            info for item in self._audio_file_items
            if item.checkState(0) == Qt.Checked
            for info in [item.data(0, Qt.UserRole)]
            if info
        ]

    def mark_all_read(self) -> None:
        changed = self.service.mark_all_read()
        for item in self._audio_file_items:
            info = item.data(0, Qt.UserRole) or {}
            item.setData(0, Qt.UserRole, {**info, "unread": False})
        refresh_audio_tree_unread(self.page.audio_table, self._catalog_index)
        self.unread_changed.emit()
        self.status_changed.emit("已将全部音频标记为已读" if changed else "当前没有未读音频")

    def export_selected(self) -> None:
        selected = self._selected_infos()
        if not selected:
            QMessageBox.information(self.page, "提示", "请先勾选要导出的音频文件")
            return
        destination = QFileDialog.getExistingDirectory(self.page, "选择导出目录")
        if not destination:
            return
        logger.info(f"导出音频：选中 {len(selected)} 个文件 → {destination}")
        success, failures = self.service.export_selected(selected, destination)
        for filename in failures:
            logger.error(f"导出失败 {filename}")
        QMessageBox.information(
            self.page,
            "导出完成",
            f"成功导出 {success} 个音频文件到:\n{destination}",
        )
        logger.info(f"导出 {success} 个音频文件到 {destination}")

    def play_selected(self) -> None:
        for info in self._selected_infos():
            self.play_file(info["path"], info["name"])
            return
        current = self.page.audio_table.currentItem()
        if current:
            info = current.data(0, Qt.UserRole)
            if info:
                self.play_file(info["path"], info["name"])

    def on_item_double_clicked(self, item, _column: int) -> None:
        info = item.data(0, Qt.UserRole) if item else None
        if info:
            self.play_file(info["path"], info["name"])

    def play_file(self, filepath: str, filename: str) -> None:
        self.initialize_player()
        if not QT_MULTIMEDIA_AVAILABLE or self._audio_player is None:
            QMessageBox.warning(self.page, "错误", "音频播放器不可用（QtMultimedia 未安装）")
            return
        if not os.path.exists(filepath):
            QMessageBox.warning(self.page, "错误", f"文件不存在: {filepath}")
            return

        self._current_path = filepath
        self._audio_player.setSource(QUrl.fromLocalFile(filepath))
        self._audio_player.play()
        relative_name = os.path.relpath(filepath, str(self.service.audio_dir))
        self.service.mark_read(relative_name)
        for item in self._audio_file_items:
            info = item.data(0, Qt.UserRole) or {}
            if info.get("path") == filepath:
                item.setData(0, Qt.UserRole, {**info, "unread": False})
                break
        refresh_audio_tree_unread(self.page.audio_table, self._catalog_index)
        self.unread_changed.emit()
        self.page.audio_now_playing.setText(f"正在播放：{filename}")
        self.page.audio_play_btn.setText("暂停")
        self.page.audio_play_btn.setEnabled(True)
        self.page.audio_slider.setEnabled(True)
        logger.info(f"播放音频: {filename}")

    def toggle_play(self) -> None:
        if not self._audio_player:
            return
        if self._audio_player.playbackState() == QMediaPlayer.PlayingState:
            self._audio_player.pause()
        else:
            self._audio_player.play()

    def _on_playback_state_changed(self, state) -> None:
        if state == QMediaPlayer.PlayingState:
            self.page.audio_play_btn.setText("暂停")
        elif state in (QMediaPlayer.PausedState, QMediaPlayer.StoppedState):
            self.page.audio_play_btn.setText("播放")

    def _update_position(self, position: int) -> None:
        duration = self._audio_player.duration() if self._audio_player else 0
        if duration > 0 and not self._slider_dragging:
            self.page.audio_slider.setRange(0, duration)
            self.page.audio_slider.setValue(position)
        self.page.audio_position_label.setText(
            f"{self.service.format_duration(position)} / {self.service.format_duration(duration)}"
        )

    def _update_duration(self, duration: int) -> None:
        if duration > 0:
            self.page.audio_slider.setRange(0, duration)

    def on_slider_moved(self, position: int) -> None:
        duration = self._audio_player.duration() if self._audio_player else 0
        self.page.audio_position_label.setText(
            f"{self.service.format_duration(position)} / {self.service.format_duration(duration)}"
        )

    def on_slider_pressed(self) -> None:
        self._slider_dragging = True

    def on_slider_released(self) -> None:
        self._slider_dragging = False
        if self._audio_player:
            self._audio_player.setPosition(self.page.audio_slider.value())

    def set_volume(self, volume: int) -> None:
        if self._audio_output:
            self._audio_output.setVolume(volume / 100.0)

    def show_context_menu(self, position) -> None:
        table = self.page.audio_table
        item = table.itemAt(position)
        if not item:
            return
        info = item.data(0, Qt.UserRole)
        if not info:
            return
        filepath = info["path"]
        menu = QMenu(self.page)
        menu.setObjectName("contextMenu")
        act_open = menu.addAction("打开文件所在目录")
        act_copy = menu.addAction("复制文件")
        act_play = menu.addAction("播放")
        action = menu.exec(table.mapToGlobal(position))
        if action == act_open:
            self.open_file_location(filepath)
        elif action == act_copy:
            self.copy_file(filepath)
        elif action == act_play:
            self.play_file(filepath, info["name"])

    @staticmethod
    def open_file_location(file_path: str) -> None:
        logger.info(f"打开文件所在目录: {file_path}")
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", file_path], creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                folder = os.path.dirname(file_path)
                subprocess.Popen(["xdg-open", folder] if sys.platform.startswith("linux") else ["open", folder])
        except Exception as error:
            logger.error(f"打开目录失败: {error}")

    @staticmethod
    def copy_file(file_path: str) -> None:
        if not os.path.exists(file_path):
            return
        try:
            clipboard = QGuiApplication.clipboard()
            mime_data = QMimeData()
            mime_data.setUrls([QUrl.fromLocalFile(file_path)])
            clipboard.setMimeData(mime_data)
            logger.info(f"已复制文件到剪贴板: {file_path}")
        except Exception as error:
            logger.error(f"复制文件失败: {error}")

    def cancel_audio_worker(self) -> bool:
        worker = self._audio_worker
        if worker is None:
            return True
        worker.cancel()
        if not worker.wait(30000):
            logger.error("音频解密线程未能在取消超时内退出，不启动新的音频任务")
            return False
        self._audio_worker = None
        return True

    def start_decrypt(self, force: bool = False, shared_dialog=None) -> bool:
        if not self.cancel_audio_worker():
            self.status_changed.emit("上一轮音频处理尚未退出，已取消启动新的任务")
            return False

        self.status_changed.emit("正在处理音频文件...")
        self._audio_worker = AudioDecryptWorker(
            self.material_dir,
            str(self.service.audio_dir),
            self.debank_dir,
            force=force,
            lua_output_dir=self.lua_output_dir,
            parent=self,
        )
        self._audio_worker.progress.connect(self._on_progress)
        self._audio_worker.progress_value.connect(self._on_progress_value)
        self._audio_worker.finished_decrypt.connect(self._on_finished)
        self._audio_worker.cancelled_decrypt.connect(self._on_cancelled)
        self._audio_worker.error.connect(self._on_error)
        self._audio_worker.start()

        self._processing_shared = shared_dialog is not None
        if shared_dialog is not None:
            self._progress_dialog = shared_dialog
            shared_dialog.setLabelText("正在处理音频...\n准备转换音频中间文件")
            shared_dialog.setRange(0, 0)
        else:
            self._progress_dialog = QProgressDialog("正在处理音频...", "取消", 0, 100, self.page)
            self._progress_dialog.setWindowTitle("音频处理")
            self._progress_dialog.setWindowModality(Qt.NonModal)
            self._progress_dialog.setMinimumDuration(0)
            self._progress_dialog.setAutoClose(False)
            self._progress_dialog.setAutoReset(False)
            self._progress_dialog.setMinimumWidth(520)
            self._progress_dialog.setMinimumHeight(160)
        self._progress_dialog.canceled.connect(self._audio_worker.cancel)
        self._progress_dialog.show()
        return True

    def _on_progress(self, message: str) -> None:
        self.status_changed.emit(message)
        self.processing_progress.emit(message)

    def _on_progress_value(self, current: int, total: int, message: str) -> None:
        if self._progress_dialog:
            if total > 0:
                self._progress_dialog.setLabelText(f"{message}\n已处理 {current}/{total}")
                self._progress_dialog.setRange(0, total)
                self._progress_dialog.setValue(current)
            else:
                self._progress_dialog.setLabelText(f"{message}\n处理中...")
                self._progress_dialog.setRange(0, 0)
        self.status_changed.emit(message)
        self.processing_progress_value.emit(current, total, message)

    def _close_private_dialog(self) -> None:
        if self._progress_dialog is not None and not self._processing_shared:
            self._progress_dialog.close()
        self._progress_dialog = None

    def _on_finished(self) -> None:
        was_shared = self._processing_shared
        self._close_private_dialog()
        self._audio_worker = None
        self._processing_shared = False
        self.status_changed.emit("音频处理完成")
        self.invalidate_catalog()
        self.load_catalog(force_reload=True)
        self.processing_finished.emit(was_shared)

    def _on_cancelled(self) -> None:
        self._close_private_dialog()
        self._audio_worker = None
        was_shared = self._processing_shared
        self._processing_shared = False
        self.status_changed.emit("音频处理已取消，已完成的文件已保留")
        self.invalidate_catalog()
        self.load_catalog(force_reload=True)
        if was_shared:
            self.processing_cancelled.emit(was_shared)

    def _on_error(self, message: str) -> None:
        was_shared = self._processing_shared
        self._close_private_dialog()
        self._audio_worker = None
        self._processing_shared = False
        logger.error(f"音频解密失败: {message}")
        self.status_changed.emit("音频处理失败")
        self.invalidate_catalog()
        self.load_catalog(force_reload=True)
        self.processing_error.emit(message, was_shared)

    def close(self) -> None:
        self.cancel_audio_worker()
        if self._catalog_worker is not None and self._catalog_worker.isRunning():
            self._catalog_worker.requestInterruption()
            if not self._catalog_worker.wait(30000):
                logger.error("音频目录预热线程未能在关闭超时内退出")
        self._catalog_worker = None
        if self._audio_player:
            self._audio_player.stop()
