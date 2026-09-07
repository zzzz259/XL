"""Release 环境自检（--self-check）。

打包后的干净机器上没有 Python/Java/.NET 安装，一旦缺少 bundled 组件，
用户只能看到功能静默失败。自检模式逐项验证资源目录、Python 依赖、
bundled 运行时（java/dotnet 实际执行）和外部工具文件，输出
[PASS]/[FAIL] 报告并把报告写入 logs 目录；必要组件缺失时返回退出码 1，
供启动器、CI 和用户排障使用。

本模块不依赖 Qt，且必须在创建 QApplication 之前可独立运行。
"""

from __future__ import annotations

import ctypes
import importlib
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .logger import logger
from .paths import get_data_dir, get_logs_dir, get_output_dir
from .tool_locator import ToolLocator, ToolNotFoundError, resource_root

# 自检等待外部进程的最长时间；干净机器上首次启动 dotnet 可能触发运行时解压，留足余量。
PROCESS_TIMEOUT = 30
# AssetStudio.CLI 是 framework-dependent 构建，运行时必须包含 .NET 8。
REQUIRED_DOTNET_RUNTIME = "Microsoft.NETCore.App 8.0."
# (显示名, import 模块名)；Pillow 的 import 名是 PIL，pycryptodome 是 Crypto。
PYTHON_MODULES = (
    ("PySide6", "PySide6"),
    ("Pillow", "PIL"),
    ("UnityPy", "UnityPy"),
    ("Crypto", "Crypto"),
    ("psutil", "psutil"),
)


@dataclass(frozen=True)
class CheckResult:
    """单项自检结果；critical 为 True 的项失败会让退出码变为 1。"""

    name: str
    ok: bool
    detail: str = ""
    critical: bool = True


@dataclass(frozen=True)
class SelfCheckReport:
    results: tuple[CheckResult, ...] = field(default_factory=tuple)

    @property
    def failed(self) -> tuple[CheckResult, ...]:
        return tuple(result for result in self.results if not result.ok)

    @property
    def passed(self) -> bool:
        """所有必要组件通过才算自检通过。"""
        return all(result.ok or not result.critical for result in self.results)

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1


def run_self_check(session=None, *, locator: ToolLocator | None = None,
                   report_dir: str | os.PathLike[str] | None = None) -> int:
    """执行自检、打印并落盘报告，返回进程退出码（0 通过 / 1 失败）。"""
    _attach_parent_console()
    locator = locator or ToolLocator.create()
    report = SelfCheckReport(tuple(_collect_checks(locator)))
    text = render_report(report)

    # 控制台 print 给双击/命令行用户看；logger 留进会话日志，两者互补。
    print(text)
    for line in text.splitlines():
        logger.info("self_check %s", line)

    try:
        report_path = _write_report(text, session, report_dir)
        print(f"报告已写入: {report_path}")
        logger.info("self_check.report path=%s", report_path)
    except (OSError, PermissionError) as error:
        logger.warning("self_check.report_unavailable error=%s", error)
    return report.exit_code


def render_report(report: SelfCheckReport) -> str:
    """渲染 [PASS]/[FAIL] 文本报告，结尾附汇总与结论。"""
    lines = ["XL 环境自检报告", "=" * 40]
    for result in report.results:
        status = "[PASS]" if result.ok else "[FAIL]"
        line = f"{status} {result.name}"
        if result.detail:
            line += f": {result.detail}"
        lines.append(line)
    lines.append("=" * 40)
    failed = report.failed
    lines.append(f"汇总: {len(report.results) - len(failed)}/{len(report.results)} 项通过，{len(failed)} 项失败")
    lines.append("结论: 自检通过" if report.passed else "结论: 自检未通过，存在缺失组件")
    return "\n".join(lines)


def _collect_checks(locator: ToolLocator) -> list[CheckResult]:
    checks = [_check_resource_root()]
    checks.extend(_check_writable_dirs())
    checks.extend(_check_python_modules())
    checks.append(_check_java(locator))
    checks.append(_check_dotnet(locator))
    checks.extend(_check_tool_files(locator))
    return checks


def _check_resource_root() -> CheckResult:
    root = resource_root()
    ok = root.is_dir() and os.access(root, os.R_OK)
    return CheckResult("资源根目录可读", ok, str(root))


