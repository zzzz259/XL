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

from app.core.logger import logger, timed
from app.core.path_utils import DATA_DIR, get_base_dir, get_tools_dir
from app.core.bundle_parser import fix_bundle_inplace
from app.ui.workers.lua_decrypt import decompile_lua_dir


# 导出分类：类名 -> [(type, name_regex, container_regex), ...]
# 经全量 assets_map.json 分析，四类按 Container 前缀严格区分、互不重叠：
#   lua=assets/lua，角色立绘=assets/art/models，FGUI=assets/fairygui，音频=assets/fmodassets
# type=None 不按类型过滤；name_regex/container_regex 为 None 表示不过滤该维度
# 角色立绘贴图排除 _e/_n 结尾（法线/发光贴图，Unity 用，Spine 拼图用不上）
EXPORT_CATEGORIES = {
    "lua":       [("TextAsset", None, r"assets/lua")],
    "character": [("TextAsset",  None, r"assets/art/models/cardspine"),
                  ("Texture2D",  r"^(?!.*_[en]$)", r"assets/art/models/cardspine"),
                  ("TextAsset",  r"battlespine_1\d{4}", r"assets/art/models/battlespine"),
                  ("Texture2D",  r"battlespine_1\d{4}(?!.*_[en]$)", r"assets/art/models/battlespine")],
    "fgui":      [("TextAsset", None, r"assets/fairygui"),
                  ("Texture2D", None, r"assets/fairygui")],
    "audio":     [("TextAsset", None, r"assets/fmodassets/voice_cn/btl"),
                  ("TextAsset", None, r"assets/fmodassets/voice_cn/system"),
                  ("TextAsset", None, r"assets/fmodassets/voice_jp/btl"),
                  ("TextAsset", None, r"assets/fmodassets/voice_jp/system"),
                  ("TextAsset", None, r"assets/fmodassets/bgm")],
}


# 分类 -> material 子目录（用于只清空本次勾选分类，保留其他分类产物）
CATEGORY_DIRS = {
    "lua":       "assets/lua",
    "character": "assets/art/models",
    "fgui":      "assets/fairygui",
    "audio":     "assets/fmodassets",
}


