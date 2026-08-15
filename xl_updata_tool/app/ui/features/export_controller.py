# -*- coding: utf-8 -*-
"""模块职责：导出流程控制

将单文件导出、批量导出、合成图视频导出等流程从 MainWindow 中拆出为模块级函数。
所有函数以 parent（MainWindow 实例）作为第一个参数，从而访问 status_bar、_skel_map、
btn_reload 以及后台线程槽函数等依赖。
"""

import os
import sys
import subprocess
from functools import partial

from PySide6.QtWidgets import QMessageBox, QDialog, QApplication

from app.core.logger import logger
from app.core.path_utils import get_tools_dir, get_base_dir
from app.ui.dialogs.export_settings import ExportSettingsDialog
from app.ui.workers.composite_export import CompositeExportWorker
from app.ui.workers.batch_export import BatchExportWorker
from app.ui.adapters.spine_adapter import (
    find_composite_sources,
    is_composite_png,
    extract_skin_name_from_png,
)


def export_composite_video(parent, png_path, default_format="MP4", skin_name=None):
    """导出合成图视频（单文件，弹窗选择参数）"""
    role_skel, role_atlas, bg_skel, bg_atlas = find_composite_sources(png_path, parent._skel_map)

    if not role_skel or not bg_skel:
        QMessageBox.warning(parent, "错误",
            "缺少角色或背景骨骼数据，无法合成视频\n"
            f"文件: {os.path.basename(png_path)}")
        return

    if not os.path.exists(role_skel):
        QMessageBox.warning(parent, "错误", f"角色 .skel 不存在: {role_skel}")
        return
    if not os.path.exists(bg_skel):
        QMessageBox.warning(parent, "错误", f"背景 .skel 不存在: {bg_skel}")
        return

    spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")

    if not os.path.exists(spine_cli):
        QMessageBox.warning(parent, "错误",
            "SpineViewerCLI.exe 未找到，请确认 tools/SpineViewer/ 目录完整")
        return

    # 获取对话框设置（合成图默认 MP4）
    dialog = ExportSettingsDialog(role_skel, role_atlas, default_format, parent)
    if dialog.exec() != QDialog.Accepted:
        return

    settings = dialog.get_settings()
    base_name = os.path.splitext(os.path.basename(png_path))[0]
    if base_name.endswith("_composite"):
        base_name = base_name[:-len("_composite")]
    logger.info(f"合成视频导出: {base_name}，格式={settings['format']}, 皮肤={skin_name or '无'}")

    parent.status_bar.showMessage(f"正在导出合成视频... {base_name}")
    QApplication.processEvents()

    # 后台线程导出（单 png，复用 CompositeExportWorker）
    parent._single_composite_worker = CompositeExportWorker(
        [png_path], settings, spine_cli, parent._skel_map, get_base_dir(), parent
    )
    parent._single_composite_worker.one_finished.connect(partial(on_single_one_finished, parent))
    parent._single_composite_worker.all_finished.connect(
        partial(on_single_composite_all_finished, parent, settings))
    parent._single_composite_worker.start()


def export_with_dialog(parent, skel_path, atlas_path, default_format="MP4", skin_name=None):
    """弹出导出设置对话框，按用户选择的参数导出"""
    # 验证文件
    if not skel_path or not os.path.exists(skel_path):
        logger.warning(f"无法导出，缺少 .skel 文件: {skel_path}")
        QMessageBox.warning(parent, "错误", "无法导出，.skel 文件不存在")
        return

    if not atlas_path or not os.path.exists(atlas_path):
        logger.warning(f"无法导出，缺少 .atlas 文件: {atlas_path}")
        QMessageBox.warning(parent, "错误", "无法导出，缺少对应的 .atlas 文件")
        return

    spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")

    if not os.path.exists(spine_cli):
        logger.error(f"SpineViewerCLI 不存在: {spine_cli}")
        QMessageBox.warning(parent, "错误",
            "SpineViewerCLI.exe 未找到，请确认 tools/SpineViewer/ 目录完整")
        return

    # 弹出设置对话框
    dialog = ExportSettingsDialog(skel_path, atlas_path, default_format, parent)
    if dialog.exec() != QDialog.Accepted:
        return

    settings = dialog.get_settings()
    fmt = settings["format"]
    skel_base = os.path.splitext(os.path.basename(skel_path))[0]

    logger.info(f"导出设置: 格式={fmt}, 时长={settings['duration']}s, 帧率={settings['fps']}fps, 缩放={settings['scale']}x, 预乘={settings['pma']}, 皮肤={skin_name or '无'}")
    parent.status_bar.showMessage(f"正在导出 {fmt.upper()}... {skel_base}")

    # 后台线程导出（单 entry，复用 BatchExportWorker）
    auto_open = settings.get("auto_open", False)
    parent._single_export_worker = BatchExportWorker(
        [(skel_path, atlas_path, skin_name)], settings, spine_cli, get_base_dir(), parent
    )
    parent._single_export_worker.progress.connect(partial(on_single_progress, parent))
    parent._single_export_worker.one_finished.connect(partial(on_single_export_one_finished, parent, auto_open))
    parent._single_export_worker.all_finished.connect(partial(on_single_export_all_finished, parent, settings))
    parent._single_export_worker.start()


