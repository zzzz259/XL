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
import time

from PySide6.QtCore import QThread, Signal

from app.platform.diagnostics import logger, task_operation
from app.platform.processes import run_external_process, update_process_manifest
from app.platform.tool_locator import ToolLocator


def _java_exe():
    """经 ToolLocator 解析 Java；冻结环境只允许 bundled 运行时。"""
    return ToolLocator.create().java()


FIXED_HEAD = (b'\x1B\x4C\x75\x61\x54\x00\x19\x93\x0D\x0A\x1A\x0A\x04\x08\x08\x78'
              b'\x56\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x28\x77\x40\x01')
MARKER = b'\x28\x77\x40\x01'

# 角色数据解析依赖这两个文件，反编译完成后尽早通知
PRIORITY_NAMES = ('BaseWord_cn.lua', 'BaseCard.lua')
BATCH_TIMEOUT_SECONDS = 1800
BATCH_PREP_PROGRESS_EVERY = 100


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
    logger.info("Lua 文件扫描完成 directory=%s candidates=%s", lua_dir, len(candidates), extra={"event": "lua.scan.complete", "details": {"directory": lua_dir, "candidate_count": len(candidates), "sample": [path for path, _name in candidates[:20]]}})

    plain_candidates = [(src, name) for src, name in candidates if not name.endswith(('.lua.bank', '.lua.bytes'))]
    binary_candidates = [(src, name) for src, name in candidates if name.endswith(('.lua.bank', '.lua.bytes'))]
    scan_total = len(plain_candidates) + len(binary_candidates)
    scan_done = 0
    scan_last_report = time.monotonic()
    emit(f"识别 Lua 文件: 0/{scan_total}")

    def advance_scan():
        nonlocal scan_done, scan_last_report
        scan_done += 1
        now = time.monotonic()
        if scan_done == scan_total or scan_done % BATCH_PREP_PROGRESS_EVERY == 0 or now - scan_last_report >= 0.2:
            emit(f"识别 Lua 文件: {scan_done}/{scan_total}")
            scan_last_report = now

    def read(src):
        with open(src, 'rb') as fh:
            return fh.read()

    done = set()          # 已存在的明文 .lua 目标名
    bytecode = {}         # 目标名 -> (源路径, 修复后字节码)
    rename = []           # (源路径, 目标名) 明文但需重命名的文件
    success = 0
    unknown = 0
    plaintext_count = 0
    bytecode_count = 0
    deduplicated_count = 0

    # 第一遍：处理纯 .lua 文件（已解密的优先）
    for src, f in plain_candidates:
        if cancelled():
            logger.info("Lua 文件识别阶段取消 completed=%s total=%s", scan_done, scan_total, extra={"event": "lua.scan.cancelled", "details": {"completed": scan_done, "total": scan_total}})
            return success, 0
        try:
            data = read(src)
        except Exception as e:
            logger.error(f"读取失败: {f}: {e}")
            logger.exception("Lua 文件读取失败 source=%s", src, extra={"event": "lua.file.failed", "error_code": "LUA_INPUT_READ_FAILED", "details": {"source": src, "pass": "plaintext_scan"}})
            unknown += 1
            advance_scan()
            continue
        kind, processed = classify(data)
        if kind == 'plaintext':
            done.add(f)
            success += 1
            plaintext_count += 1
        elif kind == 'bytecode':
            bytecode[f] = (src, processed)
            bytecode_count += 1
        else:
            unknown += 1
        advance_scan()

    # 第二遍：处理 .lua.bank / .lua.bytes
    for src, f in binary_candidates:
        if cancelled():
            logger.info("Lua 文件识别阶段取消 completed=%s total=%s", scan_done, scan_total, extra={"event": "lua.scan.cancelled", "details": {"completed": scan_done, "total": scan_total}})
            return success, 0
        tgt = target_name(f)
        if tgt in done:
            deduplicated_count += 1
            advance_scan()
            continue  # 已有明文 .lua，跳过
        try:
            data = read(src)
        except Exception as e:
            logger.error(f"读取失败: {f}: {e}")
            logger.exception("Lua 文件读取失败 source=%s", src, extra={"event": "lua.file.failed", "error_code": "LUA_INPUT_READ_FAILED", "details": {"source": src, "pass": "bytecode_scan"}})
            unknown += 1
            advance_scan()
            continue
        kind, processed = classify(data)
        if kind == 'plaintext':
            rename.append((src, tgt))
            plaintext_count += 1
        elif kind == 'bytecode':
            bytecode[tgt] = (src, processed)
            bytecode_count += 1
        else:
            unknown += 1
        advance_scan()

    logger.info(
        "Lua 文件识别汇总 candidates=%s plaintext=%s bytecode=%s unknown=%s deduplicated=%s",
        scan_total,
        plaintext_count,
        bytecode_count,
        unknown,
        deduplicated_count,
        extra={
            "event": "lua.scan.classification.complete",
            "details": {
                "candidate_count": scan_total,
                "plaintext_count": plaintext_count,
                "bytecode_count": bytecode_count,
                "unknown_count": unknown,
                "deduplicated_count": deduplicated_count,
            },
        },
    )

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
            logger.info("Lua 明文文件转换开始 source=%s target=%s", src, os.path.join(lua_dir, tgt), extra={"event": "lua.file.start", "details": {"source": src, "target": os.path.join(lua_dir, tgt), "operation": "rename_decode"}})
            data = read(src)
            text = decode_chinese(data.decode('utf-8', errors='replace'))
            dst = os.path.join(lua_dir, tgt)
            with open(dst, 'w', encoding='utf-8') as fh:
                fh.write(text)
            if src != dst and os.path.isfile(src):
                os.remove(src)
            success += 1
            notify(tgt)
            logger.info("Lua 明文文件转换完成 source=%s output=%s bytes=%s", src, dst, os.path.getsize(dst), extra={"event": "lua.file.complete", "details": {"source": src, "output": dst, "bytes": os.path.getsize(dst), "operation": "rename_decode"}})
        except Exception as e:
            fail += 1
            logger.error(f"明文重命名失败 {tgt}: {e}")
            logger.exception("Lua 明文文件转换失败 source=%s target=%s", src, tgt, extra={"event": "lua.file.failed", "error_code": "LUA_RENAME_DECODE_FAILED", "details": {"source": src, "target": tgt}})

    # 字节码批量反编译
    if bytecode and not cancelled():
        s, f = _decompile_batch(lua_dir, bytecode, unluac_path, opmap_path,
                                emit, notify, cancelled)
        success += s
        fail += f

    if cancelled():
        logger.warning("Lua 反编译已取消 success=%s failed=%s pending=%s", success, fail, len(bytecode), extra={"event": "lua.decompile.cancelled", "details": {"success": success, "failed": fail, "pending_bytecode": len(bytecode)}})
        return success, fail

    emit(f"Lua 解密完成: 成功 {success} 个, 失败 {fail} 个")
    logger.info(f"Lua 解密完成: 成功 {success}, 失败 {fail}, 共 {total}")
    return success, fail


