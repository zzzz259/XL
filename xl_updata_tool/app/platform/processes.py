"""外部程序调用与有限资源诊断捕获。"""

from __future__ import annotations

import codecs
import json
import locale
import os
import re
import subprocess
import threading
import time
import uuid
from contextvars import copy_context
from pathlib import Path
from typing import Sequence

from .logger import get_log_session, logger
from .logging_context import get_log_context


DEFAULT_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_NATIVE_LOG_BYTES = 10 * 1024 * 1024
_RETURN_TAIL_BYTES = 64 * 1024
_SENSITIVE_ARGUMENT = re.compile(
    r"(?:token|password|secret|authorization|cookie|api[-_]?key|access[-_]?key)",
    re.IGNORECASE,
)


class _StreamCapture:
    """Spool a process stream to disk while bounding retained output and memory."""

    def __init__(self, path: Path | None, max_bytes: int):
        self.path = path
        self.max_bytes = max(1, int(max_bytes))
        self.tail_limit = min(_RETURN_TAIL_BYTES, max(1, self.max_bytes // 4))
        self.head_limit = max(0, self.max_bytes - self.tail_limit)
        self.head = bytearray()
        self.tail = bytearray()
        self.total_bytes = 0
        self._file = None
        self.write_error: OSError | None = None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._file = path.open("wb")
            except OSError as exc:
                self.write_error = exc

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.total_bytes += len(chunk)
        head_count = min(len(chunk), max(0, self.head_limit - len(self.head)))
        if head_count:
            head_chunk = chunk[:head_count]
            self.head.extend(head_chunk)
            self._write(head_chunk)
        remainder = chunk[head_count:]
        if remainder:
            self.tail.extend(remainder)
            overflow = len(self.tail) - self.tail_limit
            if overflow > 0:
                del self.tail[:overflow]

    def finish(self) -> None:
        if self._file is None:
            return
        try:
            if self.total_bytes > self.max_bytes:
                marker = (
                    f"\n[XL OUTPUT TRUNCATED: total={self.total_bytes} bytes; "
                    f"kept_head={len(self.head)} kept_tail={len(self.tail)}]\n"
                ).encode("ascii")
                self._file.write(marker)
            self._file.write(self.tail)
            self._file.flush()
        except OSError as exc:
            self.write_error = self.write_error or exc
        finally:
            try:
                self._file.close()
            except OSError as exc:
                self.write_error = self.write_error or exc
            self._file = None

    @property
    def truncated(self) -> bool:
        return self.total_bytes > self.max_bytes

    def returned_bytes(self) -> bytes:
        return bytes(self.head + self.tail)

    def tail_text(self, encoding: str) -> str:
        return self.returned_bytes().decode(encoding, errors="replace")[-2000:]

    def _write(self, chunk: bytes) -> None:
        if self._file is None:
            return
        try:
            self._file.write(chunk)
        except OSError as exc:
            self.write_error = self.write_error or exc
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None


def run_external_process(
    command: Sequence[str],
    *,
    tool: str,
    timeout: float | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    tool_log_paths: Sequence[str | os.PathLike[str]] = (),
    on_line=None,
    cancel_check=None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """运行外部工具并持续保存其 stdout/stderr 与声明的原生日志。

    返回 ``CompletedProcess`` 以兼容现有调用方。stdout/stderr 会由独立读取
    线程排空并流式写入当前会话的 ``external/<command_id>-<tool>/``，防止
    PIPE 死锁；单个流的持久化与返回内容均有字节上限。正式版普通运行也会
    捕获输出，不能依赖用户开启 Debug。
    """
    command_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    cwd = kwargs.pop("cwd", None)
    env = kwargs.pop("env", None)
    input_value = kwargs.pop("input", None)
    check = bool(kwargs.pop("check", False))
    capture_output = bool(kwargs.pop("capture_output", False))
    text_mode = bool(
        kwargs.pop("text", False)
        or kwargs.pop("universal_newlines", False)
        or "encoding" in kwargs
        or "errors" in kwargs
    )
    encoding = kwargs.pop("encoding", None) or locale.getpreferredencoding(False)
    errors = kwargs.pop("errors", None) or "replace"
    requested_stdout = kwargs.pop("stdout", None)
    requested_stderr = kwargs.pop("stderr", None)
    if not tool_log_paths and "spine" in tool.casefold() and command:
        tool_log_paths = (Path(command[0]).parent / "logs" / "cli.log",)
    if capture_output and (requested_stdout is not None or requested_stderr is not None):
        raise ValueError("stdout/stderr arguments may not be used with capture_output")
    if requested_stdout not in (None, subprocess.PIPE) or requested_stderr not in (None, subprocess.PIPE):
        raise ValueError("run_external_process owns stdout/stderr so it can preserve tool diagnostics")

    session = get_log_session()
    log_context = get_log_context()
    command_dir = None
    if session and session.external_dir:
        safe_tool = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool).strip("._-") or "tool"
        if log_context.task_id and log_context.task_id != "-":
            external_root = session.directory / "tasks" / log_context.task_id / "external"
        else:
            external_root = session.external_dir
        command_dir = external_root / f"{command_id}-{safe_tool}"
        try:
            command_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("process.log_directory_unavailable command_id=%s error=%s", command_id, exc)
            command_dir = None

    stdout_path = command_dir / "stdout.log" if command_dir else None
    stderr_path = command_dir / "stderr.log" if command_dir else None
    stdout_capture = _StreamCapture(stdout_path, max_output_bytes)
    stderr_capture = _StreamCapture(stderr_path, max_output_bytes)
    safe_command = _redact_command(command)
    logger.debug(
        "process.start command_id=%s tool=%s args=%r cwd=%s timeout=%s output_dir=%s",
        command_id,
        tool,
        safe_command,
        cwd,
        timeout,
        command_dir,
        extra={"event": "process.start", "details": {"tool": tool, "command_id": command_id}},
    )

    popen_kwargs = dict(kwargs)
    popen_kwargs.update(
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if input_value is not None else popen_kwargs.pop("stdin", None),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except OSError:
        stdout_capture.finish()
        stderr_capture.finish()
        logger.exception("process.start_failed command_id=%s tool=%s args=%r", command_id, tool, safe_command, extra={"event": "process.start_failed", "error_code": "PROCESS_START_FAILED", "details": {"tool": tool, "command_id": command_id}})
        raise

    readers = []
    for stream, capture, name in (
        (process.stdout, stdout_capture, "stdout"),
        (process.stderr, stderr_capture, "stderr"),
    ):
        context = copy_context()
        readers.append(
            threading.Thread(
                target=context.run,
                args=(_drain_stream, stream, capture, name, on_line, encoding, errors),
                daemon=True,
            )
        )
    for reader in readers:
        reader.start()

    input_error = None
    if input_value is not None:
        input_bytes = input_value.encode(encoding, errors=errors) if isinstance(input_value, str) else input_value

        def write_input() -> None:
            nonlocal input_error
            try:
                process.stdin.write(input_bytes)
                process.stdin.close()
            except (BrokenPipeError, OSError) as exc:
                input_error = exc

        threading.Thread(target=write_input, daemon=True).start()

    timed_out = False
    cancelled = False
    try:
        if cancel_check is None:
            try:
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                return_code = process.wait()
        else:
            deadline = started + timeout if timeout is not None else None
            while process.poll() is None:
                if cancel_check():
                    cancelled = True
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    break
                if deadline is not None and time.perf_counter() >= deadline:
                    timed_out = True
                    process.kill()
                    break
                time.sleep(0.05)
            return_code = process.wait()
    finally:
        for reader in readers:
            reader.join()
        stdout_capture.finish()
        stderr_capture.finish()

    stdout_bytes = stdout_capture.returned_bytes()
    stderr_bytes = stderr_capture.returned_bytes()
    stdout_value = _decode(stdout_bytes, encoding, errors) if text_mode else stdout_bytes
    stderr_value = _decode(stderr_bytes, encoding, errors) if text_mode else stderr_bytes
    if not capture_output and requested_stdout is None:
        stdout_value = None
    if not capture_output and requested_stderr is None:
        stderr_value = None

    native_logs = _collect_native_logs(tool_log_paths, command_dir)
    elapsed_ms = (time.perf_counter() - started) * 1000
    manifest = {
        "schema_version": 1,
        "session_id": session.session_id if session else None,
        "task_id": log_context.task_id,
        "parent_task_id": log_context.parent_task,
        "component": log_context.component,
        "stage": log_context.stage,
        "command_id": command_id,
        "tool": tool,
        "args": safe_command,
        "cwd": str(cwd) if cwd is not None else None,
        "return_code": return_code,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "duration_ms": round(elapsed_ms, 1),
        "stdout_bytes": stdout_capture.total_bytes,
        "stderr_bytes": stderr_capture.total_bytes,
        "stdout_truncated": stdout_capture.truncated,
        "stderr_truncated": stderr_capture.truncated,
        "stdout_log": str(stdout_path) if stdout_path and stdout_path.exists() else None,
        "stderr_log": str(stderr_path) if stderr_path and stderr_path.exists() else None,
        "native_logs": native_logs,
        "log_write_error": str(stdout_capture.write_error or stderr_capture.write_error) if stdout_capture.write_error or stderr_capture.write_error else None,
        "input_write_error": str(input_error) if input_error else None,
        "encoding": encoding if text_mode else None,
    }
    _write_manifest(command_dir, manifest)

    logger.debug(
        "process.exit command_id=%s tool=%s exit_code=%s elapsed_ms=%.1f stdout_bytes=%d stderr_bytes=%d stdout_truncated=%s stderr_truncated=%s stdout_log=%s stderr_log=%s",
        command_id,
        tool,
        return_code,
        elapsed_ms,
        stdout_capture.total_bytes,
        stderr_capture.total_bytes,
        stdout_capture.truncated,
        stderr_capture.truncated,
        stdout_path,
        stderr_path,
        extra={"event": "process.exit", "details": {"tool": tool, "command_id": command_id, "exit_code": return_code}},
    )
    if native_logs:
        logger.debug("process.native_logs command_id=%s files=%r", command_id, native_logs)
    if stdout_capture.write_error or stderr_capture.write_error:
        logger.warning(
            "process.output_log_incomplete command_id=%s error=%s",
            command_id,
            stdout_capture.write_error or stderr_capture.write_error,
        )
    if timed_out:
        logger.error(
            "process.timeout command_id=%s tool=%s elapsed_ms=%.1f timeout=%s stdout_tail=%r stderr_tail=%r",
            command_id,
            tool,
            elapsed_ms,
            timeout,
            stdout_capture.tail_text(encoding),
            stderr_capture.tail_text(encoding),
            extra={"event": "process.timeout", "error_code": "PROCESS_TIMEOUT", "details": {"tool": tool, "command_id": command_id, "timeout": timeout}},
        )
        raise subprocess.TimeoutExpired(command, timeout, output=stdout_value, stderr=stderr_value)
    if return_code != 0:
        logger.warning(
            "process.failed command_id=%s tool=%s exit_code=%s elapsed_ms=%.1f stdout_tail=%r stderr_tail=%r",
            command_id,
            tool,
            return_code,
            elapsed_ms,
            stdout_capture.tail_text(encoding),
            stderr_capture.tail_text(encoding),
            extra={"event": "process.failed", "error_code": "PROCESS_NONZERO_EXIT", "details": {"tool": tool, "command_id": command_id, "exit_code": return_code}},
        )

    result = subprocess.CompletedProcess(command, return_code, stdout_value, stderr_value)
    result.xl_command_id = command_id
    result.xl_log_dir = command_dir
    result.xl_cancelled = cancelled
    if check and return_code != 0:
        raise subprocess.CalledProcessError(return_code, command, output=stdout_value, stderr=stderr_value)
    return result


def _drain_stream(stream, capture: _StreamCapture, name: str, on_line, encoding: str, errors: str) -> None:
    decoder = codecs.getincrementaldecoder(encoding)(errors=errors) if on_line else None
    pending = ""
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            capture.feed(chunk)
            if decoder:
                pending += decoder.decode(chunk)
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    _emit_line(on_line, name, line.rstrip("\r"))
        if decoder:
            pending += decoder.decode(b"", final=True)
            if pending:
                _emit_line(on_line, name, pending.rstrip("\r"))
    except OSError as exc:
        capture.write_error = capture.write_error or exc
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _emit_line(callback, stream_name: str, line: str) -> None:
    try:
        callback(stream_name, line)
    except Exception:
        logger.exception("process.output_callback_failed stream=%s", stream_name)


def _decode(value: bytes, encoding: str, errors: str):
    return value.decode(encoding, errors=errors)


def _redact_command(command: Sequence[str]) -> list[str]:
    safe = []
    redact_next = False
    for argument in command:
        value = str(argument)
        if redact_next:
            safe.append("<redacted>")
            redact_next = False
            continue
        if _SENSITIVE_ARGUMENT.search(value):
            if "=" in value:
                safe.append(value.split("=", 1)[0] + "=<redacted>")
            else:
                safe.append(value)
                redact_next = True
        else:
            safe.append(value)
    return safe


def _collect_native_logs(paths: Sequence[str | os.PathLike[str]], command_dir: Path | None) -> list[str]:
    if not command_dir:
        return []
    collected = []
    native_dir = command_dir / "native"
    for index, raw_path in enumerate(paths):
        source = Path(raw_path)
        try:
            if not source.is_file():
                logger.debug("process.native_log_missing path=%s", source)
                continue
            native_dir.mkdir(parents=True, exist_ok=True)
            target = native_dir / f"{index:02d}-{source.name}"
            _copy_limited(source, target, DEFAULT_MAX_NATIVE_LOG_BYTES)
            collected.append(str(target))
        except OSError:
            logger.warning("process.native_log_copy_failed path=%s", source, exc_info=True)
    return collected


def _copy_limited(source: Path, target: Path, max_bytes: int) -> None:
    remaining = max_bytes
    copied = 0
    with source.open("rb") as src, target.open("wb") as dst:
        while remaining:
            chunk = src.read(min(64 * 1024, remaining))
            if not chunk:
                break
            dst.write(chunk)
            copied += len(chunk)
            remaining -= len(chunk)
        if src.read(1):
            dst.write(f"\n[XL NATIVE LOG TRUNCATED: copied={copied} limit={max_bytes}]\n".encode("ascii"))


def _write_manifest(directory: Path | None, manifest: dict) -> None:
    if not directory:
        return
    path = directory / "command.json"
    temporary = directory / "command.json.tmp"
    try:
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        logger.warning("process.manifest_write_failed path=%s", path, exc_info=True)


def update_process_manifest(result: subprocess.CompletedProcess, **details) -> None:
    """Attach business-level input/output verification to a command record."""
    directory = getattr(result, "xl_log_dir", None)
    if directory is None:
        return
    path = Path(directory) / "command.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.update(details)
        _write_manifest(Path(directory), manifest)
    except (OSError, json.JSONDecodeError):
        logger.warning("process.manifest_update_failed path=%s", path, exc_info=True)