def _check_writable_dirs() -> list[CheckResult]:
    return [
        _check_writable_dir("数据目录可写 data/", get_data_dir()),
        _check_writable_dir("输出目录可写 output/", get_output_dir()),
        _check_writable_dir("日志目录可写 logs/", get_logs_dir()),
    ]


def _check_writable_dir(name: str, directory) -> CheckResult:
    try:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".self-check-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckResult(name, True, str(path))
    except (OSError, PermissionError) as error:
        return CheckResult(name, False, f"{directory} ({error})")


def _check_python_modules() -> list[CheckResult]:
    results = []
    for display, module_name in PYTHON_MODULES:
        try:
            importlib.import_module(module_name)
        except ImportError as error:
            results.append(CheckResult(f"Python 依赖 {display}", False, str(error)))
        else:
            results.append(CheckResult(f"Python 依赖 {display}", True, "可导入"))
    return results


def _check_java(locator: ToolLocator) -> CheckResult:
    try:
        java = locator.java()
    except ToolNotFoundError as error:
        return CheckResult("Java 运行时（java -version）", False, str(error))
    try:
        proc = subprocess.run(
            [java, "-version"],
            capture_output=True, text=True, timeout=PROCESS_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return CheckResult("Java 运行时（java -version）", False, f"{java}: {error}")
    # java -version 的版本信息固定输出到 stderr。
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    if proc.returncode == 0:
        return CheckResult("Java 运行时（java -version）", True, detail[0] if detail else java)
    return CheckResult("Java 运行时（java -version）", False, f"退出码 {proc.returncode}")


def _check_dotnet(locator: ToolLocator) -> CheckResult:
    name = "dotnet 运行时（含 .NET 8）"
    try:
        dotnet = locator.dotnet()
    except ToolNotFoundError as error:
        return CheckResult(name, False, str(error))
    try:
        proc = subprocess.run(
            [dotnet, "--list-runtimes"],
            capture_output=True, text=True, timeout=PROCESS_TIMEOUT, check=False,
            env=locator.subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return CheckResult(name, False, f"{dotnet}: {error}")
    matched = [line.strip() for line in (proc.stdout or "").splitlines()
               if REQUIRED_DOTNET_RUNTIME in line]
    if proc.returncode == 0 and matched:
        return CheckResult(name, True, matched[0])
    if proc.returncode != 0:
        return CheckResult(name, False, f"退出码 {proc.returncode}")
    return CheckResult(name, False, f"未找到 {REQUIRED_DOTNET_RUNTIME}x 运行时")


def _check_tool_files(locator: ToolLocator) -> list[CheckResult]:
    # ffmpeg 检查 bundled 文件本身：PATH 回退只是开发便利，Release 必须随包携带。
    files = (
        ("AssetStudio.CLI.dll", locator.assetstudio_dll()),
        ("vgmstream-cli.exe", locator.vgmstream()),
        ("quickbms.exe", locator.quickbms()),
        ("fsb_aud_extr.exe", locator.fsb_extractor()),
        ("unluac.jar", locator.unluac_jar()),
        ("unluac opmap", locator.unluac_opmap()),
        ("SpineViewerCLI.exe", locator.spineviewer_cli()),
        ("ffmpeg.exe", str(locator.tools / "SpineViewer" / "ffmpeg.exe")),
    )
    return [
        CheckResult(f"工具文件 {name}", os.path.isfile(path), path)
        for name, path in files
    ]


def _attach_parent_console() -> None:
    """冻结的窗口程序（console=False）没有控制台，先尝试附着父控制台再打印。"""
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.AttachConsole(-1)
    except (AttributeError, OSError):
        # 附着失败（如双击启动没有父控制台）不阻塞自检，报告仍会写入 logs。
        pass


def _write_report(text: str, session, report_dir) -> Path:
    """报告落盘：优先本次日志会话目录，其次指定目录，最后 logs/ 根目录。"""
    if session is not None:
        path = Path(session.directory) / "self-check.txt"
    else:
        root = Path(report_dir) if report_dir else Path(get_logs_dir())
        path = root / f"self-check-{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return path
