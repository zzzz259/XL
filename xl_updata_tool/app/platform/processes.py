"""外部程序调用的统一诊断包装。"""

from __future__ import annotations

import subprocess
import time
from typing import Sequence

from .logger import logger


def run_external_process(
    command: Sequence[str],
    *,
    tool: str,
    timeout: float | None = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """执行外部程序并记录开始、结束、超时和输出摘要。

    返回值保持 ``subprocess.run`` 的 CompletedProcess 契约，调用方无需改变
    原有退出码判断逻辑。完整命令参数只在 Debug 日志中记录。
    """
    started = time.perf_counter()
    cwd = kwargs.get("cwd")
    logger.debug(
        "process.start tool=%s args=%s cwd=%s timeout=%s",
        tool,
        list(command),
        cwd,
        timeout,
    )
    try:
        result = subprocess.run(command, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.error("process.timeout tool=%s elapsed_ms=%.1f timeout=%s", tool, elapsed_ms, timeout)
        raise
    except OSError:
        logger.exception("process.start_failed tool=%s", tool)
        raise

    elapsed_ms = (time.perf_counter() - started) * 1000
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    logger.debug(
        "process.exit tool=%s exit_code=%s elapsed_ms=%.1f stdout_bytes=%s stderr_bytes=%s",
        tool,
        result.returncode,
        elapsed_ms,
        len(stdout.encode("utf-8", errors="replace")) if isinstance(stdout, str) else len(stdout),
        len(stderr.encode("utf-8", errors="replace")) if isinstance(stderr, str) else len(stderr),
    )
    if result.returncode != 0:
        logger.warning(
            "process.failed tool=%s exit_code=%s stderr_tail=%r stdout_tail=%r",
            tool,
            result.returncode,
            _tail(stderr),
            _tail(stdout),
        )
    return result


def _tail(value, limit: int = 2000) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    return text[-limit:]