def batch_export_with_dialog(parent, entries_with_png, default_format="MP4"):
    """批量导出：单次弹窗，合成图后台线程 + 普通文件后台线程"""
    if getattr(parent, "_batch_exporting", False):
        QMessageBox.warning(parent, "提示", "已有批量导出任务进行中，请等待完成")
        return
    # 分类：合成图 vs 普通文件
    regular_entries = []   # [(skel, atlas, skin_name), ...]
    composite_pngs = []    # [png_path, ...]

    for entry in entries_with_png:
        skel_path = entry[0]
        atlas_path = entry[1]
        png_path = entry[2] if len(entry) > 2 else ""

        if is_composite_png(png_path):
            composite_pngs.append(png_path)
            logger.info(f"批量导出合成图: {png_path}")
        else:
            if os.path.exists(skel_path) and atlas_path and os.path.exists(atlas_path):
                skin = extract_skin_name_from_png(png_path)
                regular_entries.append((skel_path, atlas_path, skin))
                logger.info(f"批量导出普通文件: {skel_path} (皮肤: {skin or '无'})")
            else:
                logger.warning(f"批量导出: 跳过无效文件: {skel_path}")

    total_all = len(regular_entries) + len(composite_pngs)
    if total_all == 0:
        QMessageBox.warning(parent, "错误", "没有可导出的有效文件")
        return

    spine_cli = os.path.join(get_tools_dir(), "SpineViewer", "SpineViewerCLI.exe")
    if not os.path.exists(spine_cli):
        logger.error(f"SpineViewerCLI 不存在: {spine_cli}")
        QMessageBox.warning(parent, "错误",
            "SpineViewerCLI.exe 未找到，请确认 tools/SpineViewer/ 目录完整")
        return

    # 单次弹出设置对话框（用第一个有效条目初始化）
    if regular_entries:
        first_skel, first_atlas = regular_entries[0][0], regular_entries[0][1]
    else:
        # 全是合成图：用第一个合成图的角色 skel 初始化
        role_skel, role_atlas, _, _ = find_composite_sources(composite_pngs[0], parent._skel_map)
        first_skel = role_skel or ""
        first_atlas = role_atlas or ""

    dialog = ExportSettingsDialog(first_skel, first_atlas, default_format, parent)
    if dialog.exec() != QDialog.Accepted:
        logger.info("批量导出：用户取消设置对话框")
        return

    settings = dialog.get_settings()
    auto_open = settings["auto_open"]

    # 确认对话框
    fmt_label = "MP4 视频" if settings["format"] == "mp4" else "GIF 动画"
    ret = QMessageBox.question(
        parent, "批量导出确认",
        f"即将批量导出 {total_all} 个文件为 {fmt_label}\n"
        f"（普通: {len(regular_entries)}，合成图: {len(composite_pngs)}）\n"
        f"参数: {settings['duration']}秒 / {settings['fps']}fps / {settings['scale']}x\n"
        f"\n是否继续?",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
    )
    if ret != QMessageBox.Yes:
        logger.info("批量导出：用户取消确认")
        return

    # 禁用相关按钮，防止重复操作
    parent.btn_reload.setEnabled(False)
    parent._batch_exporting = True

    # 保存批量导出上下文（用于合成图 + 普通文件结果合并）
    parent._batch_settings = settings
    parent._batch_auto_open = auto_open
    parent._batch_regular_entries = regular_entries
    parent._batch_spine_cli = spine_cli
    parent._batch_project_root = get_base_dir()
    parent._batch_comp_success = 0
    parent._batch_comp_fail = 0

    # 1. 启动合成图导出线程（如有）
    if composite_pngs:
        parent._composite_worker = CompositeExportWorker(
            composite_pngs, settings, spine_cli, parent._skel_map, get_base_dir(), parent
        )
        parent._composite_worker.progress.connect(partial(on_composite_progress, parent))
        parent._composite_worker.one_finished.connect(partial(on_batch_one_finished, parent))
        parent._composite_worker.all_finished.connect(partial(on_composite_all_finished, parent))
        parent._composite_worker.start()
    else:
        # 无合成图，直接处理普通文件
        start_regular_batch_export(parent)


def on_composite_progress(parent, current, total, filename):
    """合成图批量导出进度"""
    parent.status_bar.showMessage(f"合成图导出中 [{current}/{total}]: {filename}")


