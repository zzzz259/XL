import json
import os
from types import SimpleNamespace

import app.features.importer.lua_decrypt as lua_decrypt
from app.features.importer.lua_decrypt import decompile_lua_dir
from app.platform.logger import configure_logging
from app.platform.runtime_config import RuntimeConfig


def test_lua_decompile_aggregates_successful_scan_decisions(tmp_path):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    lua_dir = tmp_path / "lua"
    lua_dir.mkdir()
    plaintext = lua_dir / "BaseWord_cn.lua.bytes"
    plaintext.write_bytes(b"local words = {}\n")
    unknown = lua_dir / "unrecognized.lua.bytes"
    unknown.write_bytes(b"not lua data")

    success, failed = decompile_lua_dir(str(lua_dir), "unused.jar", "unused.json")

    assert (success, failed) == (1, 0)
    assert (lua_dir / "BaseWord_cn.lua").read_text(encoding="utf-8") == "local words = {}\n"
    events = [json.loads(line) for line in session.events_log.read_text(encoding="utf-8").splitlines()]
    classified = [event for event in events if event["event"] == "lua.file.classified"]
    assert classified == []
    scan = next(event for event in events if event["event"] == "lua.scan.classification.complete")
    assert scan["details"]["candidate_count"] == 2
    assert scan["details"]["plaintext_count"] == 1
    assert scan["details"]["unknown_count"] == 1
    completed = next(event for event in events if event["event"] == "lua.file.complete")
    assert completed["details"]["output"].endswith("BaseWord_cn.lua")
    skipped = [event for event in events if event["event"] == "lua.file.skipped"]
    assert skipped == []


def test_lua_batch_preparation_reports_progress_and_observes_cancel(tmp_path, monkeypatch):
    lua_dir = tmp_path / "lua"
    lua_dir.mkdir()
    for index in range(250):
        (lua_dir / f"file_{index}.lua.bytes").write_bytes(f"bytecode-{index}".encode())

    monkeypatch.setattr(lua_decrypt, "classify", lambda data: ("bytecode", data))
    launched = []
    monkeypatch.setattr(lua_decrypt, "_run_batch", lambda *args, **kwargs: launched.append(args))
    messages = []
    cancel = {"requested": False}

    def on_progress(message):
        messages.append(message)
        if message == "准备反编译 Lua: 100/250":
            cancel["requested"] = True

    result = decompile_lua_dir(
        str(lua_dir),
        "unused.jar",
        "unused.json",
        progress_cb=on_progress,
        cancel_check=lambda: cancel["requested"],
    )

    assert result == (0, 0)
    assert any(message.startswith("识别 Lua 文件:") for message in messages)
    assert any(message.startswith("准备反编译 Lua:") for message in messages)
    assert "准备反编译 Lua: 100/250" in messages
    assert launched == []


def test_lua_batch_runner_uses_bounded_cancellable_process_wait(monkeypatch):
    observed = {}

    class Completed:
        returncode = 0
        xl_command_id = "cmd-test"
        xl_cancelled = False

    def fake_run(command, **kwargs):
        observed.update(kwargs)
        return Completed()

    monkeypatch.setattr(lua_decrypt, "run_external_process", fake_run)

    def cancel_check():
        return False

    lua_decrypt._run_batch(["java", "-jar", "unluac.jar"], lambda _message: None, cancel_check)

    assert observed["timeout"] == 1800
    assert observed["cancel_check"] is cancel_check


def test_lua_batch_writes_decompiled_output_and_cleans_staging(tmp_path, monkeypatch):
    lua_dir = tmp_path / "lua"
    lua_dir.mkdir()
    source = lua_dir / "sample.lua.bytes"
    source.write_bytes(b"compiled lua")
    monkeypatch.setattr(lua_decrypt, "classify", lambda data: ("bytecode", data))
    monkeypatch.setattr(lua_decrypt, "_java_exe", lambda: "java")

    def fake_run_batch(command, _emit, cancel_check):
        assert cancel_check is not None
        output_dir = command[command.index("--output") + 1]
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "sample.lua"), "w", encoding="utf-8") as stream:
            stream.write("local value = 7")
        return SimpleNamespace(returncode=0, xl_command_id="batch-test", xl_cancelled=False)

    monkeypatch.setattr(lua_decrypt, "_run_batch", fake_run_batch)
    progress = []

    result = decompile_lua_dir(
        str(lua_dir), "unluac.jar", "opmap.json", progress_cb=progress.append
    )

    assert result == (1, 0)
    assert (lua_dir / "sample.lua").read_text(encoding="utf-8") == "local value = 7"
    assert not source.exists()
    assert any(message.startswith("准备反编译 Lua:") for message in progress)
    assert any(message.startswith("正在反编译 Lua:") for message in progress)
    assert list(tmp_path.glob(".xl-lua-decompile-*")) == []
