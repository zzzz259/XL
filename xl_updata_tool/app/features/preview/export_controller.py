# -*- coding: utf-8 -*-
"""Preview Feature 的 GIF/视频导出编排。

导出任务只依赖 PreviewController 的显式状态和 PreviewPage，不再把
MainWindow 当作隐式宿主，也不读取宿主窗口的私有字段。
"""

import os
import subprocess
import sys
from functools import partial

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.features.preview.adapter import (
    extract_skin_name_from_png,
    find_composite_sources,
    is_composite_png,
)
from app.features.preview.worker import BatchExportWorker, CompositeExportWorker
from app.platform.diagnostics import logger
from app.platform.paths import get_base_dir, get_tools_dir
from app.ui.dialogs.export_settings import ExportSettingsDialog


def _page(controller):
    return controller.page


def _status(controller, message):
    controller.status_changed.emit(message)


def export_composite_video(controller, png_path, default_format="MP4", skin_name=None):
    """导出单个合成图视频。"""
    role_skel, role_atlas, bg_skel, bg_atlas = find_composite_sources(
        png_path, controller.skel_map
    )
    if not role_skel or not bg_skel:
        QMessageBox.warning(_page(controller), "错误", "缺少角色或背景骨骼数据，无法合成视频\n"
                            f"文件: {os.path.basename(png_path)}")
        return
    if not os.path.exists(role_skel):
        QMessageBox.warning(_page(controller), "错误", f"角色 .skel 不存在: {role_skel}")
        return
    if not os.path.exists(bg_skel):
        QMessageBox.warning(_page(controller), "错误", f"背景 .skel 不存在: {bg_skel}")
        return

    spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")
    if not os.path.exists(spine_cli):
        QMessageBox.warning(_page(controller), "错误",
                            "SpineViewerCLI.exe 未找到，请确认 tools/SpineViewer/ 目录完整")
        return

    dialog = ExportSettingsDialog(role_skel, role_atlas, default_format, _page(controller))
    if dialog.exec() != QDialog.Accepted:
        return
    settings = dialog.get_settings()
    base_name = os.path.splitext(os.path.basename(png_path))[0]
    if base_name.endswith("_composite"):
        base_name = base_name[:-len("_composite")]
    logger.info("合成视频导出: %s，格式=%s, 皮肤=%s", base_name,
                settings["format"], skin_name or "无")
    _status(controller, f"正在导出合成视频... {base_name}")
    QApplication.processEvents()

    controller._single_composite_worker = CompositeExportWorker(
        [png_path], settings, spine_cli, controller.skel_map, get_base_dir(), controller
    )
    controller._single_composite_worker.one_finished.connect(
        partial(on_single_one_finished, controller)
    )
    controller._single_composite_worker.all_finished.connect(
        partial(on_single_composite_all_finished, controller, settings)
    )
    controller._single_composite_worker.start()


def export_with_dialog(controller, skel_path, atlas_path, default_format="MP4", skin_name=None):
    """弹出导出设置并导出单个普通 Spine 条目。"""
    if not skel_path or not os.path.exists(skel_path):
        logger.warning("无法导出，缺少 .skel 文件: %s", skel_path)
        QMessageBox.warning(_page(controller), "错误", "无法导出，.skel 文件不存在")
        return
    if not atlas_path or not os.path.exists(atlas_path):
        logger.warning("无法导出，缺少对应的 .atlas 文件: %s", atlas_path)
        QMessageBox.warning(_page(controller), "错误", "无法导出，缺少对应的 .atlas 文件")
        return

    spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")
    if not os.path.exists(spine_cli):
        logger.error("SpineViewerCLI 不存在: %s", spine_cli)
        QMessageBox.warning(_page(controller), "错误",
                            "SpineViewerCLI.exe 未找到，请确认 tools/SpineViewer/ 目录完整")
        return

    dialog = ExportSettingsDialog(skel_path, atlas_path, default_format, _page(controller))
    if dialog.exec() != QDialog.Accepted:
        return
    settings = dialog.get_settings()
    fmt = settings["format"]
    skel_base = os.path.splitext(os.path.basename(skel_path))[0]
    logger.info("导出设置: 格式=%s, 时长=%ss, 帧率=%sfps, 缩放=%sx, 预乘=%s, 皮肤=%s",
                fmt, settings["duration"], settings["fps"], settings["scale"],
                settings["pma"], skin_name or "无")
    _status(controller, f"正在导出 {fmt.upper()}... {skel_base}")

    auto_open = settings.get("auto_open", False)
    controller._single_export_worker = BatchExportWorker(
        [(skel_path, atlas_path, skin_name)], settings, spine_cli, get_base_dir(), controller
    )
    controller._single_export_worker.progress.connect(partial(on_single_progress, controller))
    controller._single_export_worker.one_finished.connect(
        partial(on_single_export_one_finished, controller, auto_open)
    )
    controller._single_export_worker.all_finished.connect(
        partial(on_single_export_all_finished, controller, settings)
    )
    controller._single_export_worker.start()