def on_composite_all_finished(parent, success_count, fail_count):
    """合成图批量导出全部完成"""
    parent._batch_comp_success = success_count
    parent._batch_comp_fail = fail_count
    logger.info(f"合成图批量导出完成: 成功 {success_count}, 失败 {fail_count}")
    # 继续处理普通文件（如有）
    start_regular_batch_export(parent)


def start_regular_batch_export(parent):
    """启动普通文件批量导出线程"""
    regular_entries = getattr(parent, "_batch_regular_entries", [])
    if regular_entries:
        spine_cli = parent._batch_spine_cli
        settings = parent._batch_settings
        parent._batch_worker = BatchExportWorker(
            regular_entries, settings, spine_cli, get_base_dir(), parent
        )
        parent._batch_worker.progress.connect(partial(on_batch_progress, parent))
        parent._batch_worker.one_finished.connect(partial(on_batch_one_finished, parent))
        parent._batch_worker.all_finished.connect(partial(on_regular_all_finished, parent))
        parent._batch_worker.start()
    else:
        # 无普通文件，直接显示合成图结果
        parent.btn_reload.setEnabled(True)
        on_batch_all_finished(
            parent, parent._batch_comp_success, parent._batch_comp_fail, parent._batch_auto_open
        )


def on_regular_all_finished(parent, success_count, fail_count):
    """普通文件批量导出全部完成，合并结果"""
    total_success = success_count + parent._batch_comp_success
    total_fail = fail_count + parent._batch_comp_fail
    parent.btn_reload.setEnabled(True)
    on_batch_all_finished(parent, total_success, total_fail, parent._batch_auto_open)


def on_batch_progress(parent, current, total, filename):
    """批量导出进度"""
    parent.status_bar.showMessage(
        f"批量导出中 [{current}/{total}]: {filename}"
    )
    QApplication.processEvents()


def on_batch_one_finished(parent, path, success):
    """单个文件导出完成"""
    if success:
        logger.info(f"批量导出成功: {os.path.basename(path)}")
    else:
        logger.warning(f"批量导出失败: {os.path.basename(path)}")


def on_batch_all_finished(parent, success_count, fail_count, auto_open):
    """批量导出全部完成"""
    parent.btn_reload.setEnabled(True)
    parent._batch_exporting = False
    total = success_count + fail_count
    settings = getattr(parent, "_batch_settings", {})
    fmt = "视频" if settings.get("format") == "mp4" else "GIF"
    parent.status_bar.showMessage(
        f"批量导出完成: 成功 {success_count} 个，失败 {fail_count} 个"
    )
    logger.info(f"批量导出完成：成功 {success_count}，失败 {fail_count}")

    if success_count > 0 and auto_open:
        # 打开输出目录
        output_dir = os.path.join(get_base_dir(), "output",
                                  "video" if fmt == "视频" else "character")
        if os.path.exists(output_dir):
            if sys.platform == "win32":
                os.startfile(output_dir)

    QMessageBox.information(
        parent, "批量导出完成",
        f"共处理 {total} 个文件\n"
        f"✅ 成功: {success_count} 个\n"
        f"❌ 失败: {fail_count} 个"
    )


def on_single_progress(parent, current, total, filename):
    """单文件导出进度"""
    parent.status_bar.showMessage(f"导出中 [{current}/{total}]: {filename}")


def on_single_one_finished(parent, path, success):
    """单个导出完成（合成图用，path 是输入 png）"""
    if success:
        logger.info(f"导出成功: {os.path.basename(path)}")
    else:
        logger.warning(f"导出失败: {os.path.basename(path)}")


def on_single_export_one_finished(parent, auto_open, path, success):
    """单文件导出完成：path 是输出文件，成功后处理 auto_open"""
    on_single_one_finished(parent, path, success)
    if success and auto_open:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(['xdg-open', path] if sys.platform.startswith('linux') else ['open', path])


def on_single_export_all_finished(parent, settings, success, fail):
    """单文件导出全部完成"""
    logger.info(f"单文件导出完成：成功 {success}，失败 {fail}")
    if success:
        parent.status_bar.showMessage("导出完成")
    else:
        parent.status_bar.showMessage("导出失败")
        QMessageBox.warning(parent, "导出失败", "导出失败")


def on_single_composite_all_finished(parent, settings, success, fail):
    """合成图导出全部完成"""
    logger.info(f"合成视频导出完成：成功 {success}，失败 {fail}")
    if success:
        parent.status_bar.showMessage("合成视频导出完成")
        if settings.get("auto_open"):
            fmt = settings["format"]
            output_dir = os.path.join(get_base_dir(), "output", "video" if fmt == "mp4" else "character")
            if os.path.exists(output_dir):
                if sys.platform == "win32":
                    os.startfile(output_dir)
    else:
        parent.status_bar.showMessage("导出失败")
        QMessageBox.warning(parent, "导出失败", "合成视频导出失败")