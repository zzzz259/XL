# -*- coding: utf-8 -*-
"""图片预览导出工作线程（.skel → PNG，含配对合成 + 皮肤导出 + FGUI 图集切割）"""

import os
import shutil
import json
import re
import subprocess
import sys
import time
import math
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QThread, Signal, QMimeData, QUrl
from PySide6.QtGui import QPixmap

from app.core.logger import logger
from app.ui.theme import *
from app.core.path_utils import DATA_DIR, get_base_dir, get_tools_dir

from app.ui.features.fgui_atlas import UIPackageTool
from app.ui.adapters.spine_adapter import (
    find_paired_files,
    composite_images,
    get_animation_names,
    extract_motion_names,
    export_animation_frames,
    export_skel_skins,
    run_spine_export,
)


class PreviewExportWorker(QThread):
    """图片预览导出工作线程（.skel → PNG，含配对合成 + 皮肤导出）

    在后台线程执行 SpineViewerCLI 导出，避免阻塞 UI。
    支持去重：force=False 时跳过已存在的 PNG。
    """
    progress = Signal(int, int)            # current, total
    export_finished = Signal(bool, str)    # success, summary
    error = Signal(str)

    def __init__(self, material_dir, output_dir, spine_cli, force=False, parent=None):
        super().__init__(parent)
        self.material_dir = material_dir
        self.output_dir = output_dir
        self.spine_cli = spine_cli
        self.force = force
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            success = self._do_export()
        except Exception as e:
            logger.error(f"预览导出线程异常: {e}", exc_info=True)
            self.error.emit(str(e))

    def _do_export(self):
        """执行完整的 .skel 导出流程，返回 (success, summary)"""
        # 扫描 .skel 文件
        skel_files = []
        for root, dirs, files in os.walk(self.material_dir):
            for f in files:
                if f.endswith(".skel"):
                    skel_files.append(os.path.join(root, f))

        if not skel_files:
            logger.warning("未找到 .skel 文件")
            self.error.emit("未找到 .skel 文件")
            return False

        os.makedirs(self.output_dir, exist_ok=True)

        # 识别配对
        pairs, unpaired = find_paired_files(skel_files)
        logger.info(f"找到 {len(skel_files)} 个 .skel 文件，其中配对 {len(pairs)} 组，未配对 {len(unpaired)} 个")

        success_count = 0
        fail_count = 0
        skipped_count = 0
        composite_count = 0
        total = len(skel_files)
        processed = 0

        # 处理每个 .skel 文件
        for skel_path in skel_files:
            if self._cancelled:
                logger.info("预览导出已取消")
                break

            skel_name = os.path.basename(skel_path)
            base_name = os.path.splitext(skel_name)[0]
            skel_dir = os.path.dirname(skel_path)
            atlas_path = os.path.join(skel_dir, f"{base_name}.atlas")

            processed += 1
            self.progress.emit(processed, total)

            if not os.path.exists(atlas_path):
                logger.warning(f"跳过 {skel_name}: 缺少对应的 .atlas 文件")
                skipped_count += 1
                continue

            # 去重检查：force=False 时跳过已存在且非空的 PNG
            main_output = os.path.join(self.output_dir, f"{base_name}.png")
            if not self.force and os.path.exists(main_output) and os.path.getsize(main_output) > 0:
                logger.info(f"跳过已存在的 PNG: {base_name}.png")
                skipped_count += 1
                continue

            logger.info(f"查找 atlas: {skel_path} -> {atlas_path}")

            # 获取动画列表
            animations = get_animation_names(skel_path, atlas_path, self.spine_cli)
            if not animations:
                animations = ["idle"]

            # 导出 idle 动画作为主图
            export_ok = export_animation_frames(
                skel_path, atlas_path, self.spine_cli, self.output_dir, base_name, animations
            )
            if export_ok:
                success_count += 1
            else:
                fail_count += 1

            # 导出皮肤图片（各表情独立图片）
            skin_names = extract_motion_names(skel_path)
            if skin_names:
                logger.info(f"开始导出皮肤图片: {base_name} ({len(skin_names)} 个皮肤)")
                skin_count = export_skel_skins(
                    skel_path, atlas_path, self.spine_cli, self.output_dir, base_name, skin_names
                )
                logger.info(f"皮肤导出完成: {base_name} (成功 {skin_count}/{len(skin_names)})")

        # 处理配对合成
        for role_skel, bg_skel in pairs:
            if self._cancelled:
                break

            role_name = os.path.splitext(os.path.basename(role_skel))[0]
            bg_name = os.path.splitext(os.path.basename(bg_skel))[0]

            role_png = os.path.join(self.output_dir, f"{role_name}.png")
            bg_png = os.path.join(self.output_dir, f"{bg_name}.png")

            # 检查两张图片是否存在
            if not os.path.exists(role_png):
                logger.warning(f"跳过合成 {role_name}: 角色图不存在")
                skipped_count += 1
                continue
            if not os.path.exists(bg_png):
                logger.warning(f"跳过合成 {role_name}: 背景图不存在")
                skipped_count += 1
                continue

            # 合成（去重：force=False 时跳过已存在的合成图）
            composite_path = os.path.join(self.output_dir, f"{role_name}_composite.png")
            if not self.force and os.path.exists(composite_path) and os.path.getsize(composite_path) > 0:
                logger.info(f"跳过已存在的合成图: {role_name}_composite.png")
                continue

            if composite_images(role_png, bg_png, composite_path):
                composite_count += 1
                logger.info(f"合成完成: {composite_path}")
            else:
                fail_count += 1

        summary = (
            f"共找到 {len(skel_files)} 个 .skel 文件\n"
            f"成功导出: {success_count} 个\n"
            f"合成完成: {composite_count} 张\n"
            f"跳过: {skipped_count} 个\n"
            f"失败: {fail_count} 个\n\n"
            f"输出目录:\n{self.output_dir}"
        )
        logger.info(f"预览图片完成: 成功 {success_count}, 合成 {composite_count}, 跳过 {skipped_count}, 失败 {fail_count}")

        # 处理 FGUI 图集切割
        self._export_fgui_atlas()

        self.export_finished.emit(success_count > 0, summary)
        return success_count > 0

    def _export_fgui_atlas(self):
        """
        检查并处理 FGUI 图集：将 .bank 重命名为 .bytes，调用 UIPackageTool 切割图集，
        并将切出的 PNG 移动到 output/character/ 根目录。
        """
        # 1. 构建目标文件路径
        fgui_dir = os.path.join(DATA_DIR, "material", "assets", "fairygui", "ui")
        bank_path = os.path.join(fgui_dir, "CardHeadBanner_fui.bank")
        bytes_path = os.path.join(fgui_dir, "CardHeadBanner_fui.bytes")
        target_bytes = None

        # 2. 检查 .bank 是否存在，存在则重命名为 .bytes
        if os.path.exists(bank_path):
            try:
                os.rename(bank_path, bytes_path)
                logger.info(f"已重命名 .bank 为 .bytes: {bytes_path}")
                target_bytes = bytes_path
            except Exception as e:
                logger.error(f"重命名 .bank 失败: {e}")
                return
        elif os.path.exists(bytes_path):
            target_bytes = bytes_path
            logger.info(f"找到已存在的 .bytes 文件: {bytes_path}")
        else:
            logger.info("未找到 CardHeadBanner_fui.bank 或 .bytes，跳过 FGUI 切割")
            return

        # 3. 检查配套的图集图片是否存在（png，模糊匹配）
        try:
            atlas_pattern = list(Path(fgui_dir).glob("CardHeadBanner*.png"))
        except Exception as e:
            logger.error(f"扫描图集图片失败: {e}")
            return
        if not atlas_pattern:
            logger.warning(f"未找到 CardHeadBanner*.png 图集图片，跳过切割")
            return

        # 4. 调用 UIPackageTool 切割图集，输出到 output/character/
        try:
            UIPackageTool.split_atlas(target_bytes, self.output_dir, is_override_exists=False)
            logger.info(f"FGUI 图集切割完成，输出目录: {self.output_dir}")

            # 5. 移动子文件夹中的 PNG 到 output/character/ 根目录，并清理临时子文件夹和 JSON
            base_name = os.path.splitext(os.path.basename(target_bytes))[0]
            sub_dir = os.path.join(self.output_dir, base_name)
            if os.path.isdir(sub_dir):
                for fname in os.listdir(sub_dir):
                    if fname.lower().endswith(".png"):
                        src = os.path.join(sub_dir, fname)
                        dst = os.path.join(self.output_dir, fname)
                        shutil.move(src, dst)
                        logger.debug(f"移动文件: {fname} -> {dst}")
                shutil.rmtree(sub_dir)
                logger.info(f"已清理临时子目录: {sub_dir}")

            # 6. 删除生成的 JSON 信息文件
            json_file = os.path.join(self.output_dir, f"{base_name}_cut_info.json")
            if os.path.exists(json_file):
                os.unlink(json_file)
                logger.debug(f"已删除 JSON 信息文件: {json_file}")

        except Exception as e:
            logger.error(f"FGUI 图集切割或文件整理失败: {e}")