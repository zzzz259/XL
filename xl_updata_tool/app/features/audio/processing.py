# -*- coding: utf-8 -*-
"""音频导出处理器（.bytes → .bank → 解密 → output/audio/）。

本模块不依赖 Qt。它只负责音频导出的文件处理、分类、缓存和取消检查；
Qt Worker 位于 ``app.features.audio.worker``，只把处理器回调映射为信号。
"""

import os
import re
import shutil
import sys

from app.core.logger import logger, timed
from app.core.task_context import stage_operation, task_operation
from .album_map import audit_bgm_exports, build_album_map
from app.core.lua_repository import latest_lua_version, version_directory
from app.core.path_utils import get_base_dir


class AudioDecryptProcessor:
    """执行一次音频导出，不持有 Qt 状态。

    ``progress_callback``、``progress_value_callback`` 和 ``cancel_check`` 是
    透明的协作端口，Worker 可以将它们连接到 Qt 信号而不让处理器反向依赖 UI。
    """
    def __init__(
        self,
        material_dir,
        audio_output_dir,
        debank_dir,
        force=False,
        lua_output_dir=None,
        progress_callback=None,
        progress_value_callback=None,
        cancel_check=None,
    ):
        self.material_dir = material_dir
        self.audio_output_dir = audio_output_dir
        self.debank_dir = debank_dir
        self.force = force
        self.lua_output_dir = lua_output_dir or os.path.join(get_base_dir(), "output", "lua")
        self._cancelled = False
        self._cancel_check = cancel_check
        self._progress_callback = progress_callback
        self._progress_value_callback = progress_value_callback
        self._album_map = {}
        self._audio_path_index = None

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled or bool(self._cancel_check and self._cancel_check())

    def _emit_progress(self, message):
        if self._progress_callback:
            self._progress_callback(message)

    def _emit_progress_value(self, current, total, message):
        if self._progress_value_callback:
            self._progress_value_callback(current, total, message)

    @task_operation(
        "AUDIO",
        "audio",
        lambda self: {"force": self.force, "material": self.material_dir, "output": self.audio_output_dir},
    )
    def process(self):
        try:
            logger.info(f"音频解密开始：material={self.material_dir}, output={self.audio_output_dir}, force={self.force}")
            # 步骤 1：转换 .bytes → .bank
            self._emit_progress("正在转换 .bytes 文件...")
            self._convert_bytes_to_bank()

            if self.is_cancelled():
                return {"cancelled": True}

            # 步骤 2：解密 .bank 文件
            result = self._decrypt_bank_files()
            return result or {"cancelled": self.is_cancelled()}
        except Exception as e:
            logger.error(f"音频解密线程异常: {e}", exc_info=True)
            raise
        finally:
            self._cleanup_material_audio()
            self._cleanup_debank_input()

    @stage_operation("audio.scan", "bytes_to_bank")
    @timed("音频-转换bytes")
    def _convert_bytes_to_bank(self):
        """扫描 data/material/ 目录，将符合条件的 .bytes 文件重命名为 .bank，并复制到解密工具的 input 目录

        筛选规则（两者需同时满足）：
        1. 文件所在路径包含 fmodassets/ 子目录
        2. 语音文件（btl/system）文件名纯数字，或 bgm 文件位于 bgm/ 子目录下
        """
        if not os.path.isdir(self.material_dir):
            logger.warning(f"素材目录不存在，跳过转换: {self.material_dir}")
            return 0

        count = 0
        skipped = 0

        for root, dirs, files in os.walk(self.material_dir):
            for f in files:
                if not f.endswith(".bytes"):
                    continue

                bytes_path = os.path.join(root, f)
                parts = root.split(os.sep)

                # 筛选规则 1：路径必须包含 fmodassets
                if "fmodassets" not in parts:
                    logger.debug(f"跳过非音频 .bytes 文件（不在 fmodassets 目录下）: {bytes_path}")
                    skipped += 1
                    continue

                # 筛选规则 2：语音(纯数字名) 或 bgm(位于 bgm 目录)
                base = os.path.splitext(f)[0]
                if not base.isdigit() and "bgm" not in parts:
                    logger.debug(f"跳过非音频 .bytes 文件（非语音/bgm）: {bytes_path}")
                    skipped += 1
                    continue

                bank_name = f[:-len(".bytes")] + ".bank"
                bank_path = os.path.join(root, bank_name)

                try:
                    os.rename(bytes_path, bank_path)
                    count += 1
                    logger.info(f"转换 .bytes → .bank: {f} → {bank_name}")
                except (OSError, PermissionError) as e:
                    logger.error(f"重命名失败 {f}: {e}")
                    continue

        logger.info(f"扫描到音频 .bytes 文件: {count} 个（跳过 {skipped} 个非音频文件）")
        if count > 0:
            self._emit_progress(f"已转换 {count} 个 .bytes → .bank")
        return count

    @stage_operation("audio.decrypt", "bank")
    @timed("音频-解密bank")
    def _decrypt_bank_files(self):
        """通过导入 epic7_debank 模块解密 .bank 文件（递归扫描 data/material/，输出到 output/audio/）"""
        if not os.path.isdir(self.debank_dir):
            logger.warning(f"epic7_debank 目录不存在: {self.debank_dir}")
            return

        # 检查 data/material/ 中是否有符合条件的 .bank 文件
        bank_count = 0
        if os.path.isdir(self.material_dir):
            for root, dirs, files in os.walk(self.material_dir):
                for f in files:
                    if f.lower().endswith(".bank"):
                        # 与 _convert_bytes_to_bank 使用相同的筛选规则
                        base = os.path.splitext(f)[0]
                        parts = root.split(os.sep)
                        if "fmodassets" in parts and (base.isdigit() or "bgm" in parts):
                            bank_count += 1
        if bank_count == 0:
            logger.info("素材目录无 .bank 文件，跳过解密")
            return

        # 不能用“输出目录非空”判断整个任务已完成：新版本可能只增加少量 bank。
        # epic7_debank.run() 本身按 bank 和文件大小去重，因此这里始终扫描当前输入，
        # 让新增语音/BGM 能够补进已有 output/audio/。
        if not self.force and os.path.isdir(self.audio_output_dir):
            existing_audio = sum(
                1
                for root, _dirs, files in os.walk(self.audio_output_dir)
                for f in files
                if os.path.splitext(f)[1].lower() in (".wav", ".ogg", ".mp3")
                and os.path.getsize(os.path.join(root, f)) > 0
            )
            if existing_audio > 0:
                logger.info("output/audio/ 已有 %s 个音频文件，但仍扫描当前 bank 以支持增量解密", existing_audio)

        self._emit_progress(f"正在解密 {bank_count} 个 .bank 文件...")

        try:
            os.makedirs(self.audio_output_dir, exist_ok=True)
            # Lua 已按版本留存在 output/lua；不依赖会被清理的 material 临时目录。
            lua_dir = self._latest_lua_dir()
            self._album_map = build_album_map(lua_dir) if lua_dir else {}
            if lua_dir:
                logger.info(f"[专辑映射] 使用已留存 Lua: {lua_dir}")
            else:
                logger.info("[专辑映射] 未找到已留存 Lua，BGM 暂归入未分类")
            self._audio_path_index = self._build_audio_path_index()
            # 将 epic7_debank 目录加入 sys.path 后导入调用，避免打包后 subprocess 递归启动 EXE
            if self.debank_dir not in sys.path:
                sys.path.insert(0, self.debank_dir)
            import epic7_debank
            result = epic7_debank.run(
                self.material_dir, self.audio_output_dir,
                progress_callback=lambda c, t: self._emit_progress_value(c, t, f"正在解密 .bank: {c}/{t}"),
                subdir_fn=self._audio_subdir,
                before_copy_callback=self._before_audio_copy,
                audio_transform_callback=self._normalize_voice_audio_files,
                temp_dir=os.path.join(self.audio_output_dir, ".debank-temp"),
                cancel_check=self.is_cancelled,
                use_cache=not self.force,
            )
            # 统计输出目录中的音频文件数（递归扫描子目录）
            audio_count = 0
            for root, dirs, files in os.walk(self.audio_output_dir):
                for f in files:
                    if os.path.splitext(f)[1].lower() in (".wav", ".ogg", ".mp3"):
                        audio_count += 1
            self._emit_progress(f"解密完成: 提取 {audio_count} 个音频文件")
            logger.info(f"解密完成: 输出 {audio_count} 个音频文件到 {self.audio_output_dir}")
            self._log_bgm_audit(lua_dir)
            return result

        except Exception as e:
            logger.error(f"解密异常: {e}", exc_info=True)
            raise RuntimeError(f"解密异常: {e}") from e

    def _audio_subdir(self, rel_path, bank_stem):
        """决定单个 .bank 解包出的音频输出到哪个子目录。

        语音：voice/<数字ID>/<cn|jp>/
        专辑：album/<专辑名>/（未匹配到专辑的 bgm 归入 未分类）
        """
        parts = rel_path.replace("\\", "/").split("/")
        if "bgm" in parts:
            bank_key = "/".join(parts[parts.index("bgm"):])
            album = (
                self._album_map.get(bank_key.lower())
                or self._album_map.get(str(bank_stem).lower())
                or "未分类"
            ) if self._album_map else "未分类"
            return os.path.join("album", album)
        lang = "jp" if "voice_jp" in parts else "cn"
        return os.path.join("voice", bank_stem, lang)

    def _latest_lua_dir(self):
        version = latest_lua_version(self.lua_output_dir)
        return version_directory(self.lua_output_dir, version) if version is not None else None

    def _before_audio_copy(self, bank_stem, rel_path, output_subdir, filenames):
        """修正旧分类时移除同作用域的历史输出，避免跨语言误删同名语音。

        输出索引只构建一次，避免每个 bank、每个 subsong 都递归扫描数千个
        最终文件；这也是大批量 debank 实机耗时异常的主要 UI 外部瓶颈。
        """
        expected_dir = os.path.normcase(os.path.abspath(os.path.join(self.audio_output_dir, output_subdir)))
        source_language = self._audio_language(rel_path)
        if source_language:
            self._remove_mismatched_voice_files(expected_dir, bank_stem, filenames)
            self._remove_legacy_voice_aliases(expected_dir, bank_stem, filenames)
        path_index = self._audio_path_index
        if path_index is None:
            path_index = self._audio_path_index = self._build_audio_path_index()
        for filename in filenames:
            key = str(filename).lower()
            target = os.path.normcase(os.path.abspath(os.path.join(expected_dir, filename)))
            candidates = list(path_index.get(key, set()))
            for candidate in candidates:
                candidate_root = os.path.dirname(candidate)
                if candidate == target:
                    continue
                if source_language and not self._is_same_voice_scope(
                    candidate_root, bank_stem, source_language
                ):
                    continue
                try:
                    os.remove(candidate)
                    logger.info("移除音频旧分类输出: bank=%s file=%s", bank_stem, candidate)
                    path_index[key].discard(candidate)
                except OSError as e:
                    logger.warning("移除音频旧分类输出失败: %s (%s)", candidate, e)
            path_index.setdefault(key, set()).add(target)

    def _build_audio_path_index(self):
        """建立最终音频的 basename 索引，排除 debank 临时目录。"""
        index = {}
        if not os.path.isdir(self.audio_output_dir):
            return index
        for root, _dirs, files in os.walk(self.audio_output_dir):
            if self._is_audio_staging_root(root):
                continue
            for filename in files:
                if not filename.lower().endswith((".wav", ".ogg", ".mp3")):
                    continue
                path = os.path.normcase(os.path.abspath(os.path.join(root, filename)))
                index.setdefault(filename.lower(), set()).add(path)
        return index

    def _discard_audio_path(self, path):
        """从分类清理索引移除一个已删除的最终文件。"""
        if self._audio_path_index is None:
            return
        key = os.path.basename(path).lower()
        paths = self._audio_path_index.get(key)
        if paths is not None:
            paths.discard(os.path.normcase(os.path.abspath(path)))

    def _log_bgm_audit(self, lua_dir):
        """记录 Lua 配置与已发布 BGM bank 的缺口，不把额外 BGM 误报为缺失。"""
        if not lua_dir:
            return
        report = audit_bgm_exports(lua_dir, self.audio_output_dir)
        if not report.get("available") or not report.get("state_available"):
            logger.info("[专辑审计] 缺少 Lua 或 bank 状态，跳过 BGM 完整性核对")
            return
        expected = sum(len(items) for items in report["expected_by_album"].values())
        missing = sum(len(items) for items in report["missing_by_album"].values())
        logger.info(
            "[专辑审计] Lua 配置 %d 个 BGM bank，缺失 %d 个，错分类 %d 个，额外未归类 %d 个",
            expected,
            missing,
            len(report["misclassified"]),
            len(report["untracked"]),
        )
        for album, banks in report["missing_by_album"].items():
            logger.warning("[专辑审计] %s 缺少: %s", album, ", ".join(banks))
        for item in report["misclassified"]:
            logger.warning(
                "[专辑审计] BGM 错分类: %s，应为 %s，当前为 %s",
                item["bank"],
                item["expected"],
                item["actual"],
            )
        for item in report["untracked"]:
            logger.info("[专辑审计] 配置表之外的 BGM 归入未分类: %s", item["bank"])

    def _is_audio_staging_root(self, root):
        """临时解包目录只允许在最终复制完成后统一清理。"""
        relative = os.path.relpath(root, self.audio_output_dir).replace("\\", "/")
        return relative == ".debank-temp" or relative.startswith(".debank-temp/")

    def _normalize_voice_audio_files(self, bank_stem, rel_path, audio_files):
        """将提取器对同名事件生成的连续后缀归一化为标准事件编号。

        只处理同一个 voice bank 内完整出现的 ``battle_hit_01_1..N`` 集合，
        因此不会改动正常的 ``battle_hit_01/02/03`` 或其他后缀命名。
        """
        if not self._audio_language(rel_path) or not str(bank_stem).isdigit():
            return audio_files

        audio_files = self._normalize_uniform_voice_prefix(bank_stem, audio_files)

        pattern = re.compile(
            rf"^{re.escape(str(bank_stem))}_battle_hit_01_(\d+)(\.[^.]+)$",
            re.IGNORECASE,
        )
        matches = []
        existing_names = {os.path.basename(path).lower() for path in audio_files}
        for path in audio_files:
            match = pattern.match(os.path.basename(path))
            if match:
                matches.append((int(match.group(1)), path, match.group(2)))
        if len(matches) < 2:
            return audio_files

        indexes = sorted(index for index, _path, _ext in matches)
        if indexes != list(range(1, len(indexes) + 1)):
            return audio_files

        updated = []
        for index, path, extension in matches:
            target_name = f"{bank_stem}_battle_hit_{index:02d}{extension}"
            if target_name.lower() in existing_names:
                return audio_files
            target = os.path.join(os.path.dirname(path), target_name)
            try:
                os.replace(path, target)
                updated.append((path, target))
            except OSError as exc:
                logger.warning("归一化语音文件名失败: %s -> %s (%s)", path, target, exc)
                for _old, new_path in updated:
                    try:
                        os.replace(new_path, _old)
                    except OSError:
                        pass
                return audio_files

        renamed = {old: new for old, new in updated}
        return [renamed.get(path, path) for path in audio_files]

    def _normalize_uniform_voice_prefix(self, bank_stem, audio_files):
        """修正 bank 内统一但与外层角色 ID 不一致的历史事件名前缀。

        仅在一个 bank 的所有音频都使用同一个外部数字前缀，且该前缀与
        bank 文件名不同的情况下执行。混合前缀或已有目标文件时保留原名，
        避免把共享音频误归属到当前角色。
        """
        prefix_pattern = re.compile(r"^(\d+)_")
        matches = []
        prefixes = set()
        for path in audio_files:
            match = prefix_pattern.match(os.path.basename(path))
            if not match:
                return audio_files
            prefixes.add(match.group(1))
            matches.append((path, match.group(1)))

        if len(prefixes) != 1 or str(bank_stem) in prefixes:
            return audio_files

        existing_names = {os.path.basename(path).lower() for path in audio_files}
        renamed = []
        for path, prefix in matches:
            name = os.path.basename(path)
            target_name = f"{bank_stem}_{name[len(prefix) + 1:]}"
            if target_name.lower() in existing_names:
                return audio_files
            renamed.append((path, os.path.join(os.path.dirname(path), target_name)))

        completed = []
        try:
            for old_path, new_path in renamed:
                os.replace(old_path, new_path)
                completed.append((old_path, new_path))
        except OSError as exc:
            logger.warning("语音 bank 前缀归一化失败: %s (%s)", bank_stem, exc)
            for old_path, new_path in reversed(completed):
                try:
                    os.replace(new_path, old_path)
                except OSError:
                    pass
            return audio_files

        logger.info(
            "语音 bank 内部前缀归一化: %s -> %s (%d 个文件)",
            next(iter(prefixes)),
            bank_stem,
            len(renamed),
        )
        renamed_map = dict(renamed)
        return [renamed_map.get(path, path) for path in audio_files]

    def _remove_mismatched_voice_files(self, expected_dir, bank_stem, filenames):
        """清理上一次导出遗留在当前角色目录中的其他角色文件。"""
        expected_prefix = f"{bank_stem}_"
        if not filenames or not all(os.path.basename(name).startswith(expected_prefix) for name in filenames):
            return
        if not os.path.isdir(expected_dir):
            return
        for filename in os.listdir(expected_dir):
            if not filename.lower().endswith((".wav", ".ogg", ".mp3")):
                continue
            if re.match(r"^\d+_", filename) and not filename.startswith(expected_prefix):
                try:
                    path = os.path.join(expected_dir, filename)
                    os.remove(path)
                    self._discard_audio_path(path)
                    logger.info("移除语音目录中的错位文件: %s", path)
                except OSError as exc:
                    logger.warning("移除语音错位文件失败: %s (%s)", filename, exc)

    def _remove_legacy_voice_aliases(self, expected_dir, bank_stem, filenames):
        """删除旧版本对重复 battle_hit 文件生成的 `_1/_2/_3` 别名。"""
        if not os.path.isdir(expected_dir):
            return
        if not any(
            re.match(rf"^{re.escape(str(bank_stem))}_battle_hit_0[1-3]\.", filename, re.IGNORECASE)
            for filename in filenames
        ):
            return
        prefix = f"{bank_stem}_battle_hit_01_"
        for filename in os.listdir(expected_dir):
            if filename.startswith(prefix) and filename.lower().endswith((".wav", ".ogg", ".mp3")):
                try:
                    path = os.path.join(expected_dir, filename)
                    os.remove(path)
                    self._discard_audio_path(path)
                    logger.info("移除旧语音别名: %s", path)
                except OSError as exc:
                    logger.warning("移除旧语音别名失败: %s (%s)", filename, exc)

    def _audio_language(self, rel_path):
        parts = rel_path.replace("\\", "/").lower().split("/")
        if "voice_cn" in parts:
            return "cn"
        if "voice_jp" in parts:
            return "jp"
        return None

    def _is_same_voice_scope(self, root, bank_stem, language):
        relative = os.path.relpath(root, self.audio_output_dir).replace("\\", "/").lower()
        expected = f"voice/{str(bank_stem).lower()}/{language}"
        return relative == expected

    def _cleanup_material_audio(self):
        audio_dir = os.path.join(self.material_dir, "assets", "fmodassets")
        if not os.path.isdir(audio_dir):
            return
        shutil.rmtree(audio_dir, ignore_errors=True)
        os.makedirs(audio_dir, exist_ok=True)
        logger.info("已清理音频临时目录: %s", audio_dir)

    def _cleanup_debank_input(self):
        input_dir = os.path.join(self.debank_dir, "input")
        if os.path.isdir(input_dir):
            shutil.rmtree(input_dir, ignore_errors=True)
