# -*- coding: utf-8 -*-
"""Lua 字节码批量解密工作线程（单 JVM 多线程反编译）

对比旧实现：旧版逐文件 `subprocess.run(java -jar unluac.jar ...)`，
每个文件起一个 JVM（约 0.2 秒启动开销），13000+ 文件要 15 分钟。
新版改为「单 JVM + --threads 目录批量反编译」，一次启动处理全部文件，
并解析 stderr 的 PROGRESS 输出做进度反馈。
"""

import os
import re
import shutil
import subprocess
import tempfile

from PySide6.QtCore import QThread, Signal

from app.core.logger import logger


FIXED_HEAD = (b'\x1B\x4C\x75\x61\x54\x00\x19\x93\x0D\x0A\x1A\x0A\x04\x08\x08\x78'
              b'\x56\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x28\x77\x40\x01')
MARKER = b'\x28\x77\x40\x01'

# 角色数据解析依赖这两个文件，解密完成后尽早通知主线程
PRIORITY_NAMES = ('BaseWord_cn.lua', 'BaseCard.lua')


class LuaDecryptWorker(QThread):
    """Lua 字节码批量解密工作线程

    扫描 data/material/assets/lua/ 下的 .lua / .lua.bank / .lua.bytes 文件：
    1. 分类：明文直接复用；字节码修复头部后批量反编译
    2. 单 JVM 多线程批量反编译（unluac 目录模式 + --threads）
    3. 中文转义解码（\\xxx 序列 → UTF-8）
    4. 原地覆盖为 .lua（.lua.bytes / .lua.bank 处理后删除原文件）
    """
    progress = Signal(str)          # 进度消息
    finished = Signal(int, int)     # 成功数, 失败数
    error = Signal(str)             # 错误信息
    file_done = Signal(str)         # 单个关键文件解密完成，传递文件名

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

    # ---------- 纯逻辑辅助 ----------

    @staticmethod
    def _classify(data):
        """识别文件内容，返回 ('plaintext'|'bytecode'|'unknown', 处理后的数据)"""
        stripped = data.lstrip()
        if (stripped[:4] == b'--- ' or stripped[:4] == b'---@'
                or stripped[:8] == b'function' or stripped[:5] == b'local'):
            return 'plaintext', data
        m = data.find(MARKER)
        if m >= 16:
            # 坏 header（游戏篡改过），替换为标准 header
            return 'bytecode', FIXED_HEAD + data[m + 4:]
        if m >= 0:
            # 已修复（FIXED_HEAD 内含 marker），直接用
            return 'bytecode', data
        if data[:4] == b'\x1bLua':
            return 'bytecode', data
        return 'unknown', None

    @staticmethod
    def _decode_chinese(content):
        """将 \\xxx 连续转义序列解码为 UTF-8 中文"""
        pattern = re.compile(r'(\\(\d{3}))+')

        def replace_long_match(match):
            codes = match.group().split('\\')[1:]
            byte_values = [int(code) for code in codes]
            try:
                return bytes(byte_values).decode('utf-8')
            except UnicodeDecodeError:
                return match.group()

        return pattern.sub(replace_long_match, content)

    @staticmethod
    def _target_name(fname):
        """从文件名得到输出 .lua 名（去掉 .bytes/.bank 后缀）"""
        base = fname
        if base.endswith('.bytes'):
            base = base[:-len('.bytes')]
        if base.endswith('.bank'):
            base = base[:-len('.bank')]
        return base

    def _notify_priority(self, target):
        """关键文件（BaseWord_cn / BaseCard）完成时通知主线程"""
        if target in PRIORITY_NAMES:
            self.file_done.emit(target)

    # ---------- 主流程 ----------

    def _decrypt_all(self):
        if not os.path.isdir(self.lua_dir):
            logger.info("Lua 目录不存在，跳过解密")
            self.finished.emit(0, 0)
            return

        # 收集候选文件
        candidates = []
        for root, _dirs, files in os.walk(self.lua_dir):
            for f in files:
                if f.endswith('.lua.bank.lua'):
                    continue
                if f.endswith('.lua') or f.endswith('.lua.bank') or f.endswith('.lua.bytes'):
                    candidates.append((os.path.join(root, f), f))

        done = set()          # 已存在的明文 .lua 目标名
        bytecode = {}         # 目标名 -> (源路径, 修复后字节码)
        rename = []           # (源路径, 目标名) 明文但需重命名的文件
        success = 0
        unknown = 0

        def _read(src):
            with open(src, 'rb') as fh:
                return fh.read()

        # 第一遍：处理纯 .lua 文件（已解密的优先，避免被 .lua.bytes 覆盖）
        for src, f in candidates:
            if f.endswith('.lua.bank') or f.endswith('.lua.bytes'):
                continue
            try:
                data = _read(src)
            except Exception as e:
                logger.error(f"读取失败: {f}: {e}")
                unknown += 1
                continue
            kind, processed = self._classify(data)
            if kind == 'plaintext':
                done.add(f)
                success += 1
            elif kind == 'bytecode':
                bytecode[f] = (src, processed)
            else:
                unknown += 1

        # 第二遍：处理 .lua.bank / .lua.bytes
        for src, f in candidates:
            if not (f.endswith('.lua.bank') or f.endswith('.lua.bytes')):
                continue
            target = self._target_name(f)
            if target in done:
                continue  # 已有明文 .lua，跳过
            try:
                data = _read(src)
            except Exception as e:
                logger.error(f"读取失败: {f}: {e}")
                unknown += 1
                continue
            kind, processed = self._classify(data)
            if kind == 'plaintext':
                rename.append((src, target))
            elif kind == 'bytecode':
                bytecode[target] = (src, processed)
            else:
                unknown += 1

        total = len(bytecode) + len(rename)
        if total == 0:
            logger.info("Lua 目录无待处理文件，跳过解密")
            self.finished.emit(success, 0)
            return

        logger.info(f"Lua 解密开始: 字节码 {len(bytecode)} 个, 明文重命名 {len(rename)} 个, 已明文 {len(done)} 个")
        self.progress.emit(f"正在解密 Lua: 0/{len(bytecode)}")

        fail = 0

        # 明文重命名（.lua.bytes / .lua.bank 的明文 → .lua）
        for src, target in rename:
            if self._cancelled:
                break
            try:
                data = _read(src)
                text = self._decode_chinese(data.decode('utf-8', errors='replace'))
                dst = os.path.join(self.lua_dir, target)
                with open(dst, 'w', encoding='utf-8') as fh:
                    fh.write(text)
                if src != dst and os.path.isfile(src):
                    os.remove(src)
                success += 1
                self._notify_priority(target)
            except Exception as e:
                fail += 1
                logger.error(f"明文重命名失败 {target}: {e}")

        # 字节码批量反编译
        if bytecode and not self._cancelled:
            s, f = self._decompile_batch(bytecode)
            success += s
            fail += f

        self.progress.emit(f"Lua 解密完成: 成功 {success} 个, 失败 {fail} 个")
        logger.info(f"Lua 解密完成: 成功 {success}, 失败 {fail}, 共 {total}")
        self.finished.emit(success, fail)

    # ---------- 批量反编译 ----------

    def _decompile_batch(self, bytecode):
        """单 JVM 批量反编译 bytecode，写回并中文解码。返回 (成功数, 失败数)"""
        tmp_root = tempfile.mkdtemp(prefix="lua_decompile_")
        fixed_dir = os.path.join(tmp_root, "binary")
        out_dir = os.path.join(tmp_root, "out")
        os.makedirs(fixed_dir)
        os.makedirs(out_dir)
        try:
            # 写修复后字节码到临时目录
            for target, (_src, fixed) in bytecode.items():
                with open(os.path.join(fixed_dir, target), 'wb') as fh:
                    fh.write(fixed)

            # 批量反编译（单 JVM，多线程）
            cmd = ['java', '-jar', self.unluac_path, fixed_dir,
                   '--output', out_dir, '--opmap', self.opmap_path]
            self._run_batch(cmd)

            success = 0
            fail = 0
            for target, (src, fixed) in bytecode.items():
                if self._cancelled:
                    break
                decomp = os.path.join(out_dir, target)
                if not (os.path.isfile(decomp) and os.path.getsize(decomp) > 0):
                    # 反编译失败：单文件重试 1 次
                    if not self._decompile_single(fixed, decomp):
                        fail += 1
                        logger.warning(f"Lua 解密失败（已跳过）: {target}")
                        continue
                try:
                    with open(decomp, 'r', encoding='utf-8') as fh:
                        content = fh.read()
                    decoded = self._decode_chinese(content)
                    dst = os.path.join(self.lua_dir, target)
                    with open(dst, 'w', encoding='utf-8') as fh:
                        fh.write(decoded)
                    if src != dst and os.path.isfile(src):
                        os.remove(src)
                    success += 1
                    self._notify_priority(target)
                except Exception as e:
                    fail += 1
                    logger.error(f"写回失败 {target}: {e}")
            return success, fail
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    def _run_batch(self, cmd):
        """运行批量反编译命令，解析 stderr 的 PROGRESS/FAILED 做进度与日志"""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stderr:
            line = line.strip()
            if line.startswith("PROGRESS "):
                try:
                    done, total = map(int, line.split()[1].split("/"))
                    self.progress.emit(f"正在反编译 Lua: {done}/{total}")
                except (ValueError, IndexError):
                    pass
            elif line.startswith("FAILED "):
                logger.warning(f"批量反编译失败: {line}")
        proc.wait()

    def _decompile_single(self, fixed_data, out_path):
        """单文件反编译（用于批量失败后的重试），返回是否成功"""
        with tempfile.NamedTemporaryFile(suffix='.lua', delete=False) as fh:
            fh.write(fixed_data)
            tmp_in = fh.name
        try:
            cmd = ['java', '-jar', self.unluac_path, tmp_in,
                   '--output', out_path, '--opmap', self.opmap_path]
            subprocess.run(cmd, capture_output=True, timeout=120)
            return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
        except subprocess.TimeoutExpired:
            return False
        except FileNotFoundError:
            self.error.emit("未找到 Java 运行时，请确保 Java 已安装并加入 PATH")
            return False
        except Exception as e:
            logger.error(f"单文件重试反编译异常: {e}")
            return False
        finally:
            if os.path.isfile(tmp_in):
                os.remove(tmp_in)
