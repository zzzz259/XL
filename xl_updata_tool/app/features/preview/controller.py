"""Preview 功能域控制器。"""

from __future__ import annotations

import os
import subprocess
from collections import OrderedDict

from PySide6.QtCore import QPoint, QObject, Qt, QMimeData, QUrl, Signal
from PySide6.QtWidgets import QApplication, QDialog, QListWidgetItem, QMenu, QMessageBox

from app.platform.diagnostics import logger

from .item import build_preview_item
from .page import PreviewPage
from .service import PreviewService
from .worker import ImageLoadWorker, PreviewExportWorker, PreviewPostprocessWorker
from .dialogs.export_settings import ExportSettingsDialog
from .export_plan import ExportSettings, build_default_export_plan, build_export_plan
from .character_names import CharacterNameResolver
from .spine_adapter import build_spine_export_command, get_animation_metadata, get_animation_names
from .resource_model import display_skin_key
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
        self._export_worker = None
        self._selected_export_worker = None
        self._selected_export_cancelled = False
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

    def start_postprocess(self, force: bool = False, shared_dialog=None) -> bool:
        """Run image discovery/material publishing after AS import."""
        del force  # The publisher is content-addressed and safe to repeat.
        worker = self._postprocess_worker
        if worker is not None and worker.isRunning():
            self.status_changed.emit("图片资源预处理已在进行中")
            return False
        self.status_changed.emit("正在处理图片资源：发现 Spine、切割图集…")
        worker = PreviewPostprocessWorker(self.service, self)
        worker.progress_value.connect(self._on_postprocess_progress)
        worker.detail_progress.connect(self._on_postprocess_detail_progress)
        worker.finished_processing.connect(self._on_postprocess_finished)
        worker.cancelled_processing.connect(self._on_postprocess_cancelled)
        worker.error.connect(self._on_postprocess_error)
        self._postprocess_worker = worker
        self._postprocess_shared = shared_dialog is not None
        self._postprocess_dialog = shared_dialog
        if shared_dialog is not None:
            shared_dialog.setLabelText("正在处理图片资源…\n发现 Spine、切割图集和大头照")
            shared_dialog.setRange(0, 4)
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
        if not worker.wait(30000):
            logger.error("图片资源预处理线程未能在取消超时内退出")
            return False
        self._postprocess_worker = None
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
        was_shared = self._postprocess_shared
        self._postprocess_shared = False
        self._postprocess_dialog = None
        self._finish_postprocess_worker()
        self._loaded = False
        self._preloaded = False
        material_summary = summary.materials
        self.status_changed.emit(
            f"图片资源预处理完成：Spine {summary.spine.published} 组，"
            f"游戏素材成功 {material_summary.exported}，失败 {material_summary.failed}"
        )
        self.badge_changed.emit()
        self.processing_finished.emit(was_shared)

    def _on_postprocess_cancelled(self):
        was_shared = self._postprocess_shared
        self._postprocess_shared = False
        self._postprocess_dialog = None
        self._finish_postprocess_worker()
        self.status_changed.emit("图片资源预处理已取消，已完成的文件已保留")
        if was_shared:
            self.processing_cancelled.emit(was_shared)

    def _on_postprocess_error(self, message):
        was_shared = self._postprocess_shared
        self._postprocess_shared = False
        self._postprocess_dialog = None
        self._finish_postprocess_worker()
        logger.error("图片资源预处理失败: %s", message)
        self.status_changed.emit("图片资源预处理失败")
        self.processing_error.emit(message, was_shared)

    def _connect_page(self):
        self.page.filter_changed.connect(self.apply_filter)
        self.page.reload_requested.connect(self.reload_requested)
        self.page.context_menu_requested.connect(self.show_context_menu)
        self.page.item_double_clicked.connect(self.open_item)
        self.page.selection_changed.connect(self.update_status)
        if hasattr(self.page, "export_selected_requested"):
            self.page.export_selected_requested.connect(self._on_export_selected_requested)
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
        if self._selected_export_worker is not None:
            self.cancel_selected_export()
            return
        self.start_selected_export()

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
            self.status_changed.emit(f"资源发现完成：发现 {len(catalog.skins)} 个皮肤")
        self.badge_changed.emit()
        return catalog

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
        """重新选择角色并导出预览图片。"""
        if self._selected_export_worker is not None:
            self.cancel_selected_export()
            return
        from .dialogs.character_select import CharacterSelectDialog

        try:
            catalog = self.discover_resources()
        except Exception as error:
            self._notify_export_error(f"资源发现失败：{error}")
            return
        roles = sorted(
            role_id for role_id, records in catalog.characters.items()
            if role_id and any(record.status == "ready" for record in records)
        )
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
        records = tuple(
            record
            for record in catalog.skins.values()
            if record.status == "ready" and record.character_id in selected
        )
        self.start_selected_export(self._group_export_records(records))

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
            self.load(force=True)

    def _on_export_error(self, message):
        self._export_worker = None
        self.page.preview_progress.setVisible(False)
        self._notify_export_error(str(message))

    def _notify_export_error(self, message):
        self.error.emit(str(message))
        QMessageBox.warning(self.page, "错误", f"预览导出失败:\n{message}")

    def cancel_export(self):
        self.cancel_selected_export()
        if self._export_worker is not None:
            self._export_worker.cancel()
            self._export_worker.wait(2000)
            self._export_worker = None

    def _default_skin_runner(self, spine_cli):
        def run_job(job):
            try:
                command = build_spine_export_command(job, spine_cli)
                logger.info(
                    "SpineViewerCLI 导出开始（模式=%s，资源=%s，格式=%s，动画=%s，皮肤=%s，命令=%s）",
                    getattr(job, "export_mode", "custom"),
                    job.record.resource_family,
                    job.settings.format,
                    job.settings.animation,
                    ",".join(job.settings.skins),
                    subprocess.list2cmdline(command),
                )
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode != 0:
                    logger.error(
                        "SpineViewerCLI 导出失败（命令=%s）: %s",
                        subprocess.list2cmdline(command),
                        result.stderr.strip(),
                    )
                    return False
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

        return run_job

    def start_selected_export(self, records=None, settings=None, runner=None) -> bool:
        """Export checked Spine skins through the identity-based PNG worker."""
        if self._selected_export_worker is not None:
            return False
        selected_from_page = records is None
        if selected_from_page:
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
        self._selected_export_worker.finished.connect(self._on_selected_export_summary)
        self._selected_export_worker.export_finished.connect(self._on_selected_export_finished)
        self._selected_export_worker.error.connect(self._on_selected_export_error)
        self._selected_export_cancelled = False
        self.page.btn_reload.setText("取消导出")
        self.page.btn_reload.setEnabled(True)
        if hasattr(self.page, "btn_export_selected"):
            self.page.btn_export_selected.setText("取消导出")
            self.page.btn_export_selected.setEnabled(True)
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

    def _on_selected_export_summary(self, summary):
        self.status_changed.emit(summary)

    def _on_selected_export_error(self, message):
        self.status_changed.emit(f"导出失败: {message}")
        self._selected_export_worker = None
        self._reset_selected_export_ui()

    def _on_selected_export_finished(self, success, summary):
        if self._selected_export_cancelled:
            self.status_changed.emit("导出已取消")
        else:
            self.status_changed.emit(summary if success else f"导出失败: {summary}")
        self._selected_export_worker = None
        self._reset_selected_export_ui()
        self.badge_changed.emit()
        if success and self.page.isVisible():
            self.load(force=True)

    def _reset_selected_export_ui(self):
        self.page.btn_reload.setText("重新加载图片")
        self.page.btn_reload.setEnabled(True)
        if hasattr(self.page, "btn_export_selected"):
            self.page.btn_export_selected.setText("导出选中 Spine")
            self.page.btn_export_selected.setEnabled(True)
        self.page.preview_progress.setVisible(False)
        self.page.refresh_thumbnail_pagination()

    def cancel_selected_export(self):
        worker = self._selected_export_worker
        if worker is None:
            return
        worker.cancel()
        worker.wait(5000)
        self._selected_export_cancelled = True
        self._selected_export_worker = None
        self._reset_selected_export_ui()
        self.status_changed.emit("导出已取消")

    def cancel(self):
        self.cancel_export()
        self._cancel_image_worker()

    def close(self):
        """Stop every preview worker before the Qt page is destroyed."""
        self.cancel_export()
        self._cancel_image_worker()
        self.cancel_postprocess()
        material_browser = getattr(self.page, "material_browser", None)
        if material_browser is not None and hasattr(material_browser, "stop_thumbnail_loading"):
            material_browser.stop_thumbnail_loading()

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