def batch_export_with_dialog(controller, entries_with_png, default_format="MP4"):
    """单次弹窗的批量导出，合成图和普通条目分阶段执行。"""
    if getattr(controller, "_batch_exporting", False):
        QMessageBox.warning(_page(controller), "提示", "已有批量导出任务进行中，请等待完成")
        return

    regular_entries = []
    composite_pngs = []
    for entry in entries_with_png:
        skel_path, atlas_path = entry[0], entry[1]
        png_path = entry[2] if len(entry) > 2 else ""
        if is_composite_png(png_path):
            composite_pngs.append(png_path)
            logger.info("批量导出合成图: %s", png_path)
        elif os.path.exists(skel_path) and atlas_path and os.path.exists(atlas_path):
            skin = extract_skin_name_from_png(png_path)
            regular_entries.append((skel_path, atlas_path, skin))
            logger.info("批量导出普通文件: %s (皮肤: %s)", skel_path, skin or "无")
        else:
            logger.warning("批量导出: 跳过无效文件: %s", skel_path)

    total_all = len(regular_entries) + len(composite_pngs)
    if total_all == 0:
        QMessageBox.warning(_page(controller), "错误", "没有可导出的有效文件")
        return

    spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")
    if not os.path.exists(spine_cli):
        logger.error("SpineViewerCLI 不存在: %s", spine_cli)
        QMessageBox.warning(_page(controller), "错误",
                            "SpineViewerCLI.exe 未找到，请确认 tools/SpineViewer/ 目录完整")
        return

    if regular_entries:
        first_skel, first_atlas = regular_entries[0][0], regular_entries[0][1]
    else:
        role_skel, role_atlas, _, _ = find_composite_sources(
            composite_pngs[0], controller.skel_map
        )
        first_skel, first_atlas = role_skel or "", role_atlas or ""

    dialog = ExportSettingsDialog(first_skel, first_atlas, default_format, _page(controller))
    if dialog.exec() != QDialog.Accepted:
        logger.info("批量导出：用户取消设置对话框")
        return
    settings = dialog.get_settings()
    auto_open = settings["auto_open"]
    fmt_label = "MP4 视频" if settings["format"] == "mp4" else "GIF 动画"
    ret = QMessageBox.question(
        _page(controller), "批量导出确认",
        f"即将批量导出 {total_all} 个文件为 {fmt_label}\n"
        f"（普通: {len(regular_entries)}，合成图: {len(composite_pngs)}）\n"
        f"参数: {settings['duration']}秒 / {settings['fps']}fps / {settings['scale']}x\n\n是否继续?",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
    )
    if ret != QMessageBox.Yes:
        logger.info("批量导出：用户取消确认")
        return

    controller.page.btn_reload.setEnabled(False)
    controller._batch_exporting = True
    controller._batch_settings = settings
    controller._batch_auto_open = auto_open
    controller._batch_regular_entries = regular_entries
    controller._batch_spine_cli = spine_cli
    controller._batch_comp_success = 0
    controller._batch_comp_fail = 0

    if composite_pngs:
        controller._composite_worker = CompositeExportWorker(
            composite_pngs, settings, spine_cli, controller.skel_map, get_base_dir(), controller
        )
        controller._composite_worker.progress.connect(
            partial(on_composite_progress, controller)
        )
        controller._composite_worker.one_finished.connect(
            partial(on_batch_one_finished, controller)
        )
        controller._composite_worker.all_finished.connect(
            partial(on_composite_all_finished, controller)
        )
        controller._composite_worker.start()
    else:
        start_regular_batch_export(controller)


