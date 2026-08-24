import argparse
import codecs
import concurrent.futures
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import psutil

try:
    from app.platform.diagnostics import logger
except ImportError:
    logger = None

try:
    from .fsb_fallback import vgmstream_path
except ImportError:
    from fsb_fallback import vgmstream_path


def _log(msg):
    """模块被导入时走 app 日志，独立运行时回退 print"""
    if logger is not None:
        logger.info(msg)
    else:
        print(msg)


def _debug(msg):
    if logger is not None:
        logger.debug(msg)
    else:
        print(msg)


DEFAULT_WORKERS = 6
AUDIO_EXTENSIONS = {".wav", ".ogg", ".mp3"}
BANK_STATE_FILENAME = ".bank_state.json"
BANK_CACHE_VERSION = 1

'''
  Epic7 Bank File Decryptor (v2.1)
  支持命令行参数指定输入/输出目录，递归扫描 .bank 文件。
  支持作为模块导入调用（run 函数），避免打包后递归启动 EXE。

  用法:
    python epic7_debank.py                          # 使用默认 ./input → ./result
    python epic7_debank.py -i /path/to/input -o /path/to/output

  作为模块导入:
    from epic7_debank import run
    run(input_dir="/path/to/input", output_dir="/path/to/output")

  工作流程:
    1) 递归扫描输入目录，收集所有 .bank 文件
    2) 对每个 .bank 文件，使用 quickbms.exe 解包到临时目录
    3) quickbms 通过 -S 参数回调 _epic7_defsb.py 提取音频
    4) 将提取的音频文件收集到输出目录
    5) 清理临时目录
'''


def parse_args():
    parser = argparse.ArgumentParser(description="Epic7 Bank File Decryptor")
    parser.add_argument("--input-dir", "-i", default=None,
                        help="输入目录（递归扫描 .bank 文件），默认为脚本目录下的 input/")
    parser.add_argument("--output-dir", "-o", default=None,
                        help="输出目录（提取的音频文件），默认为脚本目录下的 result/")
    parser.add_argument("--workers", "-w", type=int, default=None,
                        help=f"并行解包数量（默认 {DEFAULT_WORKERS}，也可由 XL_AUDIO_DEBANK_WORKERS 指定）")
    parser.add_argument("--timeout", type=float, default=None,
                        help="单个 bank 的最大处理秒数（也可由 XL_AUDIO_DEBANK_TIMEOUT 指定）")
    return parser.parse_args()


def _should_process_file(filepath):
    """判断文件是否应作为音频处理

    筛选规则（需同时满足）：
    1. 路径中包含 fmodassets 子目录
    2. 语音（文件名不含扩展名完全由数字组成）或 bgm（位于 bgm 目录下）
    """
    fname = os.path.basename(filepath)
    base = os.path.splitext(fname)[0]
    parts = filepath.replace("\\", "/").split("/")
    if "fmodassets" not in parts:
        return False
    if not base.isdigit() and "bgm" not in parts:
        return False
    return True


def collect_bank_files(input_root):
    """递归扫描输入目录，收集所有符合筛选条件的 .bank 文件路径"""
    bank_files = []
    for root, dirs, files in os.walk(input_root):
        for f in files:
            if f.lower().endswith(".bank"):
                filepath = os.path.join(root, f)
                if _should_process_file(filepath):
                    bank_files.append(filepath)
    return bank_files


def collect_audio_files(result_dir):
    """递归收集结果目录中的所有音频文件"""
    audio_files = []
    if os.path.isdir(result_dir):
        for root, dirs, files in os.walk(result_dir):
            for f in files:
                if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS:
                    audio_files.append(os.path.join(root, f))
    return audio_files


def _bank_state_path(output_root):
    return os.path.join(output_root, BANK_STATE_FILENAME)


