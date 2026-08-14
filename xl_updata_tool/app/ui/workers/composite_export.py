# -*- coding: utf-8 -*-
"""批量合成图视频导出工作线程"""

import os
import subprocess
import sys
import time
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from app.core.logger import logger
from app.core.path_utils import get_tools_dir, get_base_dir
from app.ui.adapters.spine_adapter import (
    get_ffmpeg_path,
    ffmpeg_composite_videos,
    cleanup_temp,
    extract_skin_name_from_png,
    find_composite_sources,
    export_spine_media_file,
)


class CompositeExportWorker(QThread):
    """批量合成图视频导出工作线程

    在后台线程串行处理合成图导出（角色MP4 + 背景MP4 + FFmpeg叠加），
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

        self.all_finished.emit(success, fail)

    def _export_one(self, png_path, skin_name=None):
        """导出单个合成图视频，返回 bool 表示成功与否"""
        role_skel, role_atlas, bg_skel, bg_atlas = find_composite_sources(png_path, self.skel_map)
        if not role_skel or not bg_skel:
            logger.warning(f"批量合成导出: 缺少角色或背景骨骼数据: {png_path}")
            return False

        if not os.path.exists(self.spine_cli):
            logger.error(f"SpineViewerCLI 不存在: {self.spine_cli}")
            return False

        fmt = self.settings["format"]
        animation = self.settings["animation"]
        duration = self.settings["duration"]
        fps = self.settings["fps"]
        scale = self.settings["scale"]
        pma = self.settings.get("pma", False)

        ext = ".mp4" if fmt == "mp4" else ".gif"
        base_name = os.path.splitext(os.path.basename(png_path))[0]
        if base_name.endswith("_composite"):
            base_name = base_name[:-len("_composite")]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(self.project_root, "output",
                                   "video" if fmt == "mp4" else "character")
        os.makedirs(output_dir, exist_ok=True)

        # 唯一临时目录
        temp_dir = os.path.join(self.project_root, "output", "temp",
                                f"composite_{base_name}_{datetime.now().strftime('%H%M%S_%f')}")
        os.makedirs(temp_dir, exist_ok=True)

        role_temp_path = os.path.join(temp_dir, f"role_temp{ext}")
        bg_temp_path = os.path.join(temp_dir, f"bg_temp{ext}")
        output_path = os.path.join(output_dir, f"{base_name}_composite_{timestamp}{ext}")

        logger.info(f"批量合成视频导出: {base_name}")
        logger.info(f"参数: 格式={fmt}, 时长={duration}s, 帧率={fps}fps, 缩放={scale}x, 预乘={pma}, 皮肤={skin_name or '无'}")

        try:
            # 步骤 1: 导出角色视频（应用皮肤）
            if not export_spine_media_file(
                self.spine_cli, role_skel, role_atlas, role_temp_path,
                animation, duration, fps, scale, fmt,
                label="角色", pma=pma, skin_name=skin_name
            ):
                logger.error(f"批量合成: 角色视频导出失败: {base_name}")
                return False

            # 步骤 2: 导出背景视频
            if not export_spine_media_file(
                self.spine_cli, bg_skel, bg_atlas, bg_temp_path,
                animation, duration, fps, scale, fmt,
                label="背景", pma=pma
            ):
                logger.error(f"批量合成: 背景视频导出失败: {base_name}")
                return False

            # 步骤 3: FFmpeg 叠加合成
            if not ffmpeg_composite_videos(
                bg_temp_path, role_temp_path, output_path,
                fps, fmt
            ):
                logger.error(f"批量合成: FFmpeg 叠加失败: {base_name}")
                return False

            if os.path.exists(output_path):
                size = os.path.getsize(output_path)
                logger.info(f"批量合成视频导出完成: {output_path} (大小: {size} bytes)")
                return True
            else:
                logger.error(f"批量合成: 输出文件未生成: {base_name}")
                return False

        except Exception as e:
            logger.error(f"批量合成视频导出异常 [{base_name}]: {e}")
            return False
        finally:
            time.sleep(0.5)
            cleanup_temp(temp_dir)