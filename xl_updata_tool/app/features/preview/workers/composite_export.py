# -*- coding: utf-8 -*-
"""批量合成图视频导出工作线程"""

import os
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.platform.diagnostics import logger
from app.platform.processes import run_external_process
from app.features.preview.export_plan import ExportSettings, SkinExportJob
from app.features.preview.resource_model import SpineSkinRecord
from app.features.preview.spine_adapter import (
    extract_skin_name_from_png,
    find_composite_sources,
    build_spine_export_command,
)


class CompositeExportWorker(QThread):
    """批量合成图视频导出工作线程

    在后台线程串行处理合成图导出（SpineViewerCLI 原生 merge），
    避免阻塞 UI。
    """
    progress = Signal(int, int, str)       # current, total, filename
    one_finished = Signal(str, bool)       # filepath, success
    all_finished = Signal(int, int)        # success_count, fail_count

    def __init__(self, composite_pngs, settings, spine_cli, skel_map, project_root, parent=None):
        super().__init__(parent)
        self.composite_pngs = composite_pngs
        self.settings = settings
        self.spine_cli = spine_cli
        self.skel_map = skel_map
        self.project_root = project_root
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.composite_pngs)
        success = 0
        fail = 0
        logger.info(f"批量合成导出开始：{total} 个合成图")

        for i, png_path in enumerate(self.composite_pngs):
            if self._cancelled:
                break

            base_name = os.path.basename(png_path)
            self.progress.emit(i + 1, total, base_name)

            skin_name = extract_skin_name_from_png(png_path)
            if self._export_one(png_path, skin_name):
                success += 1
                self.one_finished.emit(png_path, True)
            else:
                fail += 1
                self.one_finished.emit(png_path, False)

        logger.info(f"批量合成导出完成：成功 {success}，失败 {fail}")
        self.all_finished.emit(success, fail)

    def _export_one(self, png_path, skin_name=None):
        """用一次 SpineViewerCLI merge 导出角色和背景。"""
        role_skel, role_atlas, bg_skel, bg_atlas = find_composite_sources(png_path, self.skel_map)
        if not role_skel or not bg_skel:
            logger.warning(f"批量合成导出: 缺少角色或背景骨骼数据: {png_path}")
            return False

        if not os.path.exists(self.spine_cli):
            logger.error(f"SpineViewerCLI 不存在: {self.spine_cli}")
            return False

        fmt = str(self.settings["format"]).casefold()
        if fmt not in {"mp4", "gif"}:
            logger.error("合成图视频导出不支持格式: %s", fmt)
            return False

        ext = ".mp4" if fmt == "mp4" else ".gif"
        base_name = os.path.splitext(os.path.basename(png_path))[0]
        if base_name.endswith("_composite"):
            base_name = base_name[:-len("_composite")]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(self.project_root, "output",
                                   "video" if fmt == "mp4" else "character")
        os.makedirs(output_dir, exist_ok=True)

        output_path = os.path.join(output_dir, f"{base_name}_composite_{timestamp}{ext}")

        role_record = SpineSkinRecord(
            character_id=None,
            source_skel=role_skel,
            atlas_path=role_atlas,
            skin_name=skin_name or "default",
            attachment_fingerprint="",
            display_name=base_name,
            status="ready",
            resource_family="cardspine",
        )
        background_record = SpineSkinRecord(
            character_id=None,
            source_skel=bg_skel,
            atlas_path=bg_atlas,
            skin_name="default",
            attachment_fingerprint="",
            display_name=f"{base_name}_bg",
            status="ready",
            resource_family="cardspine",
        )
        settings = ExportSettings(
            animation=self.settings["animation"],
            static=False,
            scale=int(self.settings["scale"]),
            max_resolution=int(self.settings.get("max_resolution", 16000)),
            margin=int(self.settings.get("margin", 10)),
            transparent=bool(self.settings.get("transparent", False)),
            pma=bool(self.settings.get("pma", False)),
            format=fmt.title(),
            fps=int(self.settings["fps"]),
            time_offset=float(self.settings.get("time_offset", 0)),
            duration=float(self.settings["duration"]),
            loop=fmt == "gif",
            skins=(skin_name or "default",),
            background_color=str(self.settings.get("background_color", "#7f7f7f")),
        )
        job = SkinExportJob(
            role_record,
            Path(output_path),
            settings,
            records=(role_record, background_record),
        )

        try:
            command = build_spine_export_command(job, self.spine_cli)
            proc = run_external_process(
                command,
                tool="spine-cli",
                cwd=os.path.dirname(self.spine_cli),
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if proc.returncode != 0:
                logger.error("原生 merge 视频导出失败 [%s]: %s", base_name, proc.stderr[:500])
                return False
            if not os.path.exists(output_path):
                logger.error("原生 merge 视频未生成输出: %s", base_name)
                return False
            logger.info("原生 merge 视频导出完成: %s (大小: %s bytes)", output_path, os.path.getsize(output_path))
            return True
        except Exception as e:
            logger.error(f"原生 merge 视频导出异常 [{base_name}]: {e}")
            return False
