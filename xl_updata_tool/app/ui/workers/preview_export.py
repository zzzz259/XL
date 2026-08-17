# -*- coding: utf-8 -*-
"""图片预览导出工作线程（.skel → PNG，含配对合成 + 皮肤导出 + FGUI 图集切割）"""

import os

from PySide6.QtCore import QThread, Signal

from app.core.logger import logger, timed
from app.core.path_utils import DATA_DIR, get_base_dir

from app.ui.features.fgui_atlas import UIPackageTool
from app.ui.adapters.spine_adapter import (
    find_paired_files,
    composite_images,
    composite_with_offset,
    get_animation_names,
    extract_motion_names,
    export_animation_frames,
    export_skel_skins,
    extract_character_id,
)
from app.core.prefab_parser import parse_prefab, compute_pixel_offset, build_cardspine_bundle_map


class PreviewExportWorker(QThread):
    """图片预览导出工作线程（.skel → PNG，含配对合成 + 皮肤导出）

    在后台线程执行 SpineViewerCLI 导出，避免阻塞 UI。
    支持去重：force=False 时跳过已存在的 PNG。
    """
    progress = Signal(int, int)            # current, total
    export_finished = Signal(bool, str)    # success, summary
    error = Signal(str)

    def __init__(self, material_dir, output_dir, spine_cli, force=False, selected_roles=None, parent=None):
        super().__init__(parent)
        self.material_dir = material_dir
        self.output_dir = output_dir
        self.spine_cli = spine_cli
        self.force = force
        self.selected_roles = selected_roles
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self._do_export()
        except Exception as e:
            logger.error(f"预览导出线程异常: {e}", exc_info=True)
            self.error.emit(str(e))

    def _find_assets_map(self):
        """扫描 data/bundles/*/_map/assets_map.json，返回最新一个的路径（用于角色→bundle 映射）"""
        bundles_dir = os.path.join(DATA_DIR, "bundles")
        if not os.path.isdir(bundles_dir):
            return None
        for ts in sorted(os.listdir(bundles_dir), reverse=True):
            map_path = os.path.join(bundles_dir, ts, "_map", "assets_map.json")
            if os.path.isfile(map_path):
                return map_path
        return None

    @timed("图片导出")
    def _do_export(self):
        """执行完整的 .skel 导出流程，返回 (success, summary)"""
        logger.info(f"图片预览导出开始：material={self.material_dir}, output={self.output_dir}, force={self.force}")
        # 扫描 .skel 文件
        skel_files = []
        for root, dirs, files in os.walk(self.material_dir):
            for f in files:
                if f.endswith(".skel"):
                    skel_files.append(os.path.join(root, f))

        # 选中角色过滤：只导出勾选角色（含对应的 _bg 背景）
        if self.selected_roles:
            def _kept(p):
                base = os.path.splitext(os.path.basename(p))[0]
                role = base[:-3] if base.endswith("_bg") else base
                return role in self.selected_roles
            skel_files = [p for p in skel_files if _kept(p)]

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
            # 按角色编号分目录
            char_id = extract_character_id(base_name)
            char_subdir = os.path.join(self.output_dir, char_id)

            processed += 1
            self.progress.emit(processed, total)

            if not os.path.exists(atlas_path):
                logger.warning(f"跳过 {skel_name}: 缺少对应的 .atlas 文件")
                skipped_count += 1
                continue

            # 去重检查：force=False 时跳过已存在且非空的 PNG
            main_output = os.path.join(char_subdir, f"{base_name}.png")
            if not self.force and os.path.exists(main_output) and os.path.getsize(main_output) > 0:
                logger.info(f"跳过已存在的 PNG: {base_name}.png")
                skipped_count += 1
                continue

            os.makedirs(char_subdir, exist_ok=True)
            logger.info(f"查找 atlas: {skel_path} -> {atlas_path}")

            # 获取动画列表
            animations = get_animation_names(skel_path, atlas_path, self.spine_cli)
            if not animations:
                animations = ["idle"]

            # 导出 idle 动画作为主图
            export_ok = export_animation_frames(
                skel_path, atlas_path, self.spine_cli, char_subdir, base_name, animations
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
                    skel_path, atlas_path, self.spine_cli, char_subdir, base_name, skin_names
                )
                logger.info(f"皮肤导出完成: {base_name} (成功 {skin_count}/{len(skin_names)})")

        # 处理配对合成（UnityPy 提取背景偏移 → 按偏移合成，无偏移则回退旧合成）
        bundle_map = build_cardspine_bundle_map(self._find_assets_map())
        _offset_cache = {}  # bundle 路径 -> 像素偏移（空间换时间）

        for role_skel, bg_skel in pairs:
            if self._cancelled:
                break

            role_name = os.path.splitext(os.path.basename(role_skel))[0]
            bg_name = os.path.splitext(os.path.basename(bg_skel))[0]
            role_subdir = os.path.join(self.output_dir, extract_character_id(role_name))

            role_png = os.path.join(role_subdir, f"{role_name}.png")
            bg_png = os.path.join(role_subdir, f"{bg_name}.png")

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
            composite_path = os.path.join(role_subdir, f"{role_name}_composite.png")
            if not self.force and os.path.exists(composite_path) and os.path.getsize(composite_path) > 0:
                logger.info(f"跳过已存在的合成图: {role_name}_composite.png")
                continue

            # 提取背景偏移（有 bundle 映射时才做，结果缓存）
            offset = None
            bundle_path = bundle_map.get(role_name)
            if bundle_path and os.path.isfile(bundle_path):
                if bundle_path not in _offset_cache:
                    _offset_cache[bundle_path] = compute_pixel_offset(parse_prefab(bundle_path))
                off = _offset_cache[bundle_path]
                if off:
                    # Unity (x,y) → Pillow (px,py)，Y 轴翻转
                    offset = (off['pixel_offset'][0], -off['pixel_offset'][1])

            if offset is not None:
                ok = composite_with_offset(role_png, bg_png, offset, composite_path)
            else:
                ok = composite_images(role_png, bg_png, composite_path)

            if ok:
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
        """处理所有 FGUI 图集：将 *_fui.bank 重命名为 *_fui.bytes，逐个切割到 output/fgui/<包名>/"""
        fgui_dir = os.path.join(DATA_DIR, "material", "assets", "fairygui", "ui")
        if not os.path.isdir(fgui_dir):
            logger.info("FGUI 目录不存在，跳过切割")
            return

        # 先重命名 .bank → .bytes
        for fname in os.listdir(fgui_dir):
            if fname.endswith("_fui.bank"):
                try:
                    os.rename(
                        os.path.join(fgui_dir, fname),
                        os.path.join(fgui_dir, fname[:-5] + ".bytes"),
                    )
                    logger.info(f"已重命名 .bank 为 .bytes: {fname}")
                except Exception as e:
                    logger.error(f"重命名 .bank 失败: {fname}: {e}")

        # 逐个包切割
        bytes_files = sorted(f for f in os.listdir(fgui_dir) if f.endswith("_fui.bytes"))
        if not bytes_files:
            logger.info("未找到 *_fui.bytes 文件，跳过 FGUI 切割")
            return

        fgui_output_dir = os.path.join(get_base_dir(), "output", "fgui")
        os.makedirs(fgui_output_dir, exist_ok=True)

        for fname in bytes_files:
            byte_path = os.path.join(fgui_dir, fname)
            try:
                UIPackageTool.split_atlas(byte_path, fgui_output_dir, is_override_exists=False)
                logger.info(f"FGUI 图集切割完成: {fname}")
            except Exception as e:
                logger.error(f"FGUI 图集切割失败 {fname}: {e}")
