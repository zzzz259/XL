"""Preview 功能域控制器。"""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QObject, Qt, Signal
from PySide6.QtWidgets import QListWidgetItem

from app.platform.diagnostics import logger

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
        self._connect_page()

    def _connect_page(self):
        self.page.filter_changed.connect(self.apply_filter)
        self.page.context_menu_requested.connect(self.context_menu_requested)
        self.page.item_double_clicked.connect(self.item_double_clicked)
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

    def reload_requested(self):
        self.load()

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

    def start_export(self, spine_cli: str, force=False, selected_roles=None) -> bool:
        if not self.service.material_dir.is_dir():
            self.error.emit(f"素材目录不存在：{self.service.material_dir}")
            return False
        if not os.path.isfile(spine_cli):
            self.error.emit(f"SpineViewerCLI 不存在：{spine_cli}")
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

    def _on_export_error(self, message):
        self._export_worker = None
        self.page.preview_progress.setVisible(False)
        self.error.emit(str(message))

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
