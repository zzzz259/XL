# -*- coding: utf-8 -*-
"""批量导出工作线程"""

import os
import subprocess
import sys
from datetime import datetime

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from app.platform.diagnostics import logger
from app.platform.processes import run_external_process


class BatchExportWorker(QThread):
    """批量导出工作线程"""
    progress = Signal(int, int, str)   # current, total, filename
    one_finished = Signal(str, bool)   # filepath, success
    all_finished = Signal(int, int)   # success_count, fail_count

    def __init__(self, skel_atlas_list, settings, spine_cli, project_root, parent=None):
        super().__init__(parent)
        self.skel_atlas_list = skel_atlas_list  # [(skel, atlas), ...]
        self.settings = settings
        self.spine_cli = spine_cli
        self.project_root = project_root
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.skel_atlas_list)
        success = 0
        fail = 0

        fmt = self.settings["format"]
        animation = self.settings["animation"]
        duration = self.settings["duration"]
        fps = self.settings["fps"]
        scale = self.settings["scale"]
        pma = self.settings.get("pma", False)

        logger.info(f"批量导出开始：{total} 个文件，格式={fmt}, 动画={animation}, 时长={duration}s, 帧率={fps}, 缩放={scale}, 预乘={pma}")

        format_name = {"png": "Png", "mp4": "Mp4", "gif": "Gif"}.get(fmt)
        if format_name is None:
            logger.error("批量导出不支持的格式: %s", fmt)
            self.all_finished.emit(0, total)
            return
        ext = ".png" if fmt == "png" else (".mp4" if fmt == "mp4" else ".gif")
        output_dir = os.path.join(
            self.project_root,
            "output", "video" if fmt == "mp4" else "character"
        )
        os.makedirs(output_dir, exist_ok=True)

        for i, entry in enumerate(self.skel_atlas_list):
            if self._cancelled:
                break

            skel_path = entry[0]
            atlas_path = entry[1]
            skin_name = entry[2] if len(entry) > 2 else None
            configured_skins = tuple(self.settings.get("skins") or ())
            if not skin_name or str(skin_name).casefold() == "default":
                skin_name = configured_skins[0] if configured_skins else skin_name
            if (
                (not skin_name or str(skin_name).casefold() == "default")
                and str(self.settings.get("resource_family", "")).casefold() == "battlespine"
            ):
                skin_name = "motion_stander"

            skel_base = os.path.splitext(os.path.basename(skel_path))[0]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, f"{skel_base}_{timestamp}{ext}")

            self.progress.emit(i + 1, total, skel_base)
            QApplication.processEvents()

            try:
                cmd = [
                    self.spine_cli, "export", skel_path,
                    "-f", format_name,
                    "-o", output_path,
                    "--animations", animation,
                    "--atlas", atlas_path,
                    "--duration", "0" if fmt == "png" else str(duration),
                    "--fps", "1" if fmt == "png" else str(fps),
                    "--scale", str(scale),
                    "--max-resolution", str(self.settings.get("max_resolution", 16000)),
                    "--margin", str(self.settings.get("margin", 10)),
                    "--time", str(self.settings.get("time_offset", 0)),
                    "--color", (
                        "#00000000"
                        if self.settings.get("transparent", True)
                        else self.settings.get("background_color", "#7f7f7f")
                    ),
                ]
                if pma:
                    cmd.append("--pma")
                if fmt == "png" or self.settings.get("disable_track_loop", False):
                    cmd.append("--disable-track-loop")
                if skin_name and str(skin_name).casefold() != "default":
                    cmd.extend(["--skins", skin_name])
                if fmt == "gif":
                    cmd.append("--loop")

                proc = run_external_process(
                    cmd,
                    tool="spine-cli",
                    cwd=os.path.dirname(self.spine_cli),
                    capture_output=True,
                    text=True,
                    timeout=60,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )

                if proc.returncode == 0 and os.path.exists(output_path):
                    success += 1
                    self.one_finished.emit(output_path, True)
                else:
                    fail += 1
                    logger.error(f"批量导出失败 [{skel_base}]: {proc.stderr[:200]}")
                    self.one_finished.emit(skel_path, False)

            except subprocess.TimeoutExpired:
                fail += 1
                logger.error(f"批量导出超时 [{skel_base}]")
                self.one_finished.emit(skel_path, False)
            except Exception as e:
                fail += 1
                logger.error(f"批量导出异常 [{skel_base}]: {e}")
                self.one_finished.emit(skel_path, False)

        logger.info(f"批量导出完成：成功 {success}，失败 {fail}")
        self.all_finished.emit(success, fail)