class ImportASWorker(QThread):
    """导入AS后台工作线程：整合 修复→解析→导出分类 三阶段

    直接修复原始 .bundle 文件（不复制到临时目录），
    然后调用 AssetStudio CLI 生成资源映射（assets_map.json），
    最后按类型导出所有资源到 data/material/。
    """
    progress_stage = Signal(str, int, int)  # stage_name, current, total
    stage_finished = Signal(str)            # stage_name
    category_finished = Signal(str)         # 单个分类导出完成的 label（如「导出 audio」）
    all_finished = Signal(bool, str)        # success, message

    def __init__(self, bundle_paths, bundle_dir, material_dir, as_cli, parent=None,
                 export_types=None, export_categories=None):
        super().__init__(parent)
        self.bundle_paths = bundle_paths
        self.bundle_dir = bundle_dir
        self.material_dir = material_dir
        self.as_cli = as_cli
        self.export_types = export_types
        self.export_categories = export_categories
        self._cancelled = False
        self._last_progress_emit = 0.0

    def cancel(self):
        self._cancelled = True

    def _emit_progress(self, label, current, total):
        """节流进度信号：阶段起点/终点必发，中间值 200ms 才发一次，避免 UI 闪烁"""
        now = time.time()
        if current == 0 or current == total or now - self._last_progress_emit > 0.2:
            self._last_progress_emit = now
            self.progress_stage.emit(label, current, total)

    def run(self):
        try:
            sel = sorted(self.export_categories) if self.export_categories else (self.export_types or ["全部"])
            logger.info(f"[导入AS] 开始：{len(self.bundle_paths)} 个 bundle，导出分类 {sel}")
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
            # 按分类导出时跳过：映射对分类导出无用（命令直接从 EXPORT_CATEGORIES 构建）
            assets = None
            if self.export_categories:
                logger.info(f"[导入AS] 按分类导出，跳过资源映射生成: {sorted(self.export_categories)}")
            else:
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

    @timed("导入AS-修复文件头")
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

    @timed("导入AS-解析资源")
    def _stage_map(self):
        """阶段 2: 调用 AssetStudio CLI 生成资源映射"""
        if not os.path.exists(self.as_cli):
            logger.error(f"[导入AS] AssetStudio.CLI.exe 不存在: {self.as_cli}")
            return None, f"AssetStudio CLI 不存在: {self.as_cli}"
        map_dir = os.path.join(self.bundle_dir, "_map")
        os.makedirs(map_dir, exist_ok=True)
        total_bundles = len(self.bundle_paths)
        logger.info(f"[导入AS] 阶段2: 解析资源，输出映射到 {map_dir}（{total_bundles} 个 bundle）")
        self._emit_progress("解析资源", 0, total_bundles)
        try:
            proc = subprocess.Popen(
                [self.as_cli, self.bundle_dir, map_dir, "--game", "UnityCN", "--key_index", "23",
                 "--map_op", "Both", "--map_type", "JSON"],
                cwd=os.path.dirname(self.as_cli), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            loaded = 0
            for line in proc.stdout:
                if self._cancelled:
                    proc.terminate()
                    break
                line = line.strip()
                if not line:
                    continue
                if "Loading" in line and ".bundle" in line:
                    loaded += 1
                    self._emit_progress("解析资源", loaded, total_bundles)
                elif "Process Assets" in line or "Read assets" in line:
                    logger.debug(f"[导入AS] CLI: {line}")
                else:
                    logger.debug(f"[导入AS] CLI: {line}")
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

    @timed("导入AS-导出分类")
    def _stage_export(self, assets):
        """阶段 3: 按分类（或类型）导出资源到 data/material/"""
        if not os.path.exists(self.as_cli):
            logger.error(f"[导入AS] AssetStudio.CLI.exe 不存在: {self.as_cli}")
            return
        logger.info(f"[导入AS] 阶段3: 导出分类到 {self.material_dir}")
        os.makedirs(self.material_dir, exist_ok=True)
        # 只清空本次勾选分类对应的目录（保留其他分类产物，如 lua 反编译结果）
        if self.export_categories:
            for cat in sorted(self.export_categories):
                rel = CATEGORY_DIRS.get(cat)
                if not rel:
                    continue
                cat_dir = os.path.join(self.material_dir, rel)
                if os.path.exists(cat_dir):
                    shutil.rmtree(cat_dir, ignore_errors=True)
                    logger.info(f"[导入AS] 清空 {cat} 目录: {cat_dir}")
                else:
                    logger.info(f"[导入AS] {cat} 目录不存在，无需清空: {cat_dir}")
        else:
            # 调试模式（按类型导出）仍清空整个 material
            if os.path.exists(self.material_dir):
                shutil.rmtree(self.material_dir, ignore_errors=True)
                logger.info(f"[导入AS] 清空整个 material 目录: {self.material_dir}")
            os.makedirs(self.material_dir, exist_ok=True)

        # 构建导出命令列表
        if self.export_categories:
            commands = self._build_category_commands()
            logger.info(f"[导入AS] 按分类导出: {sorted(self.export_categories)}")
        else:
            all_types = sorted(set(a.get("Type", "") for a in assets if a.get("Type")))
            if self.export_types:
                all_types = [t for t in all_types if t in self.export_types]
            commands = [(f"导出 {tp}", ["--types", tp]) for tp in all_types]
            logger.info(f"[导入AS] 按类型导出（共 {len(commands)} 种）: {all_types}")

        total = len(commands)
        total_bundles = len(self.bundle_paths)
        for i, (label, extra_args) in enumerate(commands):
            if self._cancelled:
                break
            self.progress_stage.emit("导出分类", i + 1, total)
            cmd = [self.as_cli, self.bundle_dir, self.material_dir,
                   "--game", "UnityCN", "--key_index", "23"] + extra_args + \
                  ["--group_assets", "ByContainer", "--export_type", "Convert"]
            logger.info(f"[导入AS] {label} 开始（{i + 1}/{total}）: {' '.join(cmd)}")
            t0 = time.time()
            try:
                proc = subprocess.Popen(
                    cmd, cwd=os.path.dirname(self.as_cli),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                loaded = 0
                for line in proc.stdout:
                    if self._cancelled:
                        proc.terminate()
                        break
                    line = line.strip()
                    if not line:
                        continue
                    if "Loading" in line and ".bundle" in line:
                        loaded += 1
                        self._emit_progress(label, loaded, total_bundles)
                    elif "Process Assets" in line or "Read assets" in line:
                        logger.debug(f"[导入AS] CLI: {line}")
                    else:
                        logger.debug(f"[导入AS] CLI: {line}")
                proc.wait()
                elapsed = time.time() - t0
                if proc.returncode != 0:
                    logger.warning(f"[导入AS] {label} 导出失败 (退出码 {proc.returncode}, 耗时 {elapsed:.1f}s)")
                else:
                    logger.info(f"[导入AS] {label} 完成（耗时 {elapsed:.1f}s）")
                # 通知单个分类导出完成（边导出边预览：主窗口收到后刷新对应视图）
                self.category_finished.emit(label)
            except subprocess.TimeoutExpired:
                logger.error(f"[导入AS] {label} 导出超时（>300s）")
            except Exception as e:
                logger.error(f"[导入AS] {label} 导出异常: {e}")

        # 若导出了 lua 分类，反编译 .lua.bytes → .lua（角色数据加载免等待）
        if self.export_categories and "lua" in self.export_categories:
            self._decompile_lua()

        # 统计导出文件数
        total_files = self._count_files(self.material_dir)
        # 清理 .prefab 后缀（AssetStudio CLI 可能错误添加）
        self._cleanup_prefab_suffix(self.material_dir)
        logger.info(f"[导入AS] 阶段3完成: 共导出 {total_files} 个文件到 {self.material_dir}")
        return total_files, ""

    def _build_category_commands(self):
        """根据勾选的分类构建 CLI 导出命令（label, extra_args）列表"""
        commands = []
        for cat in sorted(self.export_categories):
            for type_filter, name_regex, container_regex in EXPORT_CATEGORIES.get(cat, []):
                extra = []
                if type_filter:
                    extra += ["--types", type_filter]
                if name_regex:
                    extra += ["--names", name_regex]
                if container_regex:
                    extra += ["--containers", container_regex]
                commands.append((f"导出 {cat}", extra))
        return commands

    @timed("导入AS-反编译Lua")
    def _decompile_lua(self):
        """TextAsset 导出后，反编译 data/material/assets/lua/ 下的 .lua.bytes → .lua"""
        lua_dir = os.path.join(self.material_dir, "assets", "lua")
        if not os.path.isdir(lua_dir):
            return
        tools_dir = get_tools_dir()
        unluac_path = os.path.join(tools_dir, "lua", "unluac.jar")
        opmap_path = os.path.join(tools_dir, "lua", "opmap")
        if not os.path.isfile(unluac_path):
            logger.warning("[导入AS] unluac.jar 不存在，跳过 Lua 反编译")
            return
        # 统计待反编译文件数（近似 total，用于进度条）
        total = 0
        for _root, _dirs, files in os.walk(lua_dir):
            for f in files:
                if f.endswith(".lua.bytes") or f.endswith(".lua.bank"):
                    total += 1
        logger.info(f"[导入AS] 开始反编译 Lua，约 {total} 个文件")
        self.progress_stage.emit("反编译 Lua", 0, total if total else 1)

        done = [0]
        def on_file_done(name):
            done[0] += 1
            self._emit_progress("反编译 Lua", min(done[0], total), total if total else 1)

        def on_progress(msg):
            m = re.search(r"正在反编译 Lua:\s*(\d+)/(\d+)", msg)
            if m:
                d, t = int(m.group(1)), int(m.group(2))
                self._emit_progress("反编译 Lua", d, t)
            else:
                logger.info(f"[导入AS] {msg}")

        success, fail = decompile_lua_dir(
            lua_dir, unluac_path, opmap_path,
            progress_cb=on_progress,
            file_done_cb=on_file_done,
            cancel_check=lambda: self._cancelled)
        self.progress_stage.emit("反编译 Lua", total, total if total else 1)
        logger.info(f"[导入AS] Lua 反编译完成: 成功 {success}, 失败 {fail}")

        # 同步反编译的 .lua 到 output/lua/（留存，供后续查询）
        out_lua_dir = os.path.join(get_base_dir(), "output", "lua")
        os.makedirs(out_lua_dir, exist_ok=True)
        copied = 0
        for _root, _dirs, files in os.walk(lua_dir):
            for f in files:
                if f.endswith(".lua"):
                    src = os.path.join(_root, f)
                    dst = os.path.join(out_lua_dir, f)
                    try:
                        if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
                            continue
                        shutil.copy2(src, dst)
                        copied += 1
                    except (OSError, PermissionError) as e:
                        logger.warning(f"[导入AS] 同步 lua 失败 {f}: {e}")
        logger.info(f"[导入AS] 已留存 {copied} 个 .lua 到 {out_lua_dir}")

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