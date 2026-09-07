"""启动环境摘要和 Debug 诊断文件。"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .logger import logger
from .paths import get_base_dir, get_data_dir, get_output_dir, get_tools_dir
from .tool_locator import ToolLocator, ToolNotFoundError


def collect_environment() -> dict[str, object]:
    base_dir = get_base_dir()
    locator = ToolLocator.create()
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "cwd": os.getcwd(),
        "base_dir": base_dir,
        "data_dir": get_data_dir(),
        "output_dir": get_output_dir(),
        "tools_dir": get_tools_dir(),
        "cpu_count": os.cpu_count(),
        "git_commit": _git_commit(base_dir),
        "packages": {
            name: _package_version(name)
            for name in ("Py" + "Side6", "UnityPy", "psutil")
        },
        "tools": {
            "java": _runtime_available(locator.java),
            "dotnet": _runtime_available(locator.dotnet),
            "assetstudio": os.path.isfile(locator.assetstudio_dll()),
            "vgmstream": os.path.isfile(locator.vgmstream()),
            # quickbms 实际位于 epic7_debank 的 _subcontractors/，不是独立目录
            "quickbms": os.path.isfile(locator.quickbms()),
            "fsb_aud_extr": os.path.isfile(locator.fsb_extractor()),
            "unluac": os.path.isfile(locator.unluac_jar()),
            "spineviewer_cli": os.path.isfile(locator.spineviewer_cli()),
            "ffmpeg": os.path.isfile(str(locator.tools / "SpineViewer" / "ffmpeg.exe"))
            or shutil.which("ffmpeg") is not None,
        },
    }


def _runtime_available(resolve) -> bool:
    """bundled 或系统 PATH（仅开发模式）任一可用即视为可用。"""
    try:
        resolve()
    except ToolNotFoundError:
        return False
    return True


def write_environment_report(directory: str | os.PathLike[str]) -> Path:
    report_path = Path(directory) / "environment.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    data = collect_environment()
    lines = ["XL Diagnostic Environment", "=" * 24]
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            lines.extend(f"  {subkey}: {subvalue}" for subkey, subvalue in value.items())
        else:
            lines.append(f"{key}: {value}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.debug("environment.report path=%s", report_path)
    return report_path


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _git_commit(base_dir: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
