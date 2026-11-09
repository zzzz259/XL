import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import run_server
from run_server import _executable_available, healthcheck


def write_config(root: Path) -> Path:
    (root / "tools" / "lua").mkdir(parents=True)
    (root / "tools" / "lua" / "unluac.jar").write_bytes(b"jar")
    (root / "tools" / "lua" / "opmap").write_text("map", encoding="utf-8")
    data = root / "data"
    data.mkdir()
    connection = sqlite3.connect(data / "state.sqlite")
    connection.close()
    config = root / "config.toml"
    config.write_text(
        '[server]\ntimezone="Asia/Shanghai"\n'
        '[paths]\ndata_dir="data"\nunluac_jar="tools/lua/unluac.jar"\n'
        'unluac_opmap="tools/lua/opmap"\n'
        '[cards]\nenabled=false\n'
        '[security]\nassetbundle_key="0123456789abcdef"\n',
        encoding="utf-8",
    )
    return config


def test_healthcheck_loads_config_and_opens_existing_state_database_read_only(tmp_path, monkeypatch):
    config = write_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)

    assert healthcheck(config) == 0
    assert (tmp_path / "data" / "state.sqlite").is_file()


def test_healthcheck_rejects_missing_database_without_creating_it(tmp_path, monkeypatch):
    config = write_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)
    (tmp_path / "data" / "state.sqlite").unlink()

    with pytest.raises(RuntimeError, match="state database"):
        healthcheck(config)

    assert not (tmp_path / "data" / "state.sqlite").exists()


def test_healthcheck_rejects_missing_required_lua_decoder_files(tmp_path, monkeypatch):
    config = write_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)
    (tmp_path / "tools" / "lua" / "unluac.jar").unlink()

    with pytest.raises(RuntimeError, match="unluac"):
        healthcheck(config)


def test_absolute_executable_path_must_be_executable(tmp_path, monkeypatch):
    binary = tmp_path / "java"
    binary.write_text("not executable", encoding="utf-8")
    monkeypatch.setattr(run_server.os, "access", lambda _path, _mode: False)

    assert not _executable_available(str(binary))


# ---- portraits -----------------------------------------------------------------


def _portrait_config(root: Path) -> Path:
    config = write_config(root)
    dotnet = root / "opt/dotnet"
    dotnet.parent.mkdir(parents=True, exist_ok=True)
    dotnet.write_text("dotnet", encoding="utf-8")
    scene_cli = root / "opt/scene/PortraitSceneCLI.dll"
    scene_cli.parent.mkdir(parents=True, exist_ok=True)
    scene_cli.write_text("cli", encoding="utf-8")
    assetstudio = root / "opt/assetstudio/AssetStudio.CLI.dll"
    assetstudio.parent.mkdir(parents=True, exist_ok=True)
    assetstudio.write_text("map", encoding="utf-8")
    tool_dir = root / "tool/xl_updata_tool"
    entry = tool_dir / "app/features/preview/portrait_headless.py"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("", encoding="utf-8")
    (root / "portrait-work").mkdir()
    (root / "portrait-outbox").mkdir()
    with config.open("a", encoding="utf-8") as stream:
        stream.write(
            "[portraits]\n"
            "enabled=true\n"
            'dotnet_path="opt/dotnet"\n'
            'scene_cli_path="opt/scene/PortraitSceneCLI.dll"\n'
            'assetstudio_cli="opt/assetstudio/AssetStudio.CLI.dll"\n'
            'tool_dir="tool/xl_updata_tool"\n'
            'work_dir="portrait-work"\n'
            'outbox_dir="portrait-outbox"\n'
            f"python_bin='{sys.executable}'\n",
        )
    return config


def test_healthcheck_skips_portrait_staging_checks_when_disabled(tmp_path, monkeypatch):
    config = write_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)
    calls = []
    monkeypatch.setattr(
        "run_server.subprocess.run", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    assert healthcheck(config) == 0
    assert calls == []


def test_healthcheck_accepts_staged_portrait_configuration(tmp_path, monkeypatch):
    config = _portrait_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)

    assert healthcheck(config) == 0


def test_healthcheck_probes_the_configured_render_python_without_writing(tmp_path, monkeypatch):
    config = _portrait_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("run_server.subprocess.run", fake_run)

    assert healthcheck(config) == 0
    (args, kwargs), = calls
    assert args[0] == sys.executable
    assert "-B" in args
    assert "app.features.preview.portrait_headless" in args[3]
    assert kwargs["cwd"] == str(tmp_path / "tool/xl_updata_tool")
    python_path = kwargs["env"]["PYTHONPATH"]
    assert python_path.split(os.pathsep)[0] == str(tmp_path / "tool/xl_updata_tool")


def test_healthcheck_rejects_missing_required_portrait_tool_paths(tmp_path, monkeypatch):
    config = _portrait_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)
    (tmp_path / "opt/scene/PortraitSceneCLI.dll").unlink()

    with pytest.raises(RuntimeError, match="scene_cli_path"):
        healthcheck(config)


def test_healthcheck_rejects_missing_required_portrait_tool_path_configuration(tmp_path, monkeypatch):
    config = _portrait_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)
    text = config.read_text(encoding="utf-8").replace('scene_cli_path="opt/scene/PortraitSceneCLI.dll"\n', "")
    config.write_text(text, encoding="utf-8")

    with pytest.raises(RuntimeError, match="scene_cli_path"):
        healthcheck(config)


def test_healthcheck_rejects_unusable_render_python(tmp_path, monkeypatch):
    config = _portrait_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)
    text = config.read_text(encoding="utf-8").replace(
        f"python_bin='{sys.executable}'", 'python_bin="missing-python"')
    config.write_text(text, encoding="utf-8")

    with pytest.raises(RuntimeError, match="render python"):
        healthcheck(config)


def test_healthcheck_surfaces_dependency_probe_failures(tmp_path, monkeypatch):
    config = _portrait_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)
    (tmp_path / "tool/xl_updata_tool/app/features/preview/portrait_headless.py").write_text(
        "raise ImportError('boom')\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="boom"):
        healthcheck(config)


def test_healthcheck_rejects_missing_tool_source_tree(tmp_path, monkeypatch):
    config = _portrait_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)
    shutil.rmtree(tmp_path / "tool/xl_updata_tool")

    with pytest.raises(RuntimeError, match="tool"):
        healthcheck(config)


def test_healthcheck_rejects_file_portrait_work_dir(tmp_path, monkeypatch):
    config = _portrait_config(tmp_path)
    monkeypatch.setattr("run_server._executable_available", lambda _value: True)
    shutil.rmtree(tmp_path / "portrait-work")
    (tmp_path / "portrait-work").write_text("not a directory", encoding="utf-8")

    with pytest.raises(RuntimeError, match="work_dir"):
        healthcheck(config)
