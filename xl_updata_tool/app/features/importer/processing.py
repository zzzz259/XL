# -*- coding: utf-8 -*-
"""AssetStudio 导入处理器。

本模块不依赖 Qt，只负责修复 Bundle、调用 AssetStudio、分类导出、Lua
发布、临时目录提交与回滚。Qt Worker 位于 ``app.features.importer.worker``。
"""

import os
import json
import re
import subprocess
import shutil
import sys
import tempfile
import time

from app.platform.diagnostics import logger, timed, stage_operation, task_operation
from app.platform.paths import get_base_dir, get_tools_dir
from app.platform.bundle_parser import fix_bundle_inplace
from app.platform.files import replace_directory
from app.platform.lua_repository import cleanup_lua_staging, publish_lua_version
from .lua_decrypt import decompile_lua_dir
from app.features.importer.spec import CATEGORY_DIRS, EXPORT_SPECS, build_category_commands


# 兼容旧调用方；新的分类定义统一维护在 Importer Feature 的 ExportSpec 中。
EXPORT_CATEGORIES = {
    category: [
        (rule.asset_type, rule.name_regex, rule.container_regex)
        for rule in spec.rules
    ]
    for category, spec in EXPORT_SPECS.items()
}


class ImportProcessor:
    """执行 AssetStudio 导入三阶段流程，不持有 Qt 状态。

    直接修复原始 .bundle 文件（不复制到临时目录），
    然后调用 AssetStudio CLI 生成资源映射（assets_map.json），
    最后按分类导出到 data/material/；Lua 分类提交后会立即发布到 output/lua/<版本>/，
    并清理 data/material/assets/lua 中的临时 Lua 文件。
    """
    def __init__(self, bundle_paths, bundle_dir, material_dir, as_cli,
                 export_types=None, export_categories=None, version_timestamp=None,
                 lua_output_dir=None, isolate_bundle_dir=False,
                 progress_stage_callback=None, stage_finished_callback=None,
                 category_finished_callback=None, all_finished_callback=None,
                 cancel_check=None):
        self.bundle_paths = bundle_paths
        self.bundle_dir = bundle_dir
        self.material_dir = material_dir
        self.as_cli = as_cli
        self.export_types = export_types
        self.export_categories = export_categories
        self.version_timestamp = version_timestamp
        self.lua_output_dir = lua_output_dir or os.path.join(get_base_dir(), "output", "lua")
        self.lua_export_result = None
        self._cancelled = False
        self._last_progress_emit = 0.0
        self._working_material_dir = material_dir
        self._staging_material_dir = None
        self._isolate_bundle_dir = bool(isolate_bundle_dir)
        self._cli_bundle_dir = bundle_dir
        self._isolated_bundle_dir = None
        self._progress_stage_callback = progress_stage_callback
        self._stage_finished_callback = stage_finished_callback
        self._category_finished_callback = category_finished_callback
        self._all_finished_callback = all_finished_callback
        self._cancel_check = cancel_check

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled or bool(self._cancel_check and self._cancel_check())

    def _emit_progress_stage(self, label, current, total):
        if self._progress_stage_callback:
            self._progress_stage_callback(label, current, total)

    def _emit_stage_finished(self, label):
        if self._stage_finished_callback:
            self._stage_finished_callback(label)

    def _emit_category_finished(self, label):
        if self._category_finished_callback:
            self._category_finished_callback(label)

    def _emit_all_finished(self, success, message):
        if self._all_finished_callback:
            self._all_finished_callback(success, message)

    def _emit_progress(self, label, current, total):
        """节流进度信号：阶段起点/终点必发，中间值 200ms 才发一次，避免 UI 闪烁"""
        now = time.time()
        if current == 0 or current == total or now - self._last_progress_emit > 0.2:
            self._last_progress_emit = now
            self._emit_progress_stage(label, current, total)

    @task_operation(
        "IMPORT",
        "import",
        lambda self: {
            "bundles": len(self.bundle_paths),
            "categories": sorted(self.export_categories) if self.export_categories else ["all"],
        },
    )
    def process(self):
        try:
            sel = sorted(self.export_categories) if self.export_categories else (self.export_types or ["全部"])
            logger.info(f"[导入AS] 开始：{len(self.bundle_paths)} 个 bundle，导出分类 {sel}")
            # 阶段 1: 修复文件头（直接修复原始文件）
            success, fail = self._stage_fix()
            if self.is_cancelled():
                self._emit_all_finished(False, "已取消")
                return
            if success == 0:
                self._emit_all_finished(False, "所有文件修复失败，请删除该版本并重新下载")
                return
            self._emit_stage_finished("修复文件头")
            if fail > 0:
                logger.warning(f"[导入AS] 修复阶段：{fail} 个文件失败，继续处理 {success} 个成功文件")

            # Lua/音频单分类导出可只把映射命中的包交给 CLI；临时目录在阶段 1
            # 之后创建，确保原始包仍由修复阶段直接处理。
            self._prepare_cli_bundle_dir()

            # 阶段 2: 解析资源（生成 assets_map.json）
            # 按分类导出时跳过：映射对分类导出无用（命令直接从 EXPORT_CATEGORIES 构建）
            assets = None
            if self.export_categories:
                logger.info(f"[导入AS] 按分类导出，跳过资源映射生成: {sorted(self.export_categories)}")
            else:
                assets, msg = self._stage_map()
                if self.is_cancelled():
                    self._emit_all_finished(False, "已取消")
                    return
                if assets is None:
                    self._emit_all_finished(False, f"资源解析失败: {msg}")
                    return
                self._emit_stage_finished("解析资源")

            # 阶段 3: 导出分类到 data/material/
            total_files, msg = self._stage_export(assets)
            if self.is_cancelled():
                self._emit_all_finished(False, "已取消")
                return
            if msg:
                self._emit_all_finished(False, msg)
                return
            self._emit_stage_finished("导出分类")

            if total_files > 0:
                destination = (
                    "Lua 已按版本写入 output/lua/，其余分类写入 data/material/"
                    if self.export_categories and "lua" in self.export_categories
                    else "文件已分类到 data/material/"
                )
                self._emit_all_finished(
                    True,
                    f"导入完成！{destination}\n共导出 {total_files} 个文件"
                )
            else:
                self._emit_all_finished(False, "导出完成但文件数为 0，请检查资源")
        except Exception as e:
            logger.error(f"[导入AS] 工作线程异常: {e}", exc_info=True)
            self._emit_all_finished(False, f"导入失败: {e}")
        finally:
            self._cleanup_cli_bundle_dir()

    @stage_operation("import.fix", "bundle_header")
    @timed("导入AS-修复文件头")
    def _stage_fix(self):
        """阶段 1: 直接修复原始 .bundle 文件头"""
        total = len(self.bundle_paths)
        success = 0
        fail = 0
        logger.info(f"[导入AS] 阶段1: 修复文件头，共 {total} 个文件")
        for i, f in enumerate(self.bundle_paths):
            if self.is_cancelled():
                break
            h = os.path.basename(f).replace(".bundle", "")
            try:
                if fix_bundle_inplace(f):
                    success += 1
                    logger.debug(f"[导入AS] 修复完成: {h[:16]}...")
                else:
                    fail += 1
                    logger.error(f"[导入AS] 修复未通过: {h[:16]}...")
            except Exception as e:
                logger.error(f"[导入AS] 修复失败: {h[:16]}... - {e}")
                fail += 1
            self._emit_progress_stage("修复文件头", i + 1, total)
        logger.info(f"[导入AS] 阶段1完成: 成功 {success}, 失败 {fail}")
        return success, fail

    @timed("导入AS-解析资源")
    @stage_operation("import.assetstudio", "map")
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
                [self.as_cli, self._cli_bundle_dir, map_dir, "--game", "UnityCN", "--key_index", "23",
                 "--map_op", "Both", "--map_type", "JSON"],
                cwd=os.path.dirname(self.as_cli), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            loaded = 0
            for line in proc.stdout:
                if self.is_cancelled():
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
    @stage_operation("import.export", "assets")
    def _stage_export(self, assets):
        """阶段 3: 先导出到 staging，成功后再替换 data/material/。"""
        if not os.path.exists(self.as_cli):
            logger.error(f"[导入AS] AssetStudio.CLI.exe 不存在: {self.as_cli}")
            return 0, f"AssetStudio CLI 不存在: {self.as_cli}"

        material_parent = os.path.dirname(os.path.abspath(self.material_dir))
        os.makedirs(material_parent, exist_ok=True)
        staging_root = tempfile.mkdtemp(prefix=".xl-import-", dir=material_parent)
        self._staging_material_dir = staging_root
        self._working_material_dir = os.path.join(staging_root, "material")
        os.makedirs(self._working_material_dir, exist_ok=True)
        logger.info(f"[导入AS] 阶段3: 先导出到临时目录 {self._working_material_dir}")

        try:
            if self.export_categories:
                commands = self._build_category_commands()
                logger.info(f"[导入AS] 按分类导出: {sorted(self.export_categories)}")
            else:
                all_types = sorted(set(a.get("Type", "") for a in assets if a.get("Type")))
                if self.export_types:
                    all_types = [t for t in all_types if t in self.export_types]
                commands = [(f"导出 {tp}", ["--types", tp]) for tp in all_types]
                logger.info(f"[导入AS] 按类型导出（共 {len(commands)} 种）: {all_types}")

            if not commands:
                return 0, "没有可执行的导出分类"

            total = len(commands)
            total_bundles = len(self.bundle_paths)
            failed_labels = []
            completed_labels = []
            for i, (label, extra_args) in enumerate(commands):
                if self.is_cancelled():
                    return 0, "已取消"
                self._emit_progress_stage("导出分类", i + 1, total)
                cmd = [self.as_cli, self._cli_bundle_dir, self._working_material_dir,
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
                        if self.is_cancelled():
                            proc.terminate()
                            break
                        line = line.strip()
                        if not line:
                            continue
                        if "Loading" in line and ".bundle" in line:
                            loaded += 1
                            self._emit_progress(label, loaded, total_bundles)
                        else:
                            logger.debug(f"[导入AS] CLI: {line}")
                    proc.wait()
                    elapsed = time.time() - t0
                    if proc.returncode != 0:
                        failed_labels.append(label)
                        logger.warning(f"[导入AS] {label} 导出失败 (退出码 {proc.returncode}, 耗时 {elapsed:.1f}s)")
                    else:
                        logger.info(f"[导入AS] {label} 完成（耗时 {elapsed:.1f}s）")
                        completed_labels.append(label)
                except subprocess.TimeoutExpired:
                    failed_labels.append(label)
                    logger.error(f"[导入AS] {label} 导出超时（>300s）")
                except Exception as e:
                    failed_labels.append(label)
                    logger.error(f"[导入AS] {label} 导出异常: {e}")

            if self.is_cancelled():
                return 0, "已取消"
            if failed_labels:
                hard_failed = []
                if self.export_categories:
                    for label in sorted(set(failed_labels)):
                        category = label.removeprefix("导出 ")
                        relative = CATEGORY_DIRS.get(category)
                        output_dir = os.path.join(self._working_material_dir, relative) if relative else ""
                        if not output_dir or self._count_files(output_dir) == 0:
                            hard_failed.append(label)
                elif self._count_files(self._working_material_dir) == 0:
                    hard_failed = failed_labels

                if hard_failed:
                    return 0, f"导出失败，未替换已有产物: {', '.join(sorted(set(hard_failed)))}"
                logger.warning(
                    "[导入AS] AssetStudio 部分命令失败，但目标分类已有产物，继续提交：%s",
                    ", ".join(sorted(set(failed_labels))),
                )

            if self.export_categories and "lua" in self.export_categories:
                self._decompile_lua()

            self._cleanup_prefab_suffix(self._working_material_dir)
            total_files = self._count_files(self._working_material_dir)
            if total_files <= 0:
                return 0, "导出完成但文件数为 0，请检查资源"

            self._commit_staged_material()
            if self.export_categories and "lua" in self.export_categories:
                self.lua_export_result = self._sync_lua_output()
            for category in sorted(self.export_categories or ()):
                if category not in {label.removeprefix("导出 ") for label in completed_labels}:
                    completed_labels.append(f"导出 {category}")
            for label in completed_labels:
                self._emit_category_finished(label)
            logger.info(f"[导入AS] 阶段3完成: 共导出 {total_files} 个文件到 {self.material_dir}")
            return total_files, ""
        finally:
            self._working_material_dir = self.material_dir
            if self._staging_material_dir:
                shutil.rmtree(self._staging_material_dir, ignore_errors=True)
                self._staging_material_dir = None

    def _commit_staged_material(self):
        """只在全部分类成功后替换最终产物，保留未选中的分类。"""
        if not self.export_categories:
            replace_directory(self._working_material_dir, self.material_dir)
            return

        material_root = os.path.abspath(self.material_dir)
        backup_root = tempfile.mkdtemp(prefix=".xl-import-backup-", dir=os.path.dirname(material_root))
        replacements = []
        try:
            for category in sorted(self.export_categories):
                relative = CATEGORY_DIRS.get(category)
                if not relative:
                    continue
                source = os.path.join(self._working_material_dir, relative)
                destination = os.path.join(material_root, relative)
                os.makedirs(source, exist_ok=True)
                replacements.append((source, destination, relative))

            # 先把所有旧分类移入备份区，再开始放入新分类，避免跨分类提交留下半成品。
            for _source, destination, relative in replacements:
                if os.path.exists(destination):
                    backup = os.path.join(backup_root, relative)
                    os.makedirs(os.path.dirname(backup), exist_ok=True)
                    os.replace(destination, backup)

            for source, destination, _relative in replacements:
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                os.replace(source, destination)
        except Exception:
            logger.error("[导入AS] 分类提交失败，开始回滚", exc_info=True)
            for _source, destination, _relative in reversed(replacements):
                if os.path.isdir(destination):
                    shutil.rmtree(destination, ignore_errors=True)
            for _source, destination, relative in replacements:
                backup = os.path.join(backup_root, relative)
                if os.path.exists(backup):
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    os.replace(backup, destination)
            raise
        finally:
            shutil.rmtree(backup_root, ignore_errors=True)

    def _sync_lua_output(self):
        """提交成功后，把 Lua 发布到版本目录并清理 material 临时目录。"""
        lua_dir = os.path.join(self.material_dir, "assets", "lua")
        if self.version_timestamp is None:
            logger.warning("[导入AS] 未提供 Lua 版本标识，跳过最终 Lua 版本发布（测试/兼容调用）")
            return None
        version_dir, copied, source_ready = publish_lua_version(
            lua_dir, self.lua_output_dir, self.version_timestamp
        )
        cleanup_lua_staging(self.material_dir)
        logger.info(
            "[导入AS] Lua 已按版本留存: %s（%s 个文件，角色 Base=%s）",
            version_dir, copied, source_ready,
        )
        return {
            "version": int(self.version_timestamp),
            "directory": version_dir,
            "file_count": copied,
            "character_sources": source_ready,
        }

    def _prepare_cli_bundle_dir(self):
        """为精确分类导出创建只包含命中 AB 的 CLI 输入目录。"""
        if not self._isolate_bundle_dir or not self.bundle_paths:
            return
        parent = os.path.dirname(os.path.abspath(self.bundle_dir))
        staged = tempfile.mkdtemp(prefix=".xl-lua-bundles-", dir=parent)
        try:
            for source in self.bundle_paths:
                destination = os.path.join(staged, os.path.basename(source))
                try:
                    os.link(source, destination)
                except OSError:
                    shutil.copy2(source, destination)
            self._isolated_bundle_dir = staged
            self._cli_bundle_dir = staged
            logger.info(
                "[导入AS] 精准分类导入：AssetStudio 仅使用 %s 个命中 bundle（输入目录=%s）",
                len(self.bundle_paths), staged,
            )
        except Exception:
            shutil.rmtree(staged, ignore_errors=True)
            raise

    def _cleanup_cli_bundle_dir(self):
        if self._isolated_bundle_dir:
            shutil.rmtree(self._isolated_bundle_dir, ignore_errors=True)
            self._isolated_bundle_dir = None
            self._cli_bundle_dir = self.bundle_dir

    def _build_category_commands(self):
        """根据勾选的分类构建 CLI 导出命令（label, extra_args）列表"""
        return list(build_category_commands(self.export_categories))

    @timed("导入AS-反编译Lua")
    def _decompile_lua(self):
        """TextAsset 导出后，在 staging 的 lua 目录中反编译。"""
        lua_dir = os.path.join(self._working_material_dir, "assets", "lua")
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
        self._emit_progress_stage("反编译 Lua", 0, total if total else 1)

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
            cancel_check=self.is_cancelled)
        self._emit_progress_stage("反编译 Lua", total, total if total else 1)
        logger.info(f"[导入AS] Lua 反编译完成: 成功 {success}, 失败 {fail}")


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
