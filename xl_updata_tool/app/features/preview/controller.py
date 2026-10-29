"""Preview 功能域控制器。"""

from __future__ import annotations

import os
from pathlib import Path
import json
import subprocess
import tempfile
from collections import OrderedDict

from PySide6.QtCore import QPoint, QObject, Qt, QMimeData, QUrl, Signal, QThread
from PySide6.QtWidgets import QApplication, QDialog, QListWidgetItem, QMenu, QMessageBox

from app.platform.diagnostics import logger
from app.platform.processes import run_external_process, update_process_manifest

from .item import build_preview_item
from .page import PreviewPage
from .service import PreviewService
from .worker import ImageLoadWorker, PreviewExportWorker, PreviewPostprocessWorker
from .dialogs.export_settings import ExportSettingsDialog
from .export_plan import ExportSettings, build_default_export_plan, build_export_plan
from .character_names import CharacterNameResolver
from .spine_adapter import build_spine_export_command, get_animation_metadata, get_animation_names
from .resource_model import display_skin_key
from .scene_capabilities import portrait_scene_capabilities
from .portrait_config.asset_index import AssetIndex
from .portrait_scene_export import (
    build_portrait_scene_export_jobs,
    prepare_portrait_scene_export_job,
    scene_resource_family,
)
from .output_browser import OutputBrowserCatalog
from .unread import PreviewUnreadSnapshot, build_preview_unread_snapshot