def _load_bank_state(output_root):
    try:
        with open(_bank_state_path(output_root), encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, ValueError, TypeError):
        return {"version": BANK_CACHE_VERSION, "banks": {}}
    if not isinstance(state, dict) or state.get("version") != BANK_CACHE_VERSION:
        return {"version": BANK_CACHE_VERSION, "banks": {}}
    banks = state.get("banks")
    return {"version": BANK_CACHE_VERSION, "banks": banks if isinstance(banks, dict) else {}}


def _save_bank_state(output_root, state):
    os.makedirs(output_root, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".bank-state-", suffix=".json", dir=output_root
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, ensure_ascii=False, indent=2)
            state_file.write("\n")
        os.replace(temp_name, _bank_state_path(output_root))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _bank_fingerprint(bank_path, input_root):
    stat = os.stat(bank_path)
    relative = os.path.relpath(bank_path, input_root).replace("\\", "/")
    return {"path": relative, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _cache_record_valid(record, fingerprint, out_rel, output_root):
    if not isinstance(record, dict):
        return False
    if record.get("fingerprint") != fingerprint or record.get("out_rel") != out_rel:
        return False
    files = record.get("files")
    if not isinstance(files, list) or not files:
        return False
    for item in files:
        if not isinstance(item, dict):
            return False
        relative = str(item.get("path", "")).replace("\\", "/")
        path = os.path.join(output_root, relative.replace("/", os.sep))
        if not os.path.isfile(path) or os.path.getsize(path) != item.get("size"):
            return False
    return True


def _output_file_records(result, output_root):
    records = []
    for source in result.get("audio_files", []):
        filename = os.path.basename(source)
        destination = os.path.join(output_root, result["out_rel"], filename)
        if os.path.isfile(destination):
            records.append({
                "path": os.path.relpath(destination, output_root).replace("\\", "/"),
                "size": os.path.getsize(destination),
            })
    return records


def _terminate_process_tree(process):
    """终止 QuickBMS 及其通过 -S 拉起的 Python/解码器子进程。"""
    try:
        root = psutil.Process(process.pid)
        descendants = root.children(recursive=True)
    except (psutil.Error, OSError):
        descendants = []
        root = None

    processes = list(reversed(descendants))
    if root is not None:
        processes.append(root)
    for child in processes:
        try:
            child.terminate()
        except (psutil.Error, OSError):
            pass
    if processes:
        _, alive = psutil.wait_procs(processes, timeout=1)
        for child in alive:
            try:
                child.kill()
            except (psutil.Error, OSError):
                pass
        psutil.wait_procs(alive, timeout=1)
    try:
        process.wait(timeout=1)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _run_process(command, cwd, timeout=None, cancel_check=None):
    """可轮询取消的外部进程执行器，返回退出码或 ``cancelled``。"""
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    deadline = time.monotonic() + timeout if timeout else None
    while True:
        returncode = process.poll()
        if returncode is not None:
            return returncode
        if cancel_check and cancel_check():
            _terminate_process_tree(process)
            return "cancelled"
        if deadline is not None and time.monotonic() >= deadline:
            _terminate_process_tree(process)
            raise subprocess.TimeoutExpired(command, timeout)
        time.sleep(0.1)


def _normalise_vgmstream_names(result_dir):
    """去掉 vgmstream 为多子流添加的序号，保持旧提取器的文件名契约。"""
    for source in collect_audio_files(result_dir):
        filename = os.path.basename(source)
        match = re.match(r"^\d+_(.+)$", filename)
        if not match:
            continue
        destination = os.path.join(os.path.dirname(source), match.group(1))
        if os.path.exists(destination):
            stem, extension = os.path.splitext(destination)
            index = 1
            while os.path.exists(f"{stem}_{index}{extension}"):
                index += 1
            destination = f"{stem}_{index}{extension}"
        os.replace(source, destination)


def execute_vgmstream_bank_single(
    bank_path,
    folder_cur,
    result_dir,
    timeout=None,
    cancel_check=None,
):
    """直接从 FMOD bank 解码，失败时由调用方回退 QuickBMS。"""
    cli = vgmstream_path(folder_cur)
    if not cli:
        return False, "vgmstream_unavailable"
    os.makedirs(result_dir, exist_ok=True)
    output_pattern = os.path.join(result_dir, "?02s_?n.wav")
    try:
        returncode = _run_process(
            [cli, "-i", "-S", "0", "-o", output_pattern, os.path.abspath(bank_path)],
            cwd=os.path.dirname(cli),
            timeout=timeout,
            cancel_check=cancel_check,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    if returncode == "cancelled":
        return False, "cancelled"
    if returncode != 0:
        shutil.rmtree(result_dir, ignore_errors=True)
        os.makedirs(result_dir, exist_ok=True)
        return False, returncode
    _normalise_vgmstream_names(result_dir)
    if not collect_audio_files(result_dir):
        return False, "empty_output"
    return True, returncode


def _script_command(python_exe, script_path, folder_cur, result_dir):
    """构造 QuickBMS -S 命令，并把结果目录显式传给提取脚本。"""
    def quote(value):
        return '"' + str(value).replace('"', '\\"') + '"'

    return (
        f"{quote(python_exe)} {quote(script_path)} #INPUT# "
        f"{quote(folder_cur)} {quote(result_dir)}"
    )


def execute_quickbms_single(
    bank_path,
    folder4subcontractors_,
    bank_temp,
    folder_cur_,
    python_exe_,
    result_dir=None,
    timeout=None,
    cancel_check=None,
):
    """对单个 .bank 文件执行 quickbms 解包，并返回 (是否成功, 退出码)。"""
    bank_name = os.path.basename(bank_path)
    os.makedirs(bank_temp, exist_ok=True)
    if result_dir is None:
        result_dir = os.path.join(bank_temp, "result")
    os.makedirs(result_dir, exist_ok=True)

    try:
        script_path = os.path.join(folder_cur_, "_epic7_defsb.py")
        returncode = _run_process([
            folder4subcontractors_ + "/quickbms.exe", "-Y", "-K", "-d",
            "-F", '{}.bank',
            '-S', _script_command(python_exe_, script_path, folder_cur_, result_dir),
            folder4subcontractors_ + '/bank-files.bms',
            bank_path,
            bank_temp
        ], cwd=bank_temp, timeout=timeout, cancel_check=cancel_check)
        if returncode == "cancelled":
            _log(f"[音频bank] 已取消: {bank_name}")
            return False, "cancelled"
        if returncode != 0:
            _log(f"[音频bank] quickbms 退出码 {returncode}: {bank_name}")
            return False, returncode
        status_path = os.path.join(result_dir, ".extract_status.json")
        if os.path.isfile(status_path):
            try:
                with open(status_path, encoding="utf-8") as status_file:
                    status = json.load(status_file)
            except (OSError, ValueError) as exc:
                _log(f"[音频bank] 读取 FSB 提取状态失败: {bank_name}: {exc}")
            else:
                if status.get("status") == "failed":
                    returncode = status.get("returncode", "extractor_failed")
                    _log(f"[音频bank] FSB 提取失败: {bank_name}，退出码={returncode}")
                    return False, returncode
        return True, returncode
    except subprocess.TimeoutExpired:
        _log(f"[音频bank] 超时（>{timeout:g}s）: {bank_name}")
        return False, "timeout"
    except Exception as e:
        _log(f"[音频bank] 解包失败: {bank_name}: {e}")
        return False, None


def _worker_count(value):
    if value is None:
        value = os.environ.get("XL_AUDIO_DEBANK_WORKERS", DEFAULT_WORKERS)
    try:
        return max(1, min(8, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_WORKERS


def _bank_timeout(value):
    if value is None:
        value = os.environ.get("XL_AUDIO_DEBANK_TIMEOUT", 180)
    try:
        timeout = float(value)
        return timeout if timeout > 0 else None
    except (TypeError, ValueError):
        return 180.0


def _safe_job_name(index, bank_stem):
    safe_stem = "".join(
        char if char.isalnum() or char in "._-" else "_"
        for char in str(bank_stem)
    )
    return f"{index:04d}_{safe_stem or 'bank'}"


def _process_bank_job(job, folder4subcontractors, folder_cur, python_exe, bank_timeout,
                      cancel_check=None):
    started = time.perf_counter()
    if cancel_check and cancel_check():
        return {
            **job,
            "status": "cancelled",
            "returncode": "cancelled",
            "audio_files": [],
            "elapsed": 0,
        }
    direct_success, direct_code = execute_vgmstream_bank_single(
        job["bank_path"],
        folder_cur,
        job["result_dir"],
        timeout=bank_timeout,
        cancel_check=cancel_check,
    )
    method = "vgmstream" if direct_success else "quickbms"
    if direct_success:
        success, returncode = True, direct_code
    else:
        if direct_code == "cancelled":
            return {
                **job,
                "status": "cancelled",
                "returncode": direct_code,
                "audio_files": [],
                "method": "vgmstream",
                "elapsed": time.perf_counter() - started,
            }
        execute_kwargs = {
            "result_dir": job["result_dir"],
            "timeout": bank_timeout,
        }
        if cancel_check is not None:
            execute_kwargs["cancel_check"] = cancel_check
        success, returncode = execute_quickbms_single(
            job["bank_path"],
            folder4subcontractors,
            job["extract_dir"],
            folder_cur,
            python_exe,
            **execute_kwargs,
        )
    audio_files = collect_audio_files(job["result_dir"])
    if returncode == "cancelled" or (cancel_check and cancel_check()):
        status = "cancelled"
    elif not success:
        status = "failed"
    elif not audio_files:
        status = "empty"
    else:
        status = "success"
    return {
        **job,
        "status": status,
        "returncode": returncode,
        "audio_files": audio_files,
        "method": method,
        "elapsed": time.perf_counter() - started,
    }


def _copy_job_audio(result, output_root, before_copy_callback, audio_transform_callback):
    audio_files = result["audio_files"]
    if audio_transform_callback:
        audio_files = audio_transform_callback(
            result["bank_stem"], result["rel_path"], audio_files
        ) or []
        result["audio_files"] = audio_files

    filenames = [os.path.basename(src) for src in audio_files]
    if before_copy_callback:
        try:
            before_copy_callback(
                result["bank_stem"],
                result["rel_path"],
                result["out_rel"],
                filenames,
            )
        except Exception as exc:
            _log(f"[音频bank] 输出回调失败: {result['bank_name']}: {exc}")

    copied = 0
    skipped = 0
    failed = 0
    output_subdir = os.path.join(output_root, result["out_rel"])
    os.makedirs(output_subdir, exist_ok=True)
    for src in audio_files:
        filename = os.path.basename(src)
        dst = os.path.join(output_subdir, filename)
        try:
            if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
                _debug(f"跳过已存在: {os.path.relpath(dst, output_root)}")
                skipped += 1
                continue
            try:
                os.replace(src, dst)
                _debug(f"[OK] 移动 {filename} → {os.path.relpath(dst, output_root)}")
            except OSError:
                # temp_dir 可能被调用方放在其他卷，跨卷时保留兼容复制路径。
                shutil.copy2(src, dst)
                _debug(f"[OK] 复制 {filename} → {os.path.relpath(dst, output_root)}")
            copied += 1
        except Exception as exc:
            _log(f"[音频bank] 复制失败: {filename}: {exc}")
            failed += 1
    return copied, skipped, failed


def run(input_dir, output_dir, folder_cur=None, progress_callback=None, subdir_fn=None,
        before_copy_callback=None, audio_transform_callback=None, workers=None,
        temp_dir=None, bank_timeout=None, cancel_check=None, use_cache=True):
    """解密 .bank 文件，并把每个 bank 的结果隔离后汇总到输出目录。"""
    if folder_cur is None:
        folder_cur = os.path.dirname(os.path.abspath(__file__))

    if getattr(sys, 'frozen', False):
        python_exe = "python"
    else:
        python_exe = psutil.Process(os.getpid()).name()

    os.environ["PYTHONIOENCODING"] = "utf-8"
    encoding = "utf-8"
    codecs.lookup(encoding)

    folder4subcontractors = os.path.join(folder_cur, "_subcontractors")
    folder4tempo = os.path.abspath(temp_dir) if temp_dir else os.path.join(folder_cur, "_tempo")
    legacy_result = os.path.join(folder_cur, "result")

    input_root = os.path.abspath(input_dir)
    output_root = os.path.abspath(output_dir)
    bank_state = _load_bank_state(output_root) if use_cache else {
        "version": BANK_CACHE_VERSION,
        "banks": {},
    }

    _log(f"输入目录: {input_root}")
    _log(f"输出目录: {output_root}")
    _log(f"自定义输出子目录: {'启用' if subdir_fn else '关闭（保留原目录结构）'}")
    worker_count = _worker_count(workers)
    timeout = _bank_timeout(bank_timeout)
    _log(f"音频 bank 解包并行度: {worker_count}")
    _log(f"单个 bank 超时: {timeout or '不限制'} 秒")

    # 递归收集所有 .bank 文件
    bank_files = collect_bank_files(input_root)
    if not bank_files:
        _log("未找到 .bank 文件")
        return {"total": 0, "success": 0, "empty": 0, "failed": 0,
                "copied": 0, "skipped": 0, "copy_failed": 0,
                "cancelled": bool(cancel_check and cancel_check())}

    _log(f"共找到 {len(bank_files)} 个 .bank 文件")

    if cancel_check and cancel_check():
        _log("音频 bank 解包在启动前已取消")
        return {"total": len(bank_files), "success": 0, "empty": 0, "failed": 0,
                "copied": 0, "skipped": 0, "copy_failed": 0, "cancelled": True}

    os.makedirs(output_root, exist_ok=True)

    jobs = []
    cached_count = 0
    for index, bank_path in enumerate(bank_files):
        bank_name = os.path.basename(bank_path)
        bank_stem = os.path.splitext(bank_name)[0]
        rel_path = os.path.relpath(bank_path, input_root)
        rel_dir = os.path.dirname(rel_path)
        out_rel = subdir_fn(rel_path, bank_stem) if subdir_fn else rel_dir
        fingerprint = _bank_fingerprint(bank_path, input_root)
        cache_key = fingerprint["path"]
        if use_cache and _cache_record_valid(
            bank_state["banks"].get(cache_key), fingerprint, out_rel, output_root
        ):
            cached_count += 1
            continue
        job_dir = os.path.join(folder4tempo, _safe_job_name(index, bank_stem))
        jobs.append({
            "bank_path": bank_path,
            "bank_name": bank_name,
            "bank_stem": bank_stem,
            "rel_path": rel_path,
            "out_rel": out_rel or "",
            "extract_dir": os.path.join(job_dir, "extract"),
            "result_dir": os.path.join(job_dir, "result"),
            "cache_key": cache_key,
            "fingerprint": fingerprint,
        })

    if cached_count:
        _log(f"bank 缓存跳过: {cached_count} 个未变化 bank")
    if not jobs:
        _log("所有 bank 均命中增量缓存，无需重新解密")
        return {
            "total": len(bank_files), "success": 0, "empty": 0, "failed": 0,
            "copied": 0, "skipped": 0, "copy_failed": 0,
            "cached": cached_count, "cancelled": False,
        }

    shutil.rmtree(folder4tempo, ignore_errors=True)
    shutil.rmtree(legacy_result, ignore_errors=True)
    pathlib.Path(folder4tempo).mkdir(parents=True, exist_ok=True)

    total_success = 0
    total_empty = 0
    total_failed = 0
    total_copied = 0
    total_skipped = 0
    total_copy_failed = 0
    total_cancelled = 0
    completed = 0

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    _process_bank_job,
                    job,
                    folder4subcontractors,
                    folder_cur,
                    python_exe,
                    timeout,
                    cancel_check,
                ): job
                for job in jobs
            }
            for future in concurrent.futures.as_completed(futures):
                job = futures[future]
                completed += 1
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        **job,
                        "status": "failed",
                        "returncode": None,
                        "audio_files": [],
                        "elapsed": 0,
                    }
                    _log(f"[音频bank] worker 异常: {job['bank_name']}: {exc}")

                if result["status"] == "success":
                    if cancel_check and cancel_check():
                        total_cancelled += 1
                        _log(f"[音频bank] 已取消: {result['bank_name']}")
                    else:
                        total_success += 1
                        copied, skipped, copy_failed = _copy_job_audio(
                            result,
                            output_root,
                            before_copy_callback,
                            audio_transform_callback,
                        )
                        total_copied += copied
                        total_skipped += skipped
                        total_copy_failed += copy_failed
                        if copy_failed == 0:
                            bank_state["banks"][result["cache_key"]] = {
                                "fingerprint": result["fingerprint"],
                                "out_rel": result["out_rel"],
                                "files": _output_file_records(result, output_root),
                            }
                        _debug(
                            f"[音频bank] 成功: {result['bank_name']}，"
                            f"产出 {len(result['audio_files'])}，方式={result.get('method', 'unknown')}，"
                            f"耗时 {result['elapsed']:.2f}s"
                        )
                elif result["status"] == "empty":
                    total_empty += 1
                    _log(
                        f"[音频bank] 空产出: {result['bank_name']} "
                        f"({result['rel_path']})，耗时 {result['elapsed']:.2f}s"
                    )
                elif result["status"] == "cancelled":
                    total_cancelled += 1
                    _log(f"[音频bank] 已取消: {result['bank_name']}")
                else:
                    total_failed += 1
                    _log(
                        f"[音频bank] 失败: {result['bank_name']} "
                        f"({result['rel_path']})，退出码={result['returncode']}"
                    )

                if progress_callback:
                    progress_callback(completed, len(bank_files))
    finally:
        shutil.rmtree(folder4tempo, ignore_errors=True)
        shutil.rmtree(legacy_result, ignore_errors=True)
        if use_cache:
            _save_bank_state(output_root, bank_state)

    total_failed += total_copy_failed
    cancelled = total_cancelled > 0 or bool(cancel_check and cancel_check())
    if cancelled:
        _log(f"音频 bank 解包已取消，已完成 bank {total_success} 个")
    _log(
        f"bank 统计: 成功 {total_success}, 空产出 {total_empty}, "
        f"失败 {total_failed}, 总数 {len(bank_files)}"
    )
    _log(
        f"处理完成: 已复制 {total_copied} 个, 跳过 {total_skipped} 个, "
        f"失败 {total_failed} 个"
    )
    _log(f"输出目录: {output_root}")
    _log("完成!")
    return {
        "total": len(bank_files),
        "success": total_success,
        "empty": total_empty,
        "failed": total_failed,
        "copied": total_copied,
        "skipped": total_skipped,
        "copy_failed": total_copy_failed,
        "cached": cached_count,
        "cancelled": cancelled,
    }


def main():
    args = parse_args()
    folder_cur = os.path.dirname(os.path.abspath(__file__))
    input_root = os.path.abspath(args.input_dir) if args.input_dir else os.path.join(folder_cur, "input")
    output_root = os.path.abspath(args.output_dir) if args.output_dir else os.path.join(folder_cur, "result")
    run(input_root, output_root, folder_cur, workers=args.workers, bank_timeout=args.timeout)


if __name__ == "__main__":
    main()
