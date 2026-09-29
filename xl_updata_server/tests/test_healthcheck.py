import sqlite3
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