class PreviewController(QObject):
    """协调页面、预览目录、缩略图加载和图片导出任务。"""

    progress_changed = Signal(int, int, str)
    status_changed = Signal(str)
    error = Signal(str)
    export_finished = Signal(bool, str)
    context_menu_requested = Signal(QPoint)
    item_double_clicked = Signal(QListWidgetItem)
    processing_finished = Signal(bool)
    processing_cancelled = Signal(bool)
    processing_error = Signal(str, bool)
    processing_progress_value = Signal(int, int, str)
    badge_changed = Signal()

    def __init__(self, page: PreviewPage, service: PreviewService, parent=None):
        super().__init__(parent)
        self.page = page
        self.service = service
        self.skel_map: dict[str, tuple[str, str]] = {}
        self.image_paths: list[str] = []
        self._thumbnail_cache = {}
        self._image_worker = None
        self._retired_image_workers = set()
        self._closing = False
        self._export_worker = None
        self._legacy_export_result = None
        self._selected_export_worker = None
        self._selected_export_cancelled = False
        self._selected_export_result = None
        self._selected_preflight_failures = ()
        self._selected_not_applicable_count = 0
        self._postprocess_result = None
        self.last_postprocess_summary = None
        self._refresh_worker = None
        self._single_composite_worker = None
        self._single_export_worker = None
        self._batch_worker = None
        self._composite_worker = None
        self._postprocess_worker = None
        self._postprocess_shared = False
        self._postprocess_dialog = None
        self._batch_exporting = False
        self._batch_settings = {}
        self._batch_auto_open = False
        self._batch_regular_entries = []
        self._batch_spine_cli = ""
        self._batch_comp_success = 0
        self._batch_comp_fail = 0
        self._material_navigation_stack: list[str] = []
        self._character_navigation_stack: list[str] = []
        self._loaded = False
        self._preloaded = False
        self._preloaded_catalog = None
        self._preloaded_material_catalog = None
        self._connect_page()

    @property
    def processing_shared(self) -> bool:
        return self._postprocess_shared

    @property
    def has_unread(self) -> bool:
        """Return whether any published preview leaf is still unread."""
        return self.unread_snapshot.has_unread(self.service.resource_state)

    @property
    def unread_snapshot(self) -> PreviewUnreadSnapshot:
        return build_preview_unread_snapshot(self.service)

    def start_postprocess(self, force: bool = False, shared_dialog=None, import_result=None) -> bool:
        """Run image discovery/material publishing after AS import."""
        del force  # The publisher is content-addressed and safe to repeat.
        worker = self._postprocess_worker
        if worker is not None:
            self.status_changed.emit("图片资源预处理已在进行中")
            return False
        self.status_changed.emit("正在处理图片资源：发现 Spine、切割图集…")
        worker = PreviewPostprocessWorker(self.service, self, import_result=import_result)
        worker.progress_value.connect(self._on_postprocess_progress)
        worker.detail_progress.connect(self._on_postprocess_detail_progress)
        worker.finished_processing.connect(self._on_postprocess_finished)
        worker.cancelled_processing.connect(self._on_postprocess_cancelled)
        worker.error.connect(self._on_postprocess_error)
        worker.finished.connect(self._on_postprocess_thread_finished)
        self._postprocess_result = None
        self._postprocess_worker = worker
        self._postprocess_shared = shared_dialog is not None
        self._postprocess_dialog = shared_dialog
        if shared_dialog is not None:
            shared_dialog.setLabelText("正在处理图片资源…\n发现 Spine、切割图集和大头照")
            shared_dialog.setRange(0, 5)
            shared_dialog.setValue(0)
            shared_dialog.canceled.connect(worker.cancel)
            shared_dialog.show()
        worker.start()
        return True

    def cancel_postprocess(self) -> bool:
        worker = self._postprocess_worker
        if worker is None:
            return True
        worker.cancel()
        self.status_changed.emit("正在取消图片资源预处理…")
        return True

    def _on_postprocess_progress(self, current, total, message):
        if self._postprocess_dialog is not None and hasattr(self._postprocess_dialog, "set_stage_progress"):
            self._postprocess_dialog.set_stage_progress("图片资源预处理", current, total)
            self._postprocess_dialog.setLabelText(message)
        self.status_changed.emit(message)
        self.processing_progress_value.emit(current, total, message)

    def _on_postprocess_detail_progress(self, current, total, message):
        if self._postprocess_dialog is not None and hasattr(self._postprocess_dialog, "set_category_progress"):
            self._postprocess_dialog.set_category_progress(message, current, total)

    def _finish_postprocess_worker(self):
        worker = self._postprocess_worker
        if worker is not None:
            worker.deleteLater()
        self._postprocess_worker = None

    def _on_postprocess_finished(self, summary):
        self._postprocess_result = ('finished', summary)

    def _on_postprocess_thread_finished(self):
        result = self._postprocess_result
        self._finish_postprocess_worker()
        if result is None:
            result = ('error', '图片资源预处理未返回结果')
        kind, value = result
        if kind == 'cancelled':
            self._complete_postprocess_cancelled()
        elif kind == 'error':
            self._complete_postprocess_error(value)
        else:
            self._complete_postprocess_finished(value)

    def _complete_postprocess_finished(self, summary):
        self.last_postprocess_summary = summary
        was_shared = self._postprocess_shared
        self._postprocess_shared = False
        self._postprocess_dialog = None
        self._loaded = False
        self._preloaded = False
        material_summary = summary.materials
        portrait_text = ""
        portrait = summary.portrait
        packages = getattr(summary, "packages", None)
        if portrait is not None:
            if portrait.status in {"complete", "partial"}:
                state = "完成" if portrait.status == "complete" else "部分完成"
                portrait_text = (
                    f"；立绘场景{state} {portrait.complete}，不完整 {portrait.incomplete}"
                )
            elif portrait.status == "skipped":
                portrait_text = f"；立绘场景跳过（{portrait.diagnostic}）"
            elif portrait.status == "cancelled":
                portrait_text = "；立绘场景生成已取消，已完成缓存保留"
        if packages is not None:
            portrait_text += f"；皮肤包成功 {packages.published}，失败 {packages.failed}"
            if packages.metadata_incomplete:
                portrait_text += f"，元数据不完整 {packages.metadata_incomplete}"
        self.status_changed.emit(
            f"图片资源预处理{'部分完成' if summary.partial else '完成'}：Spine {summary.spine.published} 组，"
            f"游戏素材成功 {material_summary.exported}，失败 {material_summary.failed}"
            f"{portrait_text}"
        )
        self.badge_changed.emit()
        if not self._closing:
            self.preload_index()
        self.processing_finished.emit(was_shared)

    def _on_postprocess_cancelled(self):
        self._postprocess_result = ('cancelled', None)

    def _complete_postprocess_cancelled(self):
        was_shared = self._postprocess_shared
        self._postprocess_shared = False
        self._postprocess_dialog = None
        self.status_changed.emit("图片资源预处理已取消，已完成的文件已保留")
        if was_shared:
            self.processing_cancelled.emit(was_shared)

    def _on_postprocess_error(self, message):
        self._postprocess_result = ('error', message)

    def _complete_postprocess_error(self, message):
        was_shared = self._postprocess_shared
        self._postprocess_shared = False
        self._postprocess_dialog = None
        logger.error("图片资源预处理失败: %s", message)
        self.status_changed.emit("图片资源预处理失败")
        self.processing_error.emit(message, was_shared)

    def _connect_page(self):
        self.page.filter_changed.connect(self.apply_filter)
        self.page.reload_requested.connect(self.reload_requested)
        self.page.context_menu_requested.connect(self.show_context_menu)
        self.page.item_double_clicked.connect(self.open_item)
        self.page.selection_changed.connect(self.update_status)
        self.page.portrait_scene_selection_changed.connect(self._on_portrait_scene_selected)
        if hasattr(self.page, "export_selected_requested"):
            self.page.export_selected_requested.connect(self._on_export_selected_requested)
        self.page.export_basic_requested.connect(self._on_export_basic_requested)
        self.page.portrait_refresh_requested.connect(self.start_portrait_refresh)
        if hasattr(self.page, "character_browser"):
            self.page.character_browser.path_activated.connect(self._open_character_folder)
            self.page.btn_character_up.clicked.connect(self._reset_character_output_root)
            self.page.btn_open_character_folder.clicked.connect(self._open_current_character_folder)
        if hasattr(self.page, "material_browser"):
            self.page.material_browser.path_activated.connect(self._open_material_folder)
            self.page.btn_material_up.clicked.connect(self._go_material_up)
            self.page.btn_open_material_folder.clicked.connect(self._open_current_material_folder)
        if hasattr(self.page, "btn_mark_all_read"):
            self.page.btn_mark_all_read.clicked.connect(self.mark_all_read)

    def _open_current_character_folder(self) -> None:
        folder = self.page.character_browser.root_path or str(self.service.preview_dir)
        self._open_output_folder(folder)

    def _open_current_material_folder(self) -> None:
        folder = self.page.material_browser.root_path or str(
            self.service.preview_dir.parent / "game_material"
        )
        self._open_output_folder(folder)

    def _open_output_folder(self, folder: str) -> None:
        path = os.path.normpath(os.path.abspath(folder))
        if not os.path.isdir(path):
            QMessageBox.warning(self.page, "文件夹不存在", f"当前预览文件夹不存在：\n{path}")
            return
        try:
            subprocess.Popen(["explorer", path])
        except Exception as error:
            logger.error("打开预览输出文件夹失败: %s", error)
            QMessageBox.warning(self.page, "打开文件夹失败", f"无法打开当前预览文件夹：\n{error}")

    def _reset_character_output_root(self):
        current = self.page.character_browser.root_path
        if current and str(current) != str(self.service.preview_dir):
            self.mark_browser_folder_read(current, "character")
        self._character_navigation_stack.clear()
        self.page.reset_character_output_root(self.service.preview_dir, self.service.resource_state)

    def _open_character_folder(self, folder: str) -> None:
        current = self.page.character_browser.root_path
        if current:
            self._character_navigation_stack.append(current)
        self.page.open_character_output_folder(folder, self.service.resource_state)

    def _open_material_folder(self, folder: str) -> None:
        current = self.page.material_browser.root_path
        self._material_navigation_stack.append(current)
        self.page.open_material_output_folder(folder, self.service.resource_state)

    def _go_material_up(self) -> None:
        current = self.page.material_browser.root_path
        if current:
            self.mark_browser_folder_read(current, "materials")
        if not self._material_navigation_stack:
            return
        previous = self._material_navigation_stack.pop()
        if previous:
            self.page.open_material_output_folder(previous, self.service.resource_state)
            return
        catalog = self.service.discover_processed_game_materials()
        self.page.set_game_material_catalog(catalog, self.service.resource_state)

    def mark_browser_folder_read(self, root, category: str) -> bool:
        """Mark only image leaves visible in a folder as read, then save once."""
        if not root:
            return False
        if category == "character":
            fingerprints = OutputBrowserCatalog(root).file_fingerprints(recursive=False)
        else:
            fingerprints = OutputBrowserCatalog(root).file_fingerprints(recursive=False)
        changed = self.service.resource_state.mark_many_read(fingerprints)
        if changed:
            self.service.resource_state.save()
        if category == "character":
            self.page.character_browser.set_root(root, self.service.resource_state)
        elif category in {"materials", "game_material"}:
            self.page.material_browser.set_root(root, self.service.resource_state)
        self.page.refresh_unread_badges(self.unread_snapshot)
        self.badge_changed.emit()
        return changed

    def mark_all_read(self) -> None:
        """Mark every preview resource read and refresh all visible views."""
        state = self.service.resource_state
        catalog = self.service.load_published_preview_resources()
        material_catalog = self.service.discover_processed_game_materials()
        snapshot = self.unread_snapshot
        state.mark_many_read(snapshot.spine | snapshot.character_files | snapshot.material_files)
        state.save()
        self.page.set_character_name_resolver(
            CharacterNameResolver.from_output_root(self.service.preview_dir.parent)
        )
        self.page.set_spine_catalog(catalog, state)
        self._material_navigation_stack.clear()
        self._character_navigation_stack.clear()
        self.page.set_game_material_catalog(material_catalog, state)
        self.page.set_character_output_root(self.service.preview_dir, state)
        self.page.refresh_unread_badges(self.unread_snapshot)
        self.badge_changed.emit()
        self.status_changed.emit("已将图片预览资源全部标记为已读")

    def _on_export_selected_requested(self):
        if self._export_worker is not None:
            self.cancel_export()
            return
        if self._refresh_worker is not None:
            self._refresh_worker.cancel()
            self.page.set_export_busy(True, cancelling=True)
            return
        if self._selected_export_worker is not None:
            self.cancel_selected_export()
            return
        self.start_selected_export()

    def _on_export_basic_requested(self):
        return self.start_selected_export(options={
            'include_interactions': True, 'include_effects': False, 'allow_basic': True,
        })

    def start_portrait_refresh(self):
        from .workers.portrait_refresh import PortraitRefreshWorker

        if self._refresh_worker is not None or self._selected_export_worker is not None or self._postprocess_worker is not None:
            return False
        scenes = tuple(self.page.action_portrait_scenes())
        if not scenes:
            self.status_changed.emit('未选择场景工程')
            return False
        worker = PortraitRefreshWorker(self.service, scenes, self)
        worker.progress.connect(self._on_portrait_refresh_progress)
        worker.result_ready.connect(self._on_portrait_refresh_result)
        worker.error.connect(self._on_portrait_refresh_error)
        worker.finished.connect(self._on_portrait_refresh_finished)
        self._refresh_worker = worker
        self._refresh_result = None
        self.page.set_export_busy(True)
        self.status_changed.emit('正在更新选中场景的同版工程…')
        worker.start()
        return True

    def _on_portrait_refresh_progress(self, event):
        self.status_changed.emit(f"{event.get('skin_id', '')} · {event.get('phase', '更新场景工程')} · {event.get('status', '')}")

    def _on_portrait_refresh_result(self, result):
        self._refresh_result = result

    def _on_portrait_refresh_error(self, message):
        self._refresh_result = str(message)

    def _on_portrait_refresh_finished(self):
        inspected = self.page.spine_tree.selected_portrait_scenes()
        worker = self._refresh_worker
        self._refresh_worker = None
        if worker is not None:
            worker.deleteLater()
        result = self._refresh_result
        self.page.set_export_busy(False)
        self._loaded = self._preloaded = False
        if not self._closing:
            self.preload_index()
            if inspected:
                self.page.spine_tree.restore_current_portrait_scene(inspected[0])
        if isinstance(result, str) or result is None:
            self.status_changed.emit(f'场景工程更新失败：{result}')
        else:
            message = f'场景工程更新：成功 {result.published}，失败 {result.failed}'
            if result.cancelled:
                message += '，已取消'
            if result.failures:
                message += '；' + '；'.join(item['error'] for item in result.failures)
            self.status_changed.emit(message)
        self.badge_changed.emit()

    def discover_resources(self):
        """Refresh the three resource views and publish game materials.

        ``data/material`` is treated as read-only staging input here.  The
        material exporter is the only path that writes derived resources, and
        it always targets the service's output root.
        """
        self.status_changed.emit("正在发现角色 Spine、皮肤和游戏素材…")
        catalog = self.service.discover_preview_resources()
        self.page.set_character_name_resolver(
            CharacterNameResolver.from_output_root(self.service.preview_dir.parent)
        )
        self.page.set_spine_catalog(catalog, self.service.resource_state)

        material_catalog = self.service.discover_game_materials()
        summary = self.service.export_game_materials(material_catalog)
        processed_catalog = self.service.discover_processed_game_materials()
        self._loaded = False
        self._preloaded = False
        self._material_navigation_stack.clear()
        self.page.set_game_material_catalog(processed_catalog, self.service.resource_state)
        self.page.refresh_unread_badges(self.unread_snapshot)
        if summary.failed:
            self.status_changed.emit(
                f"资源发现完成：导出 {summary.exported} 项，失败 {summary.failed} 项"
            )
        else:
            self.status_changed.emit(
                f"资源发现完成：场景 {len(catalog.portrait_scenes)} 个，"
                f"原始 Spine 资源 {len(catalog.skins)} 项"
            )
        self.badge_changed.emit()
        return catalog

    def _on_portrait_scene_selected(self, scenes) -> None:
        if not scenes:
            return
        scene = scenes[0]
        errors = [item for item in scene.validate() if item.severity == "error"]
        if errors:
            self.status_changed.emit(
                f"立绘场景 {scene.character_id or '独立资源'} / {scene.skin_id}："
                f"{len(errors)} 项配置问题；悬停场景行可查看详情"
            )
        else:
            self.status_changed.emit(
                f"已查看立绘场景 {scene.character_id or '独立资源'} / {scene.skin_id} "
                f"（版本 {scene.version_timestamp}）"
            )

    def preload_index(self) -> bool:
        """Prepare catalog/state views without starting the full image scan."""
        if self._preloaded:
            return False
        catalog = self.service.load_published_preview_resources()
        material_catalog = self.service.discover_processed_game_materials()
        self.page.set_character_name_resolver(
            CharacterNameResolver.from_output_root(self.service.preview_dir.parent)
        )
        self.page.set_spine_catalog(catalog, self.service.resource_state)
        self.page.set_game_material_catalog(material_catalog, self.service.resource_state)
        self._preloaded_catalog = catalog
        self._preloaded_material_catalog = material_catalog
        self._preloaded = True
        self.page.refresh_unread_badges(self.unread_snapshot)
        return True

    def load(self, force: bool = False):
        """Load published output without re-running source discovery."""
        if self._loaded and not force:
            self.page.refresh_unread_badges(self.unread_snapshot)
            return False
        try:
            if self._preloaded and not force:
                catalog = self._preloaded_catalog
                material_catalog = self._preloaded_material_catalog
            else:
                catalog = self.service.load_published_preview_resources()
                material_catalog = self.service.discover_processed_game_materials()
                self._preloaded = False
            self.page.set_character_name_resolver(
                CharacterNameResolver.from_output_root(self.service.preview_dir.parent)
            )
            self.page.set_spine_catalog(catalog, self.service.resource_state)
            self._material_navigation_stack.clear()
            self.page.set_game_material_catalog(material_catalog, self.service.resource_state)
            self.page.refresh_unread_badges(self.unread_snapshot)
            self.status_changed.emit("正在读取已处理的图片资源…")
            self.badge_changed.emit()
            self._loaded = True
        except Exception as error:
            logger.error("已处理预览资源读取失败: %s", error, exc_info=True)
            self.status_changed.emit(f"图片资源索引读取失败: {error}")
        preview_dir = self.service.ensure_output_dir()
        if hasattr(self.page, "set_character_output_root"):
            self.page.set_character_output_root(preview_dir, self.service.resource_state)
        self.skel_map = self.service.skel_map()
        self._populate_filter()
        current_paths = self.service.image_paths()
        self._active_image_paths = list(current_paths)
        current_signatures = {
            path: self._image_signature(path)
            for path in current_paths
        }
        self.page.image_list.clear()
        pending_paths = []
        for path in current_paths:
            cached = self._thumbnail_cache.get(path)
            if cached and cached[0] == current_signatures[path]:
                self.page.image_list.addItem(build_preview_item(path, cached[1], self.skel_map))
            else:
                pending_paths.append(path)
        self.page.preview_progress.setVisible(True)
        self.page.preview_progress.setValue(0)
        self.page.empty_label.setVisible(False)
        self._cancel_image_worker()
        self._image_worker = ImageLoadWorker(str(preview_dir), 150, image_paths=pending_paths)
        self._image_worker.progress.connect(self._on_load_progress)
        self._image_worker.image_loaded.connect(self._on_thumbnail_loaded)
        self._image_worker.finished_loading.connect(self._on_load_finished)
        self._image_worker.finished.connect(self._on_image_worker_finished)
        if pending_paths:
            self._image_worker.start()
        else:
            self._image_worker = None
            self._on_load_finished([])
        return True

    @staticmethod
    def _image_signature(path):
        try:
            stat = os.stat(path)
            return stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

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
            if isinstance(data, dict):
                data["_filter_visible"] = visible
                item.setData(Qt.UserRole, data)
        self.page.refresh_thumbnail_pagination()
        self.update_status()

    def update_status(self):
        total = self.page.image_list.count()
        selected = len(self.page.image_list.selectedItems())
        self.page.preview_status.setText(f"共 {total} 张图片 · 已选 {selected}")

    def _on_load_progress(self, current, total):
        self.page.preview_progress.setMaximum(total)
        self.page.preview_progress.setValue(current)
        self.page.preview_progress.setFormat(f"加载中... {current}/{total}")
        self.progress_changed.emit(current, total, "加载预览图片")

    def _on_thumbnail_loaded(self, image_path, thumbnail):
        self._thumbnail_cache[str(image_path)] = (self._image_signature(image_path), thumbnail)
        self.page.image_list.addItem(build_preview_item(image_path, thumbnail, self.skel_map))
        self.page.refresh_thumbnail_pagination()

    def _on_load_finished(self, loaded_paths):
        self.image_paths = list(getattr(self, "_active_image_paths", loaded_paths))
        self.page.preview_progress.setVisible(False)
        self.page.empty_label.setVisible(not self.image_paths)
        self.update_status()
        message = f"图片预览: 共 {len(self.image_paths)} 张图片"
        self.badge_changed.emit()
        self.status_changed.emit(message)
        logger.info("预览图片加载完成，共 %s 张", len(self.image_paths))

    def _on_image_worker_finished(self):
        """Release the QThread only after its native run() has returned."""
        worker = self.sender()
        if worker is self._image_worker:
            self._image_worker = None
        self._retired_image_workers.discard(worker)
        if worker is not None:
            worker.deleteLater()

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
        """Discover internal Spine skins, then export the selected skin records."""
        from app.platform.tool_locator import ToolLocator

        spine_cli = ToolLocator.create().spineviewer_cli()
        if not os.path.isfile(spine_cli):
            self._notify_export_error(f"SpineViewerCLI 不存在：{spine_cli}")
            return False
        try:
            catalog = self.discover_resources()
        except Exception as error:
            self._notify_export_error(f"资源发现失败：{error}")
            return False
        records = tuple(
            record
            for record in catalog.skins.values()
            if record.status == "ready"
            and (not selected_roles or record.character_id in set(selected_roles))
        )
        return self.start_selected_export(self._group_export_records(records))

    @staticmethod
    def _group_export_records(records):
        """Group internal skin records into one visible source skin export."""
        grouped = OrderedDict()
        for record in records:
            grouped.setdefault(display_skin_key(record), []).append(record)
        return tuple(tuple(items) for items in grouped.values())

    def reload_requested(self):
        """Refresh published directories; rebuilding is an explicit separate action."""
        if self._selected_export_worker is not None:
            self.cancel_selected_export()
            return
        if self._refresh_worker is not None:
            self._on_export_selected_requested()
            return
        if self._export_worker is not None:
            self.cancel_export()
            return
        self.load(force=True)

    def start_export(self, spine_cli: str, force=False, selected_roles=None) -> bool:
        if not self.service.material_dir.is_dir():
            self._notify_export_error(f"素材目录不存在：{self.service.material_dir}")
            return False
        if not os.path.isfile(spine_cli):
            self._notify_export_error(f"SpineViewerCLI 不存在：{spine_cli}")
            return False
        if self._export_worker is not None or self._selected_export_worker is not None:
            return False
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
        self._export_worker.finished.connect(self._on_export_thread_finished)
        self._legacy_export_result = None
        self.page.set_export_busy(True)
        self._export_worker.start()
        return True

    def _on_export_progress(self, current, total):
        self.page.preview_progress.setMaximum(total)
        self.page.preview_progress.setValue(current)
        self.page.preview_progress.setFormat(f"导出中... {current}/{total}")
        self.progress_changed.emit(current, total, "导出预览图片")

    def _on_export_finished(self, success, summary):
        self._legacy_export_result = (success, summary)

    def _on_export_error(self, message):
        self._legacy_export_result = (False, str(message))

    def _notify_export_error(self, message):
        self.error.emit(str(message))
        QMessageBox.warning(self.page, "错误", f"预览导出失败:\n{message}")

    def cancel_export(self):
        self.cancel_selected_export()
        if self._export_worker is not None:
            self._export_worker.cancel()
            self.page.set_export_busy(True, cancelling=True)

    def _on_export_thread_finished(self):
        worker = self._export_worker
        self._export_worker = None
        if worker is not None:
            worker.deleteLater()
        self.page.preview_progress.setVisible(False)
        self.page.set_export_busy(False)
        success, summary = self._legacy_export_result or (False, '导出线程未返回结果')
        self.export_finished.emit(success, summary)
        self.status_changed.emit(summary)
        if not self._closing and self.page.isVisible():
            self.load(force=True)

    def _default_skin_runner(self, spine_cli):
        def run_job(job):
            from dataclasses import replace
            try:
                scene_job = hasattr(job, "scene") and hasattr(job, "sources")
                scratch = self.service.material_dir.parent / 'cache' / 'portrait-export'
                scratch.mkdir(parents=True, exist_ok=True)
                temporary_context = tempfile.TemporaryDirectory(prefix="xl-portrait-scene-", dir=scratch)
                with temporary_context as temporary_dir:
                    execution_job = (
                        prepare_portrait_scene_export_job(job, temporary_dir)
                        if scene_job and job.sprite_sources else job
                    )
                    if not scene_job:
                        execution_job = replace(job, output_path=Path(temporary_dir) / job.output_path.name)
                    command = build_spine_export_command(execution_job, spine_cli, temporary_dir)
                    worker = QThread.currentThread()
                    def cancelled():
                        return bool(getattr(worker, '_cancelled', False))
                    rendered_output = Path(json.loads(Path(command[-1]).read_text(encoding='utf-8'))['output']) if scene_job else execution_job.output_path
                    if scene_job:
                        resource_label = f"scene {job.scene.character_id}/{job.scene.skin_id}"
                        animation_label = ",".join(source.animation for source in execution_job.sources)
                        skin_label = ",".join(source.skin for source in execution_job.sources)
                        input_resources = [{
                            "source_kind": source.source_kind,
                            "part_id": source.part_id,
                            "skel": str(source.skeleton_path),
                            "atlas": str(source.atlas_path),
                            "animation": source.animation,
                            "skin": source.skin,
                            "time_scale": source.time_scale,
                            "affine": list(source.affine),
                            "sequence": [{"animation": step.animation, "loop": step.loop} for step in source.sequence],
                            "fade_in": source.fade_in,
                            "unity_order": source.unity_order,
                            "skeleton_ref": source.skeleton_ref.to_dict() if source.skeleton_ref else None,
                            "atlas_refs": [reference.to_dict() for reference in source.atlas_refs],
                            "sprite_ref": source.sprite_ref.to_dict() if source.sprite_ref else None,
                            "texture_ref": source.texture_ref.to_dict() if source.texture_ref else None,
                            "source_bundle_hashes": sorted({
                                reference.bundle_hash
                                for reference in (source.skeleton_ref, *source.atlas_refs,
                                                  source.sprite_ref, source.texture_ref)
                                if reference is not None
                            }),
                        } for source in execution_job.sources]
                        if job.sprite_sources:
                            logger.info(
                                "混合场景静态图层已准备: count=%s temp=%s",
                                len(job.sprite_sources), temporary_dir,
                                extra={"event": "spine.static_layers.prepared", "details": {
                                    "layers": [{
                                        "part_id": source.part_id,
                                        "sprite_ref": source.sprite_ref.to_dict(),
                                        "texture_ref": source.texture_ref.to_dict(),
                                        "unity_order": source.unity_order,
                                        "rect": source.rect,
                                        "pivot": source.pivot,
                                        "pixels_per_unit": source.pixels_per_unit,
                                    } for source in job.sprite_sources],
                                }},
                            )
                    else:
                        resource_label = job.record.resource_family
                        animation_label = job.settings.animation
                        skin_label = ",".join(job.settings.skins)
                        input_resources = [
                            {"skel": record.source_skel, "atlas": record.atlas_path, "skin": record.skin_name, "resource_family": record.resource_family}
                            for record in getattr(job, "records", (job.record,))
                        ]
                    logger.info(
                        "SpineViewerCLI 导出开始（模式=%s，资源=%s，格式=%s，动画=%s，皮肤=%s，命令=%s）",
                        getattr(job, "export_mode", "custom"),
                        resource_label,
                        job.settings.format,
                        animation_label,
                        skin_label,
                        subprocess.list2cmdline(command),
                    )
                    result = run_external_process(
                        command,
                        tool="SpineViewerCLI-export",
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        cancel_check=cancelled,
                        cancel_input='cancel\n' if scene_job else None,
                        cancel_tree=scene_job,
                        on_line=(lambda stream, line: self._on_native_render_line(worker, resource_label, stream, line)) if scene_job else None,
                    )
                    output_exists = rendered_output.is_file()
                    output_bytes = rendered_output.stat().st_size if output_exists else 0
                    update_process_manifest(
                        result,
                        input_resources=input_resources,
                        requested_output=str(job.output_path),
                        output_verified=result.returncode == 0 and output_exists and output_bytes > 0,
                        output_bytes=output_bytes,
                        business_outcome="success" if result.returncode == 0 and output_exists and output_bytes > 0 else "failed",
                        error_code=("SPINE_OUTPUT_NOT_CREATED" if result.returncode == 0 and not output_exists else "SPINE_OUTPUT_EMPTY" if result.returncode == 0 and output_exists and output_bytes <= 0 else "SPINE_CLI_NONZERO_EXIT" if result.returncode != 0 else None),
                    )
                    if result.returncode != 0:
                        logger.error(
                            "SpineViewerCLI 导出失败（命令=%s）: %s",
                            subprocess.list2cmdline(command),
                            result.stderr.strip(),
                        )
                        return False
                    if cancelled():
                        return False
                    if not output_exists or output_bytes <= 0:
                        logger.error('SpineViewerCLI 未生成有效暂存输出: %s', rendered_output)
                        return False
                    from .scene_export_adapter import publish_scene_result
                    publish_scene_result(rendered_output, job.output_path)
                    if not job.output_path.is_file() or job.output_path.stat().st_size <= 0:
                        logger.error("SpineViewerCLI 未生成有效输出: %s", job.output_path)
                        return False
                    logger.info(
                        "SpineViewerCLI 导出完成（格式=%s，输出=%s，大小=%s）",
                        job.settings.format,
                        job.output_path,
                        job.output_path.stat().st_size,
                    )
                    return job.output_path.is_file()
            except (OSError, subprocess.SubprocessError) as error:
                logger.error("Spine 皮肤导出异常: %s", error)
                return False
            except ValueError as error:
                error_code = getattr(error, "code", "PORTRAIT_RENDER_PLAN_INVALID")
                logger.error(
                    "角色场景渲染计划不完整: %s",
                    error,
                    extra={"event": "spine.scene.render_unsupported", "error_code": error_code,
                           "details": {"message": str(error), "part_id": getattr(error, "part_id", "")}},
                )
                return False

        return run_job

    def start_selected_export(self, records=None, settings=None, runner=None, *, options=None) -> bool:
        """Export checked Spine skins through the identity-based PNG worker."""
        if self._selected_export_worker is not None or self._refresh_worker is not None or self._export_worker is not None:
            return False
        selected_from_page = records is None
        if selected_from_page:
            scenes = tuple(self.page.action_portrait_scenes())
            if scenes:
                if self.page.selected_spine_record_groups():
                    self.status_changed.emit("请分别导出组装立绘场景与原始 Spine 资源")
                    return False
                return self._start_portrait_scene_export(scenes, settings=settings, runner=runner, options=options)
            record_groups = tuple(self.page.selected_spine_record_groups())
            records = record_groups
        else:
            records = tuple(records)
        if not records:
            self.status_changed.emit("未选择任何 Spine 皮肤")
            return False

        spine_cli = None
        if runner is None:
            from app.platform.tool_locator import ToolLocator

            spine_cli = ToolLocator.create().spineviewer_cli()
            if not os.path.isfile(spine_cli):
                self._notify_export_error(f"SpineViewerCLI 不存在：{spine_cli}")
                return False

        if settings is None:
            def group_items(group):
                return tuple(group) if isinstance(group, (tuple, list)) else (group,)

            groups = tuple(group_items(group) for group in records)
            first_group = groups[0]
            first = first_group[0]
            family_summary = tuple(
                dict.fromkeys(
                    item.resource_family.casefold()
                    for group in groups
                    for item in group
                    if item.resource_family
                )
            )
            duration_source = next(
                (
                    item
                    for group in groups
                    for item in group
                    if item.resource_family.casefold() == "cardspine"
                ),
                None,
            )
            animation_names = ()
            animation_durations = {}
            if spine_cli and duration_source is not None:
                animation_durations = get_animation_metadata(
                    duration_source.source_skel, duration_source.atlas_path, spine_cli
                )
                animation_names = tuple(animation_durations) or get_animation_names(
                    duration_source.source_skel, duration_source.atlas_path, spine_cli
                )
            elif spine_cli:
                animation_durations = get_animation_metadata(
                    first.source_skel, first.atlas_path, spine_cli
                )
                animation_names = tuple(animation_durations) or get_animation_names(
                    first.source_skel, first.atlas_path, spine_cli
                )
            skin_names = (
                tuple(self.page.selected_spine_skin_names())
                if selected_from_page
                else tuple(
                    dict.fromkeys(
                        item.skin_name
                        for item in first_group
                        if item.skin_name
                    )
                )
            )
            dialog = ExportSettingsDialog(
                first.source_skel,
                first.atlas_path,
                "PNG",
                self.page,
                animation_names=animation_names,
                animation_durations=animation_durations,
                skin_names=skin_names,
                resource_family=first.resource_family,
                selected_group_count=len(groups),
                family_summary=family_summary,
            )
            if dialog.exec() != QDialog.Accepted:
                return False
            export_mode = getattr(dialog, "export_mode", lambda: "custom")()
            if export_mode == "custom":
                if len(groups) != 1:
                    self.status_changed.emit("自定义导出仅支持恰好一个可见皮肤")
                    return False
                settings = dialog.settings()
            else:
                settings = None
        if not isinstance(settings, ExportSettings):
            if settings is not None:
                settings = ExportSettings(**settings)

        name_resolver = CharacterNameResolver.from_output_root(self.service.preview_dir.parent)
        if settings is None:
            jobs = build_default_export_plan(
                records,
                self.service.preview_dir,
                name_resolver,
                animation_duration=next(
                    (
                        float(value)
                        for value in animation_durations.values()
                        if value is not None and float(value) > 0
                    ),
                    None,
                ),
            )
        else:
            jobs = build_export_plan(records, settings, self.service.preview_dir, name_resolver)
        if not jobs:
            self.status_changed.emit("没有可导出的 Spine 皮肤")
            return False
        if runner is None:
            runner = self._default_skin_runner(spine_cli)

        self._selected_export_worker = PreviewExportWorker(
            jobs,
            settings=settings,
            runner=runner,
            parent=self,
        )
        self._selected_export_worker.skin_progress.connect(self._on_selected_export_progress)
        self._selected_export_worker.render_progress.connect(self._on_selected_render_progress)
        self._selected_export_worker.summary_ready.connect(self._on_selected_export_summary)
        self._selected_export_worker.finished.connect(self._on_selected_export_thread_finished)
        self._selected_export_worker.export_finished.connect(self._on_selected_export_finished)
        self._selected_export_worker.error.connect(self._on_selected_export_error)
        self._selected_export_cancelled = False
        self._selected_export_result = None
        self._selected_preflight_failures = ()
        self._selected_not_applicable_count = 0
        self.page.btn_reload.setText("取消导出")
        self.page.btn_reload.setEnabled(True)
        self.page.set_export_busy(True)
        self.status_changed.emit(f"准备导出 {len(jobs)} 个 Spine 皮肤")
        self._selected_export_worker.start()
        return True

    def _on_selected_export_progress(self, current, total, label):
        self.page.preview_progress.setVisible(True)
        self.page.preview_progress.setMaximum(total)
        self.page.preview_progress.setValue(current)
        self.page.preview_progress.setFormat(f"导出中... {current}/{total}")
        self.status_changed.emit(f"导出中 [{current}/{total}]: {label}")
        self.progress_changed.emit(current, total, f"导出 Spine 皮肤: {label}")

    @staticmethod
    def _on_native_render_line(worker, label, stream, line):
        signal = getattr(worker, 'render_progress', None)
        if signal is None or stream != 'stdout':
            return
        try:
            event = json.loads(line)
            phases = {'load': '准备部件', 'prepare': '准备部件', 'bounds': '计算边界',
                      'export_start': '渲染帧', 'export': '渲染帧', 'render': '渲染帧',
                      'encode': '编码视频', 'validate': '校验', 'publish': '发布'}
            phase = phases.get(event.get('phase'))
            if phase:
                total = max(0, int(event.get('total', 0)))
                current = max(0, int(event.get('current', 0)))
                signal.emit(current, total, f'{label} · {phase}')
        except (ValueError, TypeError, AttributeError, OverflowError):
            return

    def _on_selected_render_progress(self, current, total, label):
        self.page.preview_progress.setRange(0, total)
        if total:
            self.page.preview_progress.setValue(current)
        self.page.preview_progress.setFormat(f'{label} · {current}/{total}' if total else label)
        self.status_changed.emit(label)
        self.progress_changed.emit(current, total, label)

    def _start_portrait_scene_export(self, scenes, *, settings=None, runner=None, options=None) -> bool:
        """Resolve checked manifests and queue one CLI job per scene artifact."""
        from dataclasses import replace
        from .portrait_package import find_portrait_package, build_packaged_portrait_jobs, load_portrait_package

        from app.platform.tool_locator import ToolLocator

        if self._selected_export_worker is not None:
            return False
        options = dict(options or {'include_interactions': True, 'include_effects': True, 'allow_basic': False})
        preflight_failures = []
        not_applicable_count = 0
        scene_indices = {}
        packages = {}
        bundles_root = self.service.material_dir.parent / "bundles"
        usable_scenes = []
        for scene in scenes:
            if portrait_scene_capabilities(scene).state == 'not_applicable':
                not_applicable_count += 1
                continue
            try:
                package = find_portrait_package(self.service.preview_dir.parent, scene)
                if package:
                    packages[(scene.version_timestamp, scene.skin_id)] = package
                    usable_scenes.append(scene)
                    continue
                version_dir = bundles_root / str(scene.version_timestamp)
                map_path = version_dir / "_map" / "assets_map.json"
                if not map_path.is_file():
                    raise ValueError(f"版本 {scene.version_timestamp} 缺少 AssetStudio 资源索引")
                if scene.version_timestamp not in scene_indices:
                    scene_indices[scene.version_timestamp] = AssetIndex.from_catalog(
                        map_path, version_dir, int(scene.version_timestamp),
                        cab_cache_path=self.service.material_dir.parent / 'cache' / 'portrait-cab' / f'{scene.version_timestamp}.json',
                    )
                usable_scenes.append(scene)
            except (OSError, ValueError, TypeError) as error:
                preflight_failures.append(f'{scene.skin_id}: {error}')
        scenes = tuple(usable_scenes)
        if not scenes:
            self.status_changed.emit(f'未适用 {not_applicable_count}；' + '；'.join(preflight_failures))
            return False

        def scene_jobs(scene, settings=None, *, planning_options=None):
            selected_options = options if planning_options is None else planning_options
            package = packages.get((scene.version_timestamp, scene.skin_id))
            if package:
                return build_packaged_portrait_jobs(
                    scene, package, self.service.preview_dir, name_resolver, settings=settings, options=selected_options,
                )
            return build_portrait_scene_export_jobs(
                scene, scene_indices[scene.version_timestamp], self.service.material_dir,
                self.service.preview_dir, name_resolver, settings=settings, options=selected_options,
            )

        spine_cli = None
        if runner is None:
            spine_cli = ToolLocator.create().spineviewer_cli()
            if not os.path.isfile(spine_cli):
                self._notify_export_error(f"SpineViewerCLI 不存在：{spine_cli}")
                return False

            helper = ToolLocator.create().portrait_scene_cli()
            if not os.path.isfile(helper):
                self._notify_export_error(f"立绘场景导出器不存在：{helper}；请运行 scripts/build-portrait-scene-renderer.py")
                return False

        name_resolver = CharacterNameResolver.from_output_root(self.service.preview_dir.parent)
        if settings is None:
            initial_jobs = None
            dialog_scene = None
            for candidate in scenes:
                try:
                    initial_capability = portrait_scene_capabilities(candidate)
                    preview_options = options
                    if not initial_capability.can_export and initial_capability.can_export_basic:
                        preview_options = {**options, 'allow_basic': True, 'include_effects': False}
                    initial_jobs = scene_jobs(candidate, planning_options=preview_options)
                    if initial_jobs:
                        dialog_scene = candidate
                        break
                except (OSError, ValueError) as error:
                    logger.warning('场景设置预检失败 skin=%s error=%s', candidate.skin_id, error)
            if initial_jobs is None or not initial_jobs:
                self.status_changed.emit('所选场景均未通过导出预检；查看场景诊断后更新工程')
                return False
            if initial_jobs[0].sources:
                first = initial_jobs[0].sources[0]
            else:
                # Sprite-only covers still have generated native projects in
                # their published package, available for dialog metadata.
                from types import SimpleNamespace
                package_path = packages.get((dialog_scene.version_timestamp, dialog_scene.skin_id))
                if package_path is None:
                    self._notify_export_error("纯 Sprite 场景需要先发布皮肤包")
                    return False
                package = load_portrait_package(package_path)
                part = next((p for p in package.manifest["parts"] if p.get("skeleton_path")), None)
                if part is None:
                    self._notify_export_error("纯 Sprite 场景缺少生成的 Spine 工程")
                    return False
                first = SimpleNamespace(skeleton_path=package_path.parent / part["skeleton_path"],
                                        atlas_path=package_path.parent / part["atlas_paths"][0])
            animation_durations = {}
            animation_names = ()
            if spine_cli:
                animation_durations = get_animation_metadata(
                    str(first.skeleton_path), str(first.atlas_path), spine_cli,
                )
                animation_names = tuple(animation_durations) or get_animation_names(
                    str(first.skeleton_path), str(first.atlas_path), spine_cli,
                )
            skin_names = tuple(dict.fromkeys(
                source.skin or initial_jobs[0].settings.skins[0]
                for source in initial_jobs[0].sources
            ))
            package_path = packages.get((dialog_scene.version_timestamp, dialog_scene.skin_id))
            if package_path:
                from .portrait_package import package_selectors
                animation_names, animation_durations, skin_names = package_selectors(
                    load_portrait_package(package_path),
                )
            family_summary = tuple(dict.fromkeys(scene_resource_family(scene) for scene in scenes))
            dialog = ExportSettingsDialog(
                str(first.skeleton_path), str(first.atlas_path), "PNG", self.page,
                animation_names=animation_names,
                animation_durations=animation_durations,
                skin_names=skin_names,
                resource_family=family_summary[0] if family_summary else "spine",
                selected_group_count=len(scenes),
                family_summary=family_summary,
                entrance=any(dict(scene.config_metadata).get('runtime_purpose') in {'entrance_only', 'entrance_stage'} for scene in scenes),
                scene_capabilities=tuple(portrait_scene_capabilities(scene) for scene in scenes),
            )
            if options.get('allow_basic') and hasattr(dialog, 'scene_options'):
                dialog.scene_options.basic_checkbox.setChecked(True)
            if dialog.exec() != QDialog.Accepted:
                return False
            options = dict(getattr(dialog, 'scene_export_options', lambda: options)())
            if getattr(dialog, "export_mode", lambda: "custom")() == "custom":
                if len(scenes) != 1:
                    self.status_changed.emit("自定义导出仅支持恰好一个立绘场景")
                    return False
                settings = dialog.settings()

        if settings is not None and not isinstance(settings, ExportSettings):
            settings = ExportSettings(**settings)
        jobs = []
        for scene in scenes:
            try:
                planned_jobs = scene_jobs(scene, settings=settings)
                jobs.extend(
                    replace(job, export_mode="custom") if settings is not None else job
                    for job in planned_jobs
                )
            except (OSError, ValueError) as error:
                preflight_failures.append(f'{scene.skin_id}: {error}')
                logger.warning('场景导出预检失败 skin=%s error=%s', scene.skin_id, error,
                               extra={'event': 'portrait_export.preflight.failed', 'error_code': 'PORTRAIT_PREFLIGHT_FAILED'})
        if not jobs:
            self.status_changed.emit('没有可导出的立绘场景；' + '；'.join(preflight_failures))
            return False
        if runner is None:
            runner = self._default_skin_runner(spine_cli)

        self._selected_export_worker = PreviewExportWorker(
            tuple(jobs), settings=settings or jobs[0].settings, runner=runner, parent=self,
        )
        self._selected_export_worker.skin_progress.connect(self._on_selected_export_progress)
        self._selected_export_worker.render_progress.connect(self._on_selected_render_progress)
        self._selected_export_worker.summary_ready.connect(self._on_selected_export_summary)
        self._selected_export_worker.finished.connect(self._on_selected_export_thread_finished)
        self._selected_export_worker.export_finished.connect(self._on_selected_export_finished)
        self._selected_export_worker.error.connect(self._on_selected_export_error)
        self._selected_export_cancelled = False
        self._selected_export_result = None
        self._selected_preflight_failures = tuple(preflight_failures)
        self._selected_not_applicable_count = not_applicable_count
        self.page.btn_reload.setText("取消导出")
        self.page.btn_reload.setEnabled(True)
        self.page.set_export_busy(True)
        self.page.preview_progress.setVisible(True)
        self.page.preview_progress.setMaximum(len(jobs))
        self.page.preview_progress.setValue(0)
        self.status_changed.emit(f"准备导出 {len(jobs)} 个立绘场景资源")
        self._selected_export_worker.start()
        return True

    def _on_selected_export_summary(self, summary):
        self.status_changed.emit(summary)

    def _on_selected_export_error(self, message):
        self.status_changed.emit(f"导出失败: {message}")
        self._selected_export_result = (False, str(message))

    def _on_selected_export_finished(self, success, summary):
        self._selected_export_result = (success, summary)

    def _on_selected_export_thread_finished(self):
        worker = self._selected_export_worker
        self._selected_export_worker = None
        if worker is not None:
            worker.deleteLater()
        success, summary = self._selected_export_result or (False, '导出线程未返回结果')
        if self._selected_not_applicable_count:
            summary += f'；未适用 {self._selected_not_applicable_count}'
        if self._selected_preflight_failures:
            success = False
            summary += f'；预检失败 {len(self._selected_preflight_failures)}：' + '；'.join(self._selected_preflight_failures)
        if self._selected_export_cancelled:
            self.status_changed.emit("导出已取消")
        else:
            self.status_changed.emit(summary if success else f"导出失败: {summary}")
        self._reset_selected_export_ui()
        self.export_finished.emit(success and not self._selected_export_cancelled, summary)
        self.badge_changed.emit()
        if success and not self._closing and self.page.isVisible():
            self.load(force=True)

    def _reset_selected_export_ui(self):
        self.page.btn_reload.setText("重新加载图片")
        self.page.btn_reload.setEnabled(True)
        self.page.set_export_busy(False)
        self.page.preview_progress.setVisible(False)
        self.page.refresh_thumbnail_pagination()

    def cancel_selected_export(self):
        worker = self._selected_export_worker
        if worker is None:
            return
        worker.cancel()
        self._selected_export_cancelled = True
        self.page.set_export_busy(True, cancelling=True)
        self.page.btn_reload.setText('正在取消…')
        self.page.btn_reload.setEnabled(False)
        self.status_changed.emit("正在取消导出，等待临时文件清理…")

    def cancel(self):
        self.cancel_export()
        self._cancel_image_worker()

    def close(self):
        """Stop every preview worker before the Qt page is destroyed."""
        self._closing = True
        self.cancel_export()
        self._cancel_image_worker()
        self.cancel_postprocess()
        if self._refresh_worker is not None:
            self._refresh_worker.cancel()
        for name in ('character_browser', 'material_browser'):
            browser = getattr(self.page, name, None)
            if browser is not None and hasattr(browser, 'stop_thumbnail_loading'):
                browser.stop_thumbnail_loading()
        return not self.has_running_tasks

    @property
    def has_running_tasks(self):
        return bool(self._selected_export_worker is not None or self._export_worker is not None
                    or self._postprocess_worker is not None or self._refresh_worker is not None
                    or self._image_worker is not None or self._retired_image_workers
                    or any(getattr(getattr(self.page, name, None), 'has_running_thumbnail_tasks', False)
                           for name in ('character_browser', 'material_browser')))

    def _cancel_image_worker(self):
        if self._image_worker is not None:
            worker = self._image_worker
            worker.cancel()
            for name, handler in (('progress', self._on_load_progress),
                                  ('image_loaded', self._on_thumbnail_loaded),
                                  ('finished_loading', self._on_load_finished)):
                signal = getattr(worker, name, None)
                if signal is not None and hasattr(signal, 'disconnect'):
                    signal.disconnect(handler)
            if hasattr(worker, 'isRunning') and worker.isRunning():
                self._retired_image_workers.add(worker)
            elif hasattr(worker, 'deleteLater'):
                worker.deleteLater()
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
        """显示预览条目文件操作菜单。导出统一从专用导出入口进入。"""
        item_at_pos = self.page.image_list.itemAt(position)
        if item_at_pos and not item_at_pos.isSelected():
            self.page.image_list.clearSelection()
            item_at_pos.setSelected(True)

        selected_items = self.page.image_list.selectedItems()
        if not selected_items:
            return

        paths = []
        for item in selected_items:
            data = item.data(Qt.UserRole)
            if not data:
                continue
            png_path = data.get("png", "")
            if not png_path or not os.path.exists(png_path):
                continue
            paths.append(png_path)

        if not paths:
            return

        menu = QMenu(self.page)
        menu.setObjectName("contextMenu")
        act_open = menu.addAction("打开文件所在目录")
        act_copy = menu.addAction("复制文件")

        action = menu.exec(self.page.image_list.mapToGlobal(position))
        if action == act_open:
            self.open_file_location(paths[0])
        elif action == act_copy:
            self.copy_file_to_clipboard(paths[0])

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
        from .dialogs.image_viewer import ImageViewerDialog

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