def _decompile_batch(lua_dir, bytecode, unluac_path, opmap_path,
                     emit, notify, cancelled):
    """单 JVM 批量反编译 bytecode，写回并中文解码。返回 (成功数, 失败数)"""
    tmp_root = tempfile.mkdtemp(prefix=".xl-lua-decompile-", dir=os.path.dirname(lua_dir) or None)
    fixed_dir = os.path.join(tmp_root, "binary")
    out_dir = os.path.join(tmp_root, "out")
    os.makedirs(fixed_dir)
    os.makedirs(out_dir)
    try:
        # 临时输入准备也必须可见、可取消；大版本可能包含上万份 Lua。
        total = len(bytecode)
        emit(f"准备反编译 Lua: 0/{total}")
        prep_started = time.monotonic()
        last_report = prep_started
        for index, (target, (_src, fixed)) in enumerate(bytecode.items(), start=1):
            if cancelled():
                logger.info("Lua 批量输入准备取消 completed=%s total=%s", index - 1, total, extra={"event": "lua.batch_prepare.cancelled", "details": {"completed": index - 1, "total": total}})
                return 0, 0
            with open(os.path.join(fixed_dir, target), 'wb') as fh:
                fh.write(fixed)
            now = time.monotonic()
            if index == total or index % BATCH_PREP_PROGRESS_EVERY == 0 or now - last_report >= 0.2:
                emit(f"准备反编译 Lua: {index}/{total}")
                last_report = now

        logger.info("Lua 批量输入准备完成 files=%s elapsed_ms=%.1f", total, (time.monotonic() - prep_started) * 1000, extra={"event": "lua.batch_prepare.complete", "details": {"files": total, "elapsed_ms": round((time.monotonic() - prep_started) * 1000, 1)}})
        if cancelled():
            return 0, 0

        # 批量反编译（单 JVM，多线程）
        cmd = [_java_exe(), '-jar', unluac_path, fixed_dir,
               '--output', out_dir, '--opmap', opmap_path]
        emit(f"正在反编译 Lua: 0/{total}")
        proc = _run_batch(cmd, emit, cancelled)
        if getattr(proc, "xl_cancelled", False) or cancelled():
            logger.warning("Lua 批量反编译子进程已取消 command_id=%s", getattr(proc, "xl_command_id", "-"), extra={"event": "lua.batch_decompile.cancelled", "details": {"command_id": getattr(proc, "xl_command_id", "-")}})
            return 0, 0

        success = 0
        fail = 0
        for target, (src, fixed) in bytecode.items():
            if cancelled():
                break
            decomp = os.path.join(out_dir, target)
            if not (os.path.isfile(decomp) and os.path.getsize(decomp) > 0):
                # 反编译失败：单文件重试 1 次
                logger.warning("Lua 批量反编译没有生成文件，开始单文件重试 source=%s target=%s expected=%s", src, target, decomp, extra={"event": "lua.file.retry", "details": {"source": src, "target": target, "expected_output": decomp, "retry": "single"}})
                if not _decompile_single(fixed, decomp, unluac_path, opmap_path):
                    fail += 1
                    logger.warning(f"Lua 解密失败（写入空占位）: {target}")
                    _write_stub(lua_dir, target, src)
                    logger.error("Lua 文件反编译失败 source=%s output=%s", src, os.path.join(lua_dir, target), extra={"event": "lua.file.failed", "error_code": "LUA_DECOMPILE_FAILED", "details": {"source": src, "target": target, "batch_output": decomp, "stub_written": os.path.isfile(os.path.join(lua_dir, target))}})
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
                logger.info("Lua 字节码反编译完成 source=%s output=%s bytes=%s", src, dst, os.path.getsize(dst), extra={"event": "lua.file.complete", "details": {"source": src, "output": dst, "bytes": os.path.getsize(dst), "operation": "bytecode_decompile"}})
            except Exception as e:
                fail += 1
                logger.error(f"写回失败 {target}: {e}")
                logger.exception("Lua 反编译结果写回失败 source=%s decoded=%s target=%s", src, decomp, target, extra={"event": "lua.file.failed", "error_code": "LUA_OUTPUT_WRITE_FAILED", "details": {"source": src, "decoded_output": decomp, "target": target}})
        return success, fail
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _run_batch(cmd, emit, cancel_check=None):
    """运行批量反编译命令，解析 stderr 的 PROGRESS/FAILED"""
    def on_line(stream, line):
        if stream != "stderr":
            return
        line = line.strip()
        if line.startswith("PROGRESS "):
            try:
                done, total = map(int, line.split()[1].split("/"))
                emit(f"正在反编译 Lua: {done}/{total}")
            except (ValueError, IndexError):
                pass
        elif line.startswith("FAILED "):
            logger.warning(f"批量反编译失败: {line}")

    try:
        proc = run_external_process(
            cmd,
            tool="unluac-batch",
            on_line=on_line,
            text=True,
            timeout=BATCH_TIMEOUT_SECONDS,
            cancel_check=cancel_check,
        )
    except subprocess.TimeoutExpired:
        logger.exception(
            "unluac 批量反编译超时 timeout_seconds=%s",
            BATCH_TIMEOUT_SECONDS,
            extra={"event": "lua.batch_decompile.timeout", "error_code": "LUA_BATCH_TIMEOUT", "details": {"timeout_seconds": BATCH_TIMEOUT_SECONDS}},
        )
        raise
    if proc.returncode != 0:
        logger.error("unluac 批量反编译进程失败 exit_code=%s command_id=%s", proc.returncode, proc.xl_command_id)
    return proc


