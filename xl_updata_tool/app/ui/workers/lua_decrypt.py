# -*- coding: utf-8 -*-
"""Lua 字节码批量解密工作线程"""

import os
import re
import subprocess

from PySide6.QtCore import QThread, Signal

from app.core.logger import logger
from app.core.path_utils import DATA_DIR, get_tools_dir


FIXED_HEAD = (b'\x1B\x4C\x75\x61\x54\x00\x19\x93\x0D\x0A\x1A\x0A\x04\x08\x08\x78'
              b'\x56\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x28\x77\x40\x01')


class LuaDecryptWorker(QThread):
    """Lua 字节码批量解密工作线程

    递归扫描 data/material/assets/lua/ 目录，处理所有 .lua、.lua.bank 和 .lua.bytes 文件：
    1. 头部修复（替换为 FIXED_HEAD）
    2. 反编译（调用 unluac.jar）
    3. 中文转义解码（\\xxx 序列 → UTF-8）
    4. 原地覆盖原文件
    .lua.bytes 文件会先去除 .bytes 后缀重命名为 .lua，再执行反编译。
    """
    progress = Signal(str)          # 进度消息
    finished = Signal(int, int)     # 成功数, 失败数
    error = Signal(str)             # 错误信息
    file_done = Signal(str)         # 单个文件解密完成，传递文件名

    def __init__(self, lua_dir, unluac_path, opmap_path, parent=None):
        super().__init__(parent)
        self.lua_dir = lua_dir
        self.unluac_path = unluac_path
        self.opmap_path = opmap_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self._decrypt_all()
        except Exception as e:
            logger.error(f"Lua 解密线程异常: {e}", exc_info=True)
            self.error.emit(str(e))

    def _decrypt_all(self):
        """遍历目录，处理所有符合条件的 Lua 文件"""
        # 收集所有待处理文件，将 BaseWord_cn.lua 和 BaseCard.lua 优先
        priority_files = []
        other_files = []
        if os.path.isdir(self.lua_dir):
            for root, dirs, files in os.walk(self.lua_dir):
                for f in files:
                    # 排除已处理过的 .lua.bank.lua 文件
                    if f.endswith('.lua.bank.lua'):
                        continue
                    if f.endswith('.lua') or f.endswith('.lua.bank') or f.endswith('.lua.bytes'):
                        path = os.path.join(root, f)
                        # 优先级识别（兼容 .lua.bank / .lua.bytes 变体）
                        base = f
                        if base.endswith('.bytes'):
                            base = base[:-len('.bytes')]
                        if base.endswith('.bank'):
                            base = base[:-len('.bank')]
                        base_match = base in ('BaseWord_cn.lua', 'BaseCard.lua')
                        if base_match:
                            priority_files.append(path)
                        else:
                            other_files.append(path)
        all_files = priority_files + other_files

        total = len(all_files)
        if total == 0:
            logger.info("Lua 目录无待处理文件，跳过解密")
            self.finished.emit(0, 0)
            return

        logger.info(f"Lua 解密开始: 共 {total} 个文件")
        self.progress.emit(f"正在解密 Lua: 0/{total}")

        success_count = 0
        fail_count = 0

        for i, filepath in enumerate(all_files):
            if self._cancelled:
                break

            self.progress.emit(f"正在解密 Lua: {i+1}/{total}")
            fname = os.path.basename(filepath)
            is_bank = fname.endswith('.lua.bank')
            is_bytes = fname.endswith('.lua.bytes')

            # 检查是否已经是可读文本 Lua（已解密），跳过处理
            if not is_bank and not is_bytes:
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        first_line = f.readline()
                        if first_line and (first_line.startswith('local') or first_line.startswith('--') or first_line.startswith('function')):
                            logger.debug(f"已解密，跳过: {fname}")
                            success_count += 1
                            continue
                except Exception:
                    pass

            # 重试 1 次
            ok = False
            for attempt in range(2):
                try:
                    ok = self._process_single_file(filepath)
                    if ok:
                        break
                except Exception as e:
                    logger.error(f"Lua 处理异常 (第{attempt+1}次) {fname}: {e}")

            if ok:
                success_count += 1
                logger.info(f"Lua 解密成功: {fname}")
            else:
                fail_count += 1
                logger.warning(f"Lua 解密失败（已跳过）: {fname}")

            self.progress.emit(f"正在解密 Lua: {i+1}/{total}（成功: {success_count}）")

        self.progress.emit(f"Lua 解密完成: 成功 {success_count} 个, 失败 {fail_count} 个")
        logger.info(f"Lua 解密完成: 成功 {success_count}, 失败 {fail_count}, 共 {total}")
        self.finished.emit(success_count, fail_count)

    def _process_single_file(self, filepath):
        """处理单个文件：头部修复 → 反编译 → 中文转义解码"""
        fname = os.path.basename(filepath)

        # 处理 .lua.bytes：先去除 .bytes 后缀重命名为 .lua，再走正常解密流程
        if fname.endswith('.lua.bytes'):
            new_path = filepath[:-len('.bytes')]  # 去掉 .bytes，得到 .lua
            # 若目标 .lua 已存在且非空，跳过该 .lua.bytes 文件，避免覆盖有效文件
            if os.path.exists(new_path) and os.path.getsize(new_path) > 0:
                logger.info(f"跳过 .lua.bytes（目标 .lua 已存在且非空）: {fname}")
                return False
            try:
                os.rename(filepath, new_path)
                logger.info(f"重命名 .lua.bytes → .lua: {fname}")
                filepath = new_path
                fname = os.path.basename(filepath)
            except Exception as e:
                logger.error(f"重命名 .lua.bytes → .lua 失败: {fname}: {e}")
                return False

        is_bank = fname.endswith('.lua.bank')
        out_dir = os.path.dirname(filepath)

        # 输出文件名：去除 .bank 后缀
        if is_bank:
            out_name = fname[:-len('.bank')]  # .lua.bank → .lua
        else:
            out_name = fname

        # 临时文件路径
        temp_fixed = os.path.join(out_dir, f'__tmp_fixed_{out_name}')
        temp_decomp = os.path.join(out_dir, f'__tmp_decomp_{out_name}')

        try:
            # Step 1: 头部修复
            with open(filepath, 'rb') as f:
                data = f.read()
            end = data.find(b'\x28\x77\x40\x01')
            if end == -1:
                logger.error(f"无效的 luac 文件（未找到头部特征标记）: {filepath}")
                return False
            head_len = end + 4
            fixed_data = FIXED_HEAD + data[head_len:]
            with open(temp_fixed, 'wb') as f:
                f.write(fixed_data)

            # Step 2: 反编译
            java_cmd = [
                'java', '-jar', self.unluac_path,
                temp_fixed,
                '-o', temp_decomp,
                '--opmap', self.opmap_path
            ]
            result = subprocess.run(java_cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                stderr = result.stderr.decode('utf-8', errors='replace')
                logger.error(f"反编译失败: {fname}, 返回码: {result.returncode}, stderr: {stderr[:200]}")
                return False

            # Step 3: 中文转义解码
            # 将 \xxx 连续转义序列解码为 UTF-8 中文
            pattern = re.compile(r'(\\(\d{3}))+')
            with open(temp_decomp, 'r', encoding='utf-8') as f:
                content = f.read()

            def replace_long_match(match):
                codes = match.group().split('\\')[1:]
                byte_values = [int(code) for code in codes]
                byte_sequence = bytes(byte_values)
                try:
                    return byte_sequence.decode('utf-8')
                except UnicodeDecodeError:
                    return match.group()

            decoded_content = pattern.sub(replace_long_match, content)

            # Step 4: 原地覆盖
            out_path = os.path.join(out_dir, out_name)
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(decoded_content)

            # 如果原文件是 .lua.bank，删除原文件
            if is_bank:
                os.remove(filepath)

            # 通知主线程关键文件已完成
            if fname in ('BaseWord_cn.lua', 'BaseCard.lua'):
                self.file_done.emit(fname)

            return True

        except subprocess.TimeoutExpired:
            logger.error(f"反编译超时: {fname}")
            return False
        except FileNotFoundError as e:
            logger.error(f"未找到 Java 运行时: {e}")
            self.error.emit("未找到 Java 运行时，请确保 Java 已安装并加入 PATH")
            return False
        except Exception as e:
            logger.error(f"处理文件失败 {fname}: {e}")
            return False
        finally:
            # 清理临时文件
            for tmp in [temp_fixed, temp_decomp]:
                if os.path.isfile(tmp):
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass