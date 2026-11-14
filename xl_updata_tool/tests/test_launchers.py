from pathlib import Path
import subprocess

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('total', [21, 22])
def test_launcher_accepts_complete_reports_after_checks_are_added(monkeypatch, total):
    from app.platform import self_check
    from app.platform.tool_locator import ToolLocator

    monkeypatch.setattr(Path, 'is_file', lambda self: True)
    tools = self_check._check_tool_files(ToolLocator.create())
    checks = (*tools, *(self_check.CheckResult(f'additional check {i}', True)
                       for i in range(total - len(tools))))
    output = self_check.render_report(self_check.SelfCheckReport(checks))
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, output, ''))

    test_source_launchers_run_self_check_with_project_python()


def test_launcher_rejects_report_missing_a_registered_renderer(monkeypatch):
    from app.platform import self_check
    from app.platform.tool_locator import ToolLocator

    monkeypatch.setattr(Path, 'is_file', lambda self: True)
    checks = [check for check in self_check._check_tool_files(ToolLocator.create())
              if not check.name.endswith('PortraitSceneCLI.dll')]
    output = self_check.render_report(self_check.SelfCheckReport(tuple(checks)))
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, output, ''))

    with pytest.raises(AssertionError, match='missing registered tool check.*PortraitSceneCLI.dll'):
        test_source_launchers_run_self_check_with_project_python()


def test_source_launchers_run_self_check_with_project_python():
    """The source launchers must not fall back to a global Python install."""
    from app.platform.self_check import _check_tool_files
    from app.platform.tool_locator import ToolLocator

    project_pythons = (
        PROJECT_DIR / ".venv" / "Scripts" / "python.exe",
        PROJECT_DIR.parent / ".venv" / "Scripts" / "python.exe",
    )
    if not any(python.is_file() for python in project_pythons):
        pytest.skip("source launcher self-check requires the project .venv")

    for launcher in ("run.bat", "debug.bat"):
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "call", str(PROJECT_DIR / launcher), "--self-check"],
            cwd=PROJECT_DIR,
            input="\r\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

        output = f"{result.stdout}\n{result.stderr}"
        assert result.returncode == 0, f"{launcher} failed:\n{output}"
        passed = [line for line in result.stdout.splitlines() if line.startswith('[PASS] ')]
        assert passed and '[FAIL]' not in result.stdout, output
        assert f'{len(passed)}/{len(passed)}' in result.stdout, output
        for check in _check_tool_files(ToolLocator.create()):
            # Tool labels are ASCII even when a detached Windows console's
            # localized prefix is decoded with a different code page.
            tool_label = check.name.split(' ', 1)[-1]
            assert any(f'{tool_label}: ' in line for line in passed), (
                f'{launcher} missing registered tool check {check.name}:\n{output}'
            )
