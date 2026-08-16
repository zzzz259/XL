# -*- coding: utf-8 -*-
"""音频解密工作线程（.bytes → .bank → 解密 → output/audio/）"""

import os
import re
import shutil
import subprocess
import sys

from PySide6.QtCore import QThread, Signal

from app.core.logger import logger, timed
from app.core.path_utils import DATA_DIR, get_base_dir, get_tools_dir
from app.core.album_map import build_album_map


class AudioDecryptWorker(QThread):
    """音频解密工作线程（.bytes → .bank → 解密 → output/audio/）

    在后台线程执行 epic7_debank.py 解密，避免阻塞 UI。
    支持去重：force=False 时若 output/audio/ 已有音频文件则跳过解密。
    """
    progress = Signal(str)
    progress_value = Signal(int, int, str)  # current, total, message
    finished_decrypt = Signal()
    error = Signal(str)

    def __init__(self, material_dir, audio_output_dir, debank_dir, force=False, parent=None):
        super().__init__(parent)
        self.material_dir = material_dir
        self.audio_output_dir = audio_output_dir
        self.debank_dir = debank_dir
        self.force = force
        self._cancelled = False
        self._album_map = {}

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            logger.info(f"音频解密开始：material={self.material_dir}, output={self.audio_output_dir}, force={self.force}")
            # 步骤 1：转换 .bytes → .bank
            self.progress.emit("正在转换 .bytes 文件...")
            self._convert_bytes_to_bank()

            if self._cancelled:
                self.finished_decrypt.emit()
                return

            # 步骤 2：解密 .bank 文件
            self._decrypt_bank_files()

            self.finished_decrypt.emit()
        except Exception as e:
            logger.error(f"音频解密线程异常: {e}", exc_info=True)
            self.error.emit(str(e))

    @timed("音频-转换bytes")
    def _convert_bytes_to_bank(self):
        """扫描 data/material/ 目录，将符合条件的 .bytes 文件重命名为 .bank，并复制到解密工具的 input 目录

        筛选规则（两者需同时满足）：
        1. 文件所在路径包含 fmodassets/ 子目录
        2. 语音文件（btl/system）文件名纯数字，或 bgm 文件位于 bgm/ 子目录下
        """
        debank_input = os.path.join(self.debank_dir, "input")

        if not os.path.isdir(self.material_dir):
            logger.warning(f"素材目录不存在，跳过转换: {self.material_dir}")
            return 0

        os.makedirs(debank_input, exist_ok=True)
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

                # 复制到解密工具 input 目录（去重：大小一致则跳过）
                dest = os.path.join(debank_input, bank_name)
                try:
                    if os.path.exists(dest) and os.path.getsize(dest) == os.path.getsize(bank_path):
                        logger.debug(f"跳过已存在且大小一致的文件: {bank_name}")
                    else:
                        shutil.copy2(bank_path, dest)
                except (OSError, PermissionError) as e:
                    logger.error(f"复制到解密目录失败 {bank_name}: {e}")

        logger.info(f"扫描到音频 .bytes 文件: {count} 个（跳过 {skipped} 个非音频文件）")
        if count > 0:
            self.progress.emit(f"已转换 {count} 个 .bytes → .bank")
        return count

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

        # 去重检查：force=False 时若 output/audio/ 已有音频文件则跳过解密（递归扫描子目录）
        if not self.force and os.path.isdir(self.audio_output_dir):
            existing_audio = 0
            for root, dirs, files in os.walk(self.audio_output_dir):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in (".wav", ".ogg", ".mp3") and os.path.getsize(os.path.join(root, f)) > 0:
                        existing_audio += 1
            if existing_audio > 0:
                logger.info(f"output/audio/ 已有 {existing_audio} 个音频文件，跳过解密")
                self.progress.emit(f"已有 {existing_audio} 个音频文件，跳过解密")
                return

        self.progress.emit(f"正在解密 {bank_count} 个 .bank 文件...")

        try:
            os.makedirs(self.audio_output_dir, exist_ok=True)
            # 构建 bgm 文件名 -> 专辑名 映射（复用一键解包反编译的 lua）
            self._album_map = build_album_map(os.path.join(self.material_dir, "assets", "lua"))
            # 将 epic7_debank 目录加入 sys.path 后导入调用，避免打包后 subprocess 递归启动 EXE
            if self.debank_dir not in sys.path:
                sys.path.insert(0, self.debank_dir)
            import epic7_debank
            epic7_debank.run(
                self.material_dir, self.audio_output_dir,
                progress_callback=lambda c, t: self.progress_value.emit(c, t, f"正在解密 .bank: {c}/{t}"),
                subdir_fn=self._audio_subdir,
            )
            # 统计输出目录中的音频文件数（递归扫描子目录）
            audio_count = 0
            for root, dirs, files in os.walk(self.audio_output_dir):
                for f in files:
                    if os.path.splitext(f)[1].lower() in (".wav", ".ogg", ".mp3"):
                        audio_count += 1
            self.progress.emit(f"解密完成: 提取 {audio_count} 个音频文件")
            logger.info(f"解密完成: 输出 {audio_count} 个音频文件到 {self.audio_output_dir}")

        except Exception as e:
            logger.error(f"解密异常: {e}", exc_info=True)
            self.error.emit(f"解密异常: {e}")

    def _audio_subdir(self, rel_path, bank_stem):
        """决定单个 .bank 解包出的音频输出到哪个子目录。

        语音：voice/<数字ID>/<cn|jp>/
        专辑：album/<专辑名>/（未匹配到专辑的 bgm 归入 未分类）
        """
        parts = rel_path.replace("\\", "/").split("/")
        if "bgm" in parts:
            album = self._album_map.get(bank_stem, "未分类") if self._album_map else "未分类"
            return os.path.join("album", album)
        lang = "jp" if "voice_jp" in parts else "cn"
        return os.path.join("voice", bank_stem, lang)