def _decompile_single(fixed_data, out_path, unluac_path, opmap_path):
    """单文件反编译（用于批量失败后的重试），返回是否成功"""
    with tempfile.NamedTemporaryFile(suffix='.lua', delete=False) as fh:
        fh.write(fixed_data)
        tmp_in = fh.name
    try:
        cmd = [_java_exe(), '-jar', unluac_path, tmp_in,
               '--output', out_path, '--opmap', opmap_path]
        proc = run_external_process(cmd, tool="unluac-single", capture_output=True, timeout=120)
        verified = proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0
        update_process_manifest(proc, requested_output=out_path, output_verified=verified, output_bytes=os.path.getsize(out_path) if os.path.isfile(out_path) else 0, business_outcome="success" if verified else "failed", error_code=None if verified else "LUA_SINGLE_OUTPUT_NOT_VERIFIED")
        logger.info("Lua 单文件反编译验证 source_temp=%s output=%s exit_code=%s verified=%s", tmp_in, out_path, proc.returncode, verified, extra={"event": "lua.single_decompile.verified", "details": {"command_id": getattr(proc, "xl_command_id", "-"), "output": out_path, "exit_code": proc.returncode, "verified": verified, "output_bytes": os.path.getsize(out_path) if os.path.isfile(out_path) else 0}})
        return verified
    except subprocess.TimeoutExpired:
        logger.exception("Lua 单文件反编译超时 output=%s", out_path, extra={"event": "lua.single_decompile.failed", "error_code": "LUA_SINGLE_TIMEOUT", "details": {"output": out_path}})
        return False
    except FileNotFoundError:
        logger.error("未找到 Java 运行时，请确保 Java 已安装并加入 PATH")
        return False
    except Exception as e:
        logger.error(f"单文件重试反编译异常: {e}")
        logger.exception("Lua 单文件反编译异常 output=%s", out_path, extra={"event": "lua.single_decompile.failed", "error_code": "LUA_SINGLE_FAILED", "details": {"output": out_path}})
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
        logger.exception("Lua 空占位文件写入失败 target=%s", target, extra={"event": "lua.stub.failed", "error_code": "LUA_STUB_WRITE_FAILED", "details": {"lua_dir": lua_dir, "target": target, "source": src}})


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
