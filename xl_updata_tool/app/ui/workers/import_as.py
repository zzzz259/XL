# -*- coding: utf-8 -*-
"""导入AS后台工作线程：整合 修复→解析→导出分类 三阶段"""

import os
import json
import re
import subprocess
import shutil
import sys
import time

from PySide6.QtCore import QThread, Signal

from app.core.logger import logger
from app.core.path_utils import DATA_DIR, get_base_dir, get_tools_dir
from app.core.bundle_parser import fix_bundle_inplace


class ImportASWorker(QThread):
    """导入AS后台工作线程：整合 修复→解析→导出分类 三阶段

    直接修复原始 .bundle 文件（不复制到临时目录），
    然后调用 AssetStudio CLI 生成资源映射（assets_map.json），
    最后按类型导出所有资源到 data/material/。
    """
    progress_stage = Signal(str, int, int)  # stage_name, current, total
    stage_finished = Signal(str)            # stage_name
    all_finished = Signal(bool, str)        # success, message

    def __init__(self, bundle_paths, bundle_dir, material_dir, as_cli, parent=None):
        super().__init__(parent)
        self.bundle_paths = bundle_paths
        self.bundle_dir = bundle_dir
        self.material_dir = material_dir
        self.as_cli = as_cli
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            # 阶段 1: 修复文件头（直接修复原始文件）
            success, fail = self._stage_fix()
            if self._cancelled:
                self.all_finished.emit(False, "已取消")
                return
            if success == 0:
                self.all_finished.emit(False, "所有文件修复失败，请删除该版本并重新下载")
                return
            self.stage_finished.emit("修复文件头")
            if fail > 0:
                logger.warning(f"[导入AS] 修复阶段：{fail} 个文件失败，继续处理 {success} 个成功文件")

            # 阶段 2: 解析资源（生成 assets_map.json）
            assets, msg = self._stage_map()
            if self._cancelled:
                self.all_finished.emit(False, "已取消")
                return
            if assets is None:
                self.all_finished.emit(False, f"资源解析失败: {msg}")
                return
            self.stage_finished.emit("解析资源")

            # 阶段 3: 导出分类到 data/material/
            total_files, msg = self._stage_export(assets)
            if self._cancelled:
                self.all_finished.emit(False, "已取消")
                return
            self.stage_finished.emit("导出分类")

            if total_files > 0:
                self.all_finished.emit(
                    True,
                    f"导入完成！文件已分类到 data/material/\n共导出 {total_files} 个文件"
                )
            else:
                self.all_finished.emit(False, "导出完成但文件数为 0，请检查资源")
        except Exception as e:
            logger.error(f"[导入AS] 工作线程异常: {e}", exc_info=True)
            self.all_finished.emit(False, f"导入失败: {e}")

    def _stage_fix(self):
        """阶段 1: 直接修复原始 .bundle 文件头"""
        total = len(self.bundle_paths)
        success = 0
        fail = 0
        logger.info(f"[导入AS] 阶段1: 修复文件头，共 {total} 个文件")
        for i, f in enumerate(self.bundle_paths):
            if self._cancelled:
                break
            h = os.path.basename(f).replace(".bundle", "")
            try:
                fix_bundle_inplace(f)
                success += 1
                logger.debug(f"[导入AS] 修复完成: {h[:16]}...")
            except Exception as e:
                logger.error(f"[导入AS] 修复失败: {h[:16]}... - {e}")
                fail += 1
            self.progress_stage.emit("修复文件头", i + 1, total)
        logger.info(f"[导入AS] 阶段1完成: 成功 {success}, 失败 {fail}")
        return success, fail

    def _stage_map(self):
        """阶段 2: 调用 AssetStudio CLI 生成资源映射"""
        if not os.path.exists(self.as_cli):
            logger.error(f"[导入AS] AssetStudio.CLI.exe 不存在: {self.as_cli}")
            return None, f"AssetStudio CLI 不存在: {self.as_cli}"
        map_dir = os.path.join(self.bundle_dir, "_map")
        os.makedirs(map_dir, exist_ok=True)
        logger.info(f"[导入AS] 阶段2: 解析资源，输出映射到 {map_dir}")
        self.progress_stage.emit("解析资源", 0, 0)
        try:
            proc = subprocess.Popen(
                [self.as_cli, self.bundle_dir, map_dir, "--game", "UnityCN", "--key_index", "23",
                 "--map_op", "Both", "--map_type", "JSON", "--silent"],
                cwd=os.path.dirname(self.as_cli), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            for line in proc.stdout:
                if self._cancelled:
                    proc.terminate()
                    break
                if "Processed" in line:
                    try:
                        p = line.split()
                        c = int(p[0].split("/")[0].lstrip("["))
                        t = int(p[0].split("/")[1])
                        self.progress_stage.emit("解析资源", c, t)
                    except Exception:
                        pass
            proc.wait()
            map_file = os.path.join(map_dir, "assets_map.json")
            if os.path.exists(map_file) and os.path.getsize(map_file) > 100:
                with open(map_file, "r", encoding="utf-8") as f:
                    assets = json.load(f)
                logger.info(f"[导入AS] 阶段2完成: 解析到 {len(assets)} 个资源")
                return assets, ""
            else:
                return None, "assets_map.json 为空或不存在"
        except Exception as e:
            return None, str(e)

    def _stage_export(self, assets):
        """阶段 3: 按类型导出所有资源到 data/material/"""
        if not os.path.exists(self.as_cli):
            logger.error(f"[导入AS] AssetStudio.CLI.exe 不存在: {self.as_cli}")
            return
        logger.info(f"[导入AS] 阶段3: 导出分类到 {self.material_dir}")
        # 清空旧目录
        if os.path.exists(self.material_dir):
            shutil.rmtree(self.material_dir, ignore_errors=True)
        os.makedirs(self.material_dir, exist_ok=True)

        all_types = sorted(set(a.get("Type", "") for a in assets if a.get("Type")))
        total_types = len(all_types)
        logger.info(f"[导入AS] 待导出类型（共 {total_types} 种）: {all_types}")

        for i, tp in enumerate(all_types):
            if self._cancelled:
                break
            self.progress_stage.emit("导出分类", i + 1, total_types)
            try:
                cmd = [self.as_cli, self.bundle_dir, self.material_dir,
                       "--game", "UnityCN", "--key_index", "23",
                       "--types", tp, "--group_assets", "ByContainer",
                       "--export_type", "Convert"]
                logger.debug(f"[导入AS] CLI 命令: {' '.join(cmd)}")
                proc = subprocess.run(
                    cmd, cwd=os.path.dirname(self.as_cli),
                    capture_output=True, text=True, timeout=300,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                if proc.returncode != 0:
                    logger.warning(
                        f"[导入AS] 类型 {tp} 导出失败 (退出码 {proc.returncode}): {proc.stderr[:300]}"
                    )
            except subprocess.TimeoutExpired:
                logger.error(f"[导入AS] 类型 {tp} 导出超时")
            except Exception as e:
                logger.error(f"[导入AS] 类型 {tp} 导出异常: {e}")

        # 统计导出文件数
        total_files = self._count_files(self.material_dir)
        # 清理 .prefab 后缀（AssetStudio CLI 可能错误添加）
        self._cleanup_prefab_suffix(self.material_dir)
        logger.info(f"[导入AS] 阶段3完成: 共导出 {total_files} 个文件")
        return total_files, ""

    @staticmethod
    def _count_files(directory):
        count = 0
        if not os.path.isdir(directory):
            return 0
        for root, dirs, files in os.walk(directory):
            count += len(files)
        return count

    @staticmethod
    def _cleanup_prefab_suffix(out_dir):
        """清理文件名末尾多余的 .prefab 后缀（AssetStudio CLI 错误添加）"""
        try:
            renamed = 0
            for root, dirs, files in os.walk(out_dir):
                for f in files:
                    if not f.endswith(".prefab"):
                        continue
                    src = os.path.join(root, f)
                    new_name = f[:-len(".prefab")]
                    dst = os.path.join(root, new_name)
                    if os.path.exists(dst):
                        continue
                    try:
                        os.rename(src, dst)
                        renamed += 1
                    except Exception:
                        pass
            if renamed > 0:
                logger.info(f"[导入AS] 清理 {renamed} 个 .prefab 后缀文件")
        except Exception as e:
            logger.error(f"[导入AS] 清理 .prefab 后缀失败: {e}")