def on_composite_progress(controller, current, total, filename):
    _status(controller, f"合成图导出中 [{current}/{total}]: {filename}")


def on_composite_all_finished(controller, success_count, fail_count):
    controller._batch_comp_success = success_count
    controller._batch_comp_fail = fail_count
    logger.info("合成图批量导出完成: 成功 %s, 失败 %s", success_count, fail_count)
    start_regular_batch_export(controller)


def start_regular_batch_export(controller):
    regular_entries = getattr(controller, "_batch_regular_entries", [])
    if regular_entries:
        controller._batch_worker = BatchExportWorker(
            regular_entries, controller._batch_settings, controller._batch_spine_cli,
            get_base_dir(), controller
        )
        controller._batch_worker.progress.connect(partial(on_batch_progress, controller))
        controller._batch_worker.one_finished.connect(partial(on_batch_one_finished, controller))
        controller._batch_worker.all_finished.connect(partial(on_regular_all_finished, controller))
        controller._batch_worker.start()
    else:
        controller.page.btn_reload.setEnabled(True)
        on_batch_all_finished(
            controller, controller._batch_comp_success, controller._batch_comp_fail,
            controller._batch_auto_open,
        )


def on_regular_all_finished(controller, success_count, fail_count):
    total_success = success_count + controller._batch_comp_success
    total_fail = fail_count + controller._batch_comp_fail
    controller.page.btn_reload.setEnabled(True)
    on_batch_all_finished(controller, total_success, total_fail, controller._batch_auto_open)


def on_batch_progress(controller, current, total, filename):
    _status(controller, f"批量导出中 [{current}/{total}]: {filename}")
    QApplication.processEvents()


def on_batch_one_finished(controller, path, success):
    logger.info("批量导出%s: %s", "成功" if success else "失败", os.path.basename(path))


def on_batch_all_finished(controller, success_count, fail_count, auto_open):
    controller.page.btn_reload.setEnabled(True)
    controller._batch_exporting = False
    total = success_count + fail_count
    settings = getattr(controller, "_batch_settings", {})
    fmt = "视频" if settings.get("format") == "mp4" else "GIF"
    _status(controller, f"批量导出完成: 成功 {success_count} 个，失败 {fail_count} 个")
    logger.info("批量导出完成：成功 %s，失败 %s", success_count, fail_count)
    if success_count > 0 and auto_open:
        output_dir = os.path.join(get_base_dir(), "output", "video" if fmt == "视频" else "character")
        if os.path.exists(output_dir) and sys.platform == "win32":
            os.startfile(output_dir)
    QMessageBox.information(
        _page(controller), "批量导出完成",
        f"共处理 {total} 个文件\n✅ 成功: {success_count} 个\n❌ 失败: {fail_count} 个",
    )


def on_single_progress(controller, current, total, filename):
    _status(controller, f"导出中 [{current}/{total}]: {filename}")


def on_single_one_finished(controller, path, success):
    logger.info("导出%s: %s", "成功" if success else "失败", os.path.basename(path))


def on_single_export_one_finished(controller, auto_open, path, success):
    on_single_one_finished(controller, path, success)
    if success and auto_open:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path] if sys.platform.startswith("linux") else ["open", path])


def on_single_export_all_finished(controller, settings, success, fail):
    logger.info("单文件导出完成：成功 %s，失败 %s", success, fail)
    if success:
        _status(controller, "导出完成")
    else:
        _status(controller, "导出失败")
        QMessageBox.warning(_page(controller), "导出失败", "导出失败")


def on_single_composite_all_finished(controller, settings, success, fail):
    logger.info("合成视频导出完成：成功 %s，失败 %s", success, fail)
    if success:
        _status(controller, "合成视频导出完成")
        if settings.get("auto_open"):
            fmt = settings["format"]
            output_dir = os.path.join(get_base_dir(), "output", "video" if fmt == "mp4" else "character")
            if os.path.exists(output_dir) and sys.platform == "win32":
                os.startfile(output_dir)
    else:
        _status(controller, "导出失败")
        QMessageBox.warning(_page(controller), "错误", "合成视频导出失败")
