from pathlib import Path
import subprocess

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_source_launchers_run_self_check_with_project_python():
    """The source launchers must not fall back to a global Python install."""
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
        assert "19/19" in output, f"{launcher} did not run the full self-check:\n{output}"
