"""Preview 功能域控制器。"""

from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QPoint, QObject, Qt, QMimeData, QUrl, Signal
from PySide6.QtWidgets import QApplication, QDialog, QListWidgetItem, QMenu, QMessageBox

from app.platform.diagnostics import logger

from .adapter import extract_skin_name_from_png, is_composite_png
from .item import build_preview_item
from .page import PreviewPage
from .service import PreviewService
from .worker import ImageLoadWorker, PreviewExportWorker


class PreviewController(QObject):
    """协调页面、预览目录、缩略图加载和图片导出任务。"""

    progress_changed = Signal(int, int, str)
    status_changed = Signal(str)
    error = Signal(str)
    export_finished = Signal(bool, str)
    context_menu_requested = Signal(QPoint)
    item_double_clicked = Signal(QListWidgetItem)

    def __init__(self, page: PreviewPage, service: PreviewService, parent=None):
        super().__init__(parent)
        self.page = page
        self.service = service
        self.skel_map: dict[str, tuple[str, str]] = {}
        self.image_paths: list[str] = []
        self._image_worker = None
        self._export_worker = None
        self._single_composite_worker = None
        self._single_export_worker = None
        self._batch_worker = None
        self._composite_worker = None
        self._batch_exporting = False
        self._batch_settings = {}
        self._batch_auto_open = False
        self._batch_regular_entries = []
        self._batch_spine_cli = ""
        self._batch_comp_success = 0
        self._batch_comp_fail = 0
        self._connect_page()

    def _connect_page(self):
        self.page.filter_changed.connect(self.apply_filter)
        self.page.reload_requested.connect(self.reload_requested)
        self.page.context_menu_requested.connect(self.show_context_menu)
        self.page.item_double_clicked.connect(self.open_item)
        self.page.selection_changed.connect(self.update_status)

    def load(self):
        """异步加载最终预览图片并刷新角色筛选。"""
        preview_dir = self.service.ensure_output_dir()
        self.skel_map = self.service.skel_map()
        self._populate_filter()
        self.page.image_list.clear()
        self.page.preview_progress.setVisible(True)
        self.page.preview_progress.setValue(0)
        self.page.empty_label.setVisible(False)
        self._cancel_image_worker()
        self._image_worker = ImageLoadWorker(str(preview_dir), 150)
        self._image_worker.progress.connect(self._on_load_progress)
        self._image_worker.image_loaded.connect(self._on_thumbnail_loaded)
        self._image_worker.finished_loading.connect(self._on_load_finished)
        self._image_worker.start()

    def _populate_filter(self):
        current = self.page.character_filter.currentData()
        combo = self.page.character_filter
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("全部角色", "")
        for role in self.service.preview_roles():
            combo.addItem(role, role)
        index = combo.findData(current or "")
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def apply_filter(self):
        role = self.page.character_filter.currentData()
        for index in range(self.page.image_list.count()):
            item = self.page.image_list.item(index)
            data = item.data(Qt.UserRole)
            png = data.get("png", "") if isinstance(data, dict) else ""
            visible = not role or f"/{role}/" in png.replace("\\", "/")
            item.setHidden(not visible)
        self.update_status()

    def update_status(self):
        total = self.page.image_list.count()
        selected = len(self.page.image_list.selectedItems())
        self.page.preview_status.setText(f"共 {total} 张图片 · 已选 {selected}")

    def _on_load_progress(self, current, total):
        self.page.preview_progress.setMaximum(total)
        self.page.preview_progress.setValue(current)
        self.page.preview_progress.setFormat(f"加载中... {current}/{total}")
        self.page.preview_title.setText(f"角色预览器 · 共 {total} 张图片 · 加载中 {current}/{total}")
        self.progress_changed.emit(current, total, "加载预览图片")

    def _on_thumbnail_loaded(self, image_path, thumbnail):
        self.page.image_list.addItem(build_preview_item(image_path, thumbnail, self.skel_map))

    def _on_load_finished(self, loaded_paths):
        self._image_worker = None
        self.image_paths = list(loaded_paths)
        self.page.preview_progress.setVisible(False)
        self.page.preview_title.setText(f"角色预览器 · 共 {len(self.image_paths)} 张图片")
        self.page.empty_label.setVisible(not self.image_paths)
        self.update_status()
        message = f"图片预览: 共 {len(self.image_paths)} 张图片"
        self.status_changed.emit(message)
        logger.info("预览图片加载完成，共 %s 张", len(self.image_paths))

    def start_preview_or_export(self):
        """有最终图片时加载，没有时启动立绘导出。"""
        if self.service.has_images():
            logger.info(
                "预览目录已存在 %s 张图片，直接加载",
                len(self.service.image_paths()),
            )
            self.load()
            return
        self.start_export_from_tools()

    def start_export_from_tools(self, force=False, selected_roles=None) -> bool:
        """使用项目内 Spine CLI 启动预览导出。"""
        from app.platform.paths import get_tools_dir

        spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")
        self.page.preview_progress.setVisible(True)
        self.page.preview_progress.setValue(0)
        return self.start_export(spine_cli, force=force, selected_roles=selected_roles)

    def reload_requested(self):
        """重新选择角色并导出预览图片。"""
        from app.ui.dialogs.character_select import CharacterSelectDialog

        roles = self.service.cardspine_roles()
        if not roles:
            QMessageBox.warning(self.page, "提示", "未找到角色立绘，请先导入资源")
            return
        dialog = CharacterSelectDialog(roles, self.page)
        if dialog.exec() != QDialog.Accepted:
            return
        selected = dialog.selected_roles()
        if not selected:
            QMessageBox.information(self.page, "提示", "未选择任何角色")
            return
        logger.info("重新加载预览图片，选中 %s 个角色", len(selected))
        self.start_export_from_tools(force=False, selected_roles=selected)

    def start_export(self, spine_cli: str, force=False, selected_roles=None) -> bool:
        if not self.service.material_dir.is_dir():
            self._notify_export_error(f"素材目录不存在：{self.service.material_dir}")
            return False
        if not os.path.isfile(spine_cli):
            self._notify_export_error(f"SpineViewerCLI 不存在：{spine_cli}")
            return False
        self.cancel_export()
        self.service.ensure_output_dir()
        self.page.preview_progress.setVisible(True)
        self.page.preview_progress.setValue(0)
        self._export_worker = PreviewExportWorker(
            str(self.service.material_dir),
            str(self.service.preview_dir),
            spine_cli,
            force=force,
            selected_roles=selected_roles,
            parent=self,
        )
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.export_finished.connect(self._on_export_finished)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()
        return True

    def _on_export_progress(self, current, total):
        self.page.preview_progress.setMaximum(total)
        self.page.preview_progress.setValue(current)
        self.page.preview_progress.setFormat(f"导出中... {current}/{total}")
        self.progress_changed.emit(current, total, "导出预览图片")

    def _on_export_finished(self, success, summary):
        self._export_worker = None
        self.page.preview_progress.setVisible(False)
        self.export_finished.emit(success, summary)
        if self.page.isVisible():
            QMessageBox.information(self.page, "导出完成", summary)
            self.load()

    def _on_export_error(self, message):
        self._export_worker = None
        self.page.preview_progress.setVisible(False)
        self._notify_export_error(str(message))

    def _notify_export_error(self, message):
        self.error.emit(str(message))
        QMessageBox.warning(self.page, "错误", f"预览导出失败:\n{message}")

    def cancel_export(self):
        if self._export_worker is not None:
            self._export_worker.cancel()
            self._export_worker.wait(2000)
            self._export_worker = None

    def cancel(self):
        self.cancel_export()
        self._cancel_image_worker()

    def _cancel_image_worker(self):
        if self._image_worker is not None:
            self._image_worker.cancel()
            self._image_worker.wait(2000)
            self._image_worker = None

    def export_with_dialog(self, skel_path, atlas_path, default_format="MP4", skin_name=None):
        from .export_controller import export_with_dialog

        return export_with_dialog(self, skel_path, atlas_path, default_format, skin_name)

    def export_composite_video(self, png_path, default_format="MP4", skin_name=None):
        from .export_controller import export_composite_video

        return export_composite_video(self, png_path, default_format, skin_name)

    def batch_export_with_dialog(self, entries_with_png, default_format="MP4"):
        from .export_controller import batch_export_with_dialog

        return batch_export_with_dialog(self, entries_with_png, default_format)

    def show_context_menu(self, position):
        """显示预览条目菜单，导出任务状态留在 PreviewController。"""
        item_at_pos = self.page.image_list.itemAt(position)
        if item_at_pos and not item_at_pos.isSelected():
            self.page.image_list.clearSelection()
            item_at_pos.setSelected(True)

        selected_items = self.page.image_list.selectedItems()
        if not selected_items:
            return

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

        has_skel = bool(entries)
        has_png = bool(png_only_entries)
        if not has_skel and not has_png:
            return

        menu = QMenu(self.page)
        menu.setObjectName("contextMenu")
        is_multi = len(selected_items) > 1
        act_batch_gif = act_batch_video = act_open = act_copy = None
        act_export_gif = act_export_video = None

        if is_multi:
            if has_skel:
                act_batch_gif = menu.addAction(f"批量导出 GIF（{len(entries)} 个）")
                act_batch_video = menu.addAction(f"批量导出视频（{len(entries)} 个）")
                if has_png:
                    menu.addSeparator()
            if has_png:
                act_open = menu.addAction("打开文件所在目录")
                act_copy = menu.addAction("复制文件")
        else:
            data = selected_items[0].data(Qt.UserRole)
            png_path = data.get("png", "")
            act_open = menu.addAction("打开文件所在目录")
            act_copy = menu.addAction("复制文件")
            if has_skel:
                menu.addSeparator()
                if is_composite_png(png_path):
                    act_export_gif = menu.addAction("导出合成 GIF")
                    act_export_video = menu.addAction("导出合成视频")
                else:
                    act_export_gif = menu.addAction("导出 GIF")
                    act_export_video = menu.addAction("导出视频")

        action = menu.exec(self.page.image_list.mapToGlobal(position))
        if is_multi:
            if action == act_batch_gif:
                self.batch_export_with_dialog(entries, "GIF")
            elif action == act_batch_video:
                self.batch_export_with_dialog(entries, "MP4")
            elif action in (act_open, act_copy):
                paths = png_only_entries or [entry[2] for entry in entries]
                (self.open_file_location if action == act_open else self.copy_file_to_clipboard)(paths[0])
            return

        data = selected_items[0].data(Qt.UserRole)
        png_path = data.get("png", "")
        if action == act_open:
            self.open_file_location(png_path)
        elif action == act_copy:
            self.copy_file_to_clipboard(png_path)
        elif has_skel and action == act_export_gif:
            skin_name = extract_skin_name_from_png(png_path)
            if is_composite_png(png_path):
                self.export_composite_video(png_path, "GIF", skin_name)
            else:
                self.export_with_dialog(entries[0][0], entries[0][1], "GIF", skin_name)
        elif has_skel and action == act_export_video:
            skin_name = extract_skin_name_from_png(png_path)
            if is_composite_png(png_path):
                self.export_composite_video(png_path, "MP4", skin_name)
            else:
                self.export_with_dialog(entries[0][0], entries[0][1], "MP4", skin_name)

    def open_item(self, item):
        """双击预览条目，打开同目录图片查看器。"""
        data = item.data(Qt.UserRole)
        if not data:
            return
        png_path = data.get("png", "")
        if not png_path or not os.path.exists(png_path):
            return
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
        from app.ui.dialogs.image_viewer import ImageViewerDialog

        logger.debug("双击预览: %s, 索引 %s/%s", png_path, current_index, len(all_pngs))
        ImageViewerDialog(all_pngs, current_index, self.page).exec()

    def open_file_location(self, file_path):
        logger.info("打开文件所在目录: %s", file_path)
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(file_path)])
        except Exception as error:
            logger.error("打开文件位置失败: %s", error)
            QMessageBox.warning(self.page, "错误", f"打开文件位置失败:\n{error}")

    def copy_file_to_clipboard(self, file_path):
        logger.info("复制文件: %s", file_path)
        try:
            mime_data = QMimeData()
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(file_path))])
            QApplication.clipboard().setMimeData(mime_data)
            self.status_changed.emit(f"已复制文件: {os.path.basename(file_path)}")
        except Exception as error:
            logger.error("复制文件失败: %s", error)
            QMessageBox.warning(self.page, "错误", f"复制文件失败:\n{error}")
