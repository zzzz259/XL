import subprocess
from pathlib import Path

from app.platform import self_check
from app.platform.self_check import (
    CheckResult,
    SelfCheckReport,
    render_report,
    run_self_check,
)
from app.platform.tool_locator import ToolLocator


def _make_release_tree(root: Path) -> ToolLocator:
    """搭一套完整的冻结布局（tools + runtimes），返回指向它的 locator。"""
    tools = root / "tools"
    (tools / "AssetStudio").mkdir(parents=True)
    (tools / "AssetStudio" / "AssetStudio.CLI.dll").write_bytes(b"placeholder")
    (tools / "vgmstream").mkdir()
    (tools / "vgmstream" / "vgmstream-cli.exe").write_bytes(b"placeholder")
    subcontractors = tools / "epic7_debank_v1_0" / "_subcontractors"
    subcontractors.mkdir(parents=True)
    (subcontractors / "quickbms.exe").write_bytes(b"placeholder")
    (subcontractors / "fsb_aud_extr.exe").write_bytes(b"placeholder")
    (tools / "lua").mkdir()
    (tools / "lua" / "unluac.jar").write_bytes(b"placeholder")
    (tools / "lua" / "opmap").write_bytes(b"placeholder")
    (tools / "SpineViewer").mkdir()
    (tools / "SpineViewer" / "SpineViewerCLI.exe").write_bytes(b"placeholder")
    (tools / "SpineViewer" / "ffmpeg.exe").write_bytes(b"placeholder")
    (root / "runtimes" / "java" / "bin").mkdir(parents=True)
    (root / "runtimes" / "java" / "bin" / "java.exe").write_bytes(b"placeholder")
    (root / "runtimes" / "dotnet").mkdir(parents=True)
    (root / "runtimes" / "dotnet" / "dotnet.exe").write_bytes(b"placeholder")
    return ToolLocator(root=root, frozen=True)


def _fake_process_run(args, **kwargs):
    """模拟 java -version / dotnet --list-runtimes 的正常输出。"""
    exe = str(args[0])
    if exe.endswith("java.exe"):
        return subprocess.CompletedProcess(
            args, 0, stdout="", stderr='openjdk version "17.0.9" 2023-10-17'
        )
    if exe.endswith("dotnet.exe"):
        return subprocess.CompletedProcess(
            args, 0, stdout="Microsoft.NETCore.App 8.0.16 [C:\\runtimes\\dotnet]", stderr=""
        )
    raise AssertionError(f"unexpected subprocess call: {args}")


def _isolate_dirs(monkeypatch, tmp_path):
    """自检会真实探测目录可写性，把它引到测试临时目录，避免碰项目 data/output/logs。"""
    for name in ("get_data_dir", "get_output_dir", "get_logs_dir"):
        monkeypatch.setattr(self_check, name, lambda: str(tmp_path))
    monkeypatch.setattr(self_check, "resource_root", lambda: tmp_path)


def test_report_format_and_pass_summary():
    report = SelfCheckReport((
        CheckResult("资源根目录可读", True, "E:/XL"),
        CheckResult("工具文件 unluac.jar", False, "missing"),
    ))

    text = render_report(report)

    assert "[PASS] 资源根目录可读: E:/XL" in text
    assert "[FAIL] 工具文件 unluac.jar: missing" in text
    assert "汇总: 1/2 项通过，1 项失败" in text
    assert "自检未通过" in text
    assert report.exit_code == 1


def test_all_pass_report_exit_code_zero():
    report = SelfCheckReport((CheckResult("Java 运行时（java -version）", True, "openjdk 17"),))

    assert report.passed
    assert report.exit_code == 0
    assert "自检通过" in render_report(report)


def test_self_check_passes_on_complete_release_tree(tmp_path, monkeypatch, capsys):
    locator = _make_release_tree(tmp_path)
    _isolate_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(self_check.subprocess, "run", _fake_process_run)
    monkeypatch.setattr(self_check.importlib, "import_module", lambda name: object())

    exit_code = run_self_check(locator=locator, report_dir=tmp_path / "reports")

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "[FAIL]" not in output
    assert "自检通过" in output
    reports = list((tmp_path / "reports").glob("self-check-*.txt"))
    assert len(reports) == 1
    assert "[PASS]" in reports[0].read_text(encoding="utf-8")


def test_self_check_fails_when_tool_missing(tmp_path, monkeypatch):
    locator = _make_release_tree(tmp_path)
    (tmp_path / "tools" / "lua" / "unluac.jar").unlink()
    _isolate_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(self_check.subprocess, "run", _fake_process_run)
    monkeypatch.setattr(self_check.importlib, "import_module", lambda name: object())

    exit_code = run_self_check(locator=locator, report_dir=tmp_path)

    assert exit_code == 1
    report = SelfCheckReport(tuple(self_check._collect_checks(locator)))
    failed_names = [result.name for result in report.failed]
    assert "工具文件 unluac.jar" in failed_names


def test_self_check_fails_without_dotnet8_runtime(tmp_path, monkeypatch):
    locator = _make_release_tree(tmp_path)
    _isolate_dirs(monkeypatch, tmp_path)

    def run_without_dotnet8(args, **kwargs):
        if str(args[0]).endswith("dotnet.exe"):
            return subprocess.CompletedProcess(args, 0, stdout="Microsoft.NETCore.App 6.0.32", stderr="")
        return _fake_process_run(args, **kwargs)

    monkeypatch.setattr(self_check.subprocess, "run", run_without_dotnet8)
    monkeypatch.setattr(self_check.importlib, "import_module", lambda name: object())

    exit_code = run_self_check(locator=locator, report_dir=tmp_path)

    assert exit_code == 1
    report = SelfCheckReport(tuple(self_check._collect_checks(locator)))
    dotnet = next(result for result in report.results if "dotnet" in result.name)
    assert not dotnet.ok


def test_self_check_fails_when_java_runtime_missing(tmp_path, monkeypatch):
    # 冻结布局缺 runtimes/java：java() 抛 ToolNotFoundError，自检报 FAIL 而不是异常。
    locator = _make_release_tree(tmp_path)
    (tmp_path / "runtimes" / "java" / "bin" / "java.exe").unlink()
    _isolate_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(self_check.subprocess, "run", _fake_process_run)
    monkeypatch.setattr(self_check.importlib, "import_module", lambda name: object())

    exit_code = run_self_check(locator=locator, report_dir=tmp_path)

    assert exit_code == 1
    report = SelfCheckReport(tuple(self_check._collect_checks(locator)))
    java = next(result for result in report.results if "Java" in result.name)
    assert not java.ok
