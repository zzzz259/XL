# -*- coding: utf-8 -*-
"""批量导出工作线程"""

import os
import subprocess
import sys
import time
import math
from datetime import datetime

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from app.core.logger import logger
from app.core.path_utils import get_tools_dir


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

        ext = ".mp4" if fmt == "mp4" else ".gif"
        output_dir = os.path.join(
            self.project_root,
            "output",
            "video" if fmt == "mp4" else "character"
        )
        os.makedirs(output_dir, exist_ok=True)

        for i, entry in enumerate(self.skel_atlas_list):
            if self._cancelled:
                break

            skel_path = entry[0]
            atlas_path = entry[1]
            skin_name = entry[2] if len(entry) > 2 else None

            skel_base = os.path.splitext(os.path.basename(skel_path))[0]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, f"{skel_base}_{timestamp}{ext}")

            self.progress.emit(i + 1, total, skel_base)
            QApplication.processEvents()

            try:
                cmd = [
                    self.spine_cli, "export", skel_path,
                    "-f", "Mp4" if fmt == "mp4" else "Gif",
                    "-o", output_path,
                    "-a", animation,
                    "--atlas", atlas_path,
                    "--duration", str(duration),
                    "--fps", str(fps),
                    "--scale", str(scale),
                    "--color", "#00000000",
                ]
                if pma:
                    cmd.append("--pma")
                if skin_name:
                    cmd.extend(["--skins", skin_name])
                if fmt == "gif":
                    cmd.append("--loop")

                proc = subprocess.run(
                    cmd,
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

        self.all_finished.emit(success, fail)