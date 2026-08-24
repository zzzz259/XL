# -*- coding: utf-8 -*-
"""Lua 字节码批量反编译（单 JVM 多线程）

核心逻辑抽成模块级函数 decompile_lua_dir()，供两个调用方复用：
1. LuaDecryptWorker ——「角色」按钮触发的独立反编译（回退用）
2. ImportASWorker   —— 导入 AS 时顺带反编译（主力路径）

对比旧版：旧版逐文件 subprocess.run(java -jar unluac.jar ...)，
每个文件起一个 JVM（约 0.2 秒 × 13757 ≈ 十几分钟）。
新版单 JVM 多线程目录批量反编译，约 1 分钟内完成。
"""

import os
import re
import shutil
import subprocess
import tempfile

from PySide6.QtCore import QThread, Signal

from app.core.logger import logger
from app.core.task_context import task_operation


FIXED_HEAD = (b'\x1B\x4C\x75\x61\x54\x00\x19\x93\x0D\x0A\x1A\x0A\x04\x08\x08\x78'
              b'\x56\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x28\x77\x40\x01')
MARKER = b'\x28\x77\x40\x01'

# 角色数据解析依赖这两个文件，反编译完成后尽早通知
PRIORITY_NAMES = ('BaseWord_cn.lua', 'BaseCard.lua')


def classify(data):
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


def decode_chinese(content):
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


def target_name(fname):
    """从文件名得到输出 .lua 名（去掉 .bytes/.bank 后缀）"""
    base = fname
    if base.endswith('.bytes'):
        base = base[:-len('.bytes')]
    if base.endswith('.bank'):
        base = base[:-len('.bank')]
    return base


def decompile_lua_dir(lua_dir, unluac_path, opmap_path,
                      progress_cb=None, file_done_cb=None, cancel_check=None):
    """同步反编译整个 lua 目录，返回 (成功数, 失败数)

    progress_cb(msg)   —— 进度消息（字符串）
    file_done_cb(name) —— 关键文件（BaseWord_cn/BaseCard）完成时通知
    cancel_check()     —— 返回 True 表示取消
    """
    def emit(msg):
        if progress_cb:
            progress_cb(msg)

    def cancelled():
        return bool(cancel_check and cancel_check())

    def notify(name):
        if file_done_cb:
            file_done_cb(name)

    if not os.path.isdir(lua_dir):
        logger.info("Lua 目录不存在，跳过解密")
        return 0, 0

    # 收集候选文件
    candidates = []
    for root, _dirs, files in os.walk(lua_dir):
        for f in files:
            if f.endswith('.lua.bank.lua'):
                continue
            if f.endswith('.lua') or f.endswith('.lua.bank') or f.endswith('.lua.bytes'):
                candidates.append((os.path.join(root, f), f))

    def read(src):
        with open(src, 'rb') as fh:
            return fh.read()

    done = set()          # 已存在的明文 .lua 目标名
    bytecode = {}         # 目标名 -> (源路径, 修复后字节码)
    rename = []           # (源路径, 目标名) 明文但需重命名的文件
    success = 0
    unknown = 0

    # 第一遍：处理纯 .lua 文件（已解密的优先）
    for src, f in candidates:
        if f.endswith('.lua.bank') or f.endswith('.lua.bytes'):
            continue
        try:
            data = read(src)
        except Exception as e:
            logger.error(f"读取失败: {f}: {e}")
            unknown += 1
            continue
        kind, processed = classify(data)
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
        tgt = target_name(f)
        if tgt in done:
            continue  # 已有明文 .lua，跳过
        try:
            data = read(src)
        except Exception as e:
            logger.error(f"读取失败: {f}: {e}")
            unknown += 1
            continue
        kind, processed = classify(data)
        if kind == 'plaintext':
            rename.append((src, tgt))
        elif kind == 'bytecode':
            bytecode[tgt] = (src, processed)
        else:
            unknown += 1

    total = len(bytecode) + len(rename)
    if total == 0:
        logger.info("Lua 目录无待处理文件，跳过解密")
        return success, 0

    logger.info(f"Lua 解密开始: 字节码 {len(bytecode)} 个, 明文重命名 {len(rename)} 个, 已明文 {len(done)} 个")
    emit(f"正在解密 Lua: 0/{len(bytecode)}")

    fail = 0

    # 明文重命名（.lua.bytes / .lua.bank 的明文 → .lua）
    for src, tgt in rename:
        if cancelled():
            break
        try:
            data = read(src)
            text = decode_chinese(data.decode('utf-8', errors='replace'))
            dst = os.path.join(lua_dir, tgt)
            with open(dst, 'w', encoding='utf-8') as fh:
                fh.write(text)
            if src != dst and os.path.isfile(src):
                os.remove(src)
            success += 1
            notify(tgt)
        except Exception as e:
            fail += 1
            logger.error(f"明文重命名失败 {tgt}: {e}")

    # 字节码批量反编译
    if bytecode and not cancelled():
        s, f = _decompile_batch(lua_dir, bytecode, unluac_path, opmap_path,
                                emit, notify, cancelled)
        success += s
        fail += f

    emit(f"Lua 解密完成: 成功 {success} 个, 失败 {fail} 个")
    logger.info(f"Lua 解密完成: 成功 {success}, 失败 {fail}, 共 {total}")
    return success, fail


def _decompile_batch(lua_dir, bytecode, unluac_path, opmap_path,
                     emit, notify, cancelled):
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
        cmd = ['java', '-jar', unluac_path, fixed_dir,
               '--output', out_dir, '--opmap', opmap_path]
        _run_batch(cmd, emit)

        success = 0
        fail = 0
        for target, (src, fixed) in bytecode.items():
            if cancelled():
                break
            decomp = os.path.join(out_dir, target)
            if not (os.path.isfile(decomp) and os.path.getsize(decomp) > 0):
                # 反编译失败：单文件重试 1 次
                if not _decompile_single(fixed, decomp, unluac_path, opmap_path):
                    fail += 1
                    logger.warning(f"Lua 解密失败（写入空占位）: {target}")
                    _write_stub(lua_dir, target, src)
                    continue
            try:
                with open(decomp, 'r', encoding='utf-8') as fh:
                    content = fh.read()
                decoded = decode_chinese(content)
                dst = os.path.join(lua_dir, target)
                with open(dst, 'w', encoding='utf-8') as fh:
                    fh.write(decoded)
                if src != dst and os.path.isfile(src):
                    os.remove(src)
                success += 1
                notify(target)
            except Exception as e:
                fail += 1
                logger.error(f"写回失败 {target}: {e}")
        return success, fail
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _run_batch(cmd, emit):
    """运行批量反编译命令，解析 stderr 的 PROGRESS/FAILED"""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stderr:
        line = line.strip()
        if line.startswith("PROGRESS "):
            try:
                done, total = map(int, line.split()[1].split("/"))
                emit(f"正在反编译 Lua: {done}/{total}")
            except (ValueError, IndexError):
                pass
        elif line.startswith("FAILED "):
            logger.warning(f"批量反编译失败: {line}")
    proc.wait()


def _decompile_single(fixed_data, out_path, unluac_path, opmap_path):
    """单文件反编译（用于批量失败后的重试），返回是否成功"""
    with tempfile.NamedTemporaryFile(suffix='.lua', delete=False) as fh:
        fh.write(fixed_data)
        tmp_in = fh.name
    try:
        cmd = ['java', '-jar', unluac_path, tmp_in,
               '--output', out_path, '--opmap', opmap_path]
        subprocess.run(cmd, capture_output=True, timeout=120)
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
    except subprocess.TimeoutExpired:
        return False
    except FileNotFoundError:
        logger.error("未找到 Java 运行时，请确保 Java 已安装并加入 PATH")
        return False
    except Exception as e:
        logger.error(f"单文件重试反编译异常: {e}")
        return False
    finally:
        if os.path.isfile(tmp_in):
            os.remove(tmp_in)


def _write_stub(lua_dir, target, src):
    """反编译失败的文件写入空占位 .lua，删除原 .lua.bytes，避免残留导致重复处理"""
    try:
        dst = os.path.join(lua_dir, target)
        with open(dst, 'w', encoding='utf-8') as fh:
            fh.write("return {}\n")
        if src != dst and os.path.isfile(src):
            os.remove(src)
    except Exception as e:
        logger.error(f"写空占位失败 {target}: {e}")


class LuaDecryptWorker(QThread):
    """「角色」按钮触发的独立反编译线程（导入流程已反编译时不会走到这里）"""
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

    @task_operation("LUA", "lua", lambda self: {"lua_dir": self.lua_dir})
    def run(self):
        try:
            logger.info(f"Lua 反编译线程开始：{self.lua_dir}")
            success, fail = decompile_lua_dir(
                self.lua_dir, self.unluac_path, self.opmap_path,
                progress_cb=self.progress.emit,
                file_done_cb=self.file_done.emit,
                cancel_check=lambda: self._cancelled)
            logger.info(f"Lua 反编译完成：成功 {success}，失败 {fail}")
            self.finished.emit(success, fail)
        except Exception as e:
            logger.error(f"Lua 解密线程异常: {e}", exc_info=True)
            self.error.emit(str(e))
