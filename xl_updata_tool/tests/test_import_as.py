import os
import json
import io
import sys
from pathlib import Path

from app.features.importer.processing import ImportProcessor
from app.platform.logger import configure_logging
from app.platform.runtime_config import RuntimeConfig


def test_import_as_preserves_parent_environment_when_overriding_dotnet(tmp_path, monkeypatch):
    monkeypatch.setenv("XL_TEST_PARENT_ENV", "inherited-value")
    as_cli = tmp_path / "AssetStudio.CLI.dll"
    as_cli.write_bytes(b"placeholder")
    captured = {}

    class ExitedProcess:
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        returncode = 0

        def wait(self):
            return self.returncode

        def poll(self):
            return self.returncode

    def fake_popen(command, **kwargs):
        captured.update(kwargs)
        return ExitedProcess()

    monkeypatch.setattr(
        "app.features.importer.processing.subprocess.Popen", fake_popen
    )
    worker = ImportProcessor(
        [], str(tmp_path / "bundles"), str(tmp_path / "material"),
        [str(tmp_path / "dotnet.exe"), str(as_cli)],
        export_categories={"lua"},
        as_env={"DOTNET_ROOT": "bundled-runtime"},
    )

    worker._stage_export(None)

    assert captured["env"]["XL_TEST_PARENT_ENV"] == "inherited-value"
    assert captured["env"]["DOTNET_ROOT"] == "bundled-runtime"


def test_import_as_counts_unrepairable_bundle_as_failure(tmp_path):
    valid_bundle = tmp_path / "valid.bundle"
    invalid_bundle = tmp_path / "invalid.bundle"
    valid_bundle.write_bytes(b"UnityFS" + b"\x00" * 16)
    invalid_bundle.write_bytes(b"not a bundle")

    worker = ImportProcessor(
        [str(valid_bundle), str(invalid_bundle)],
        str(tmp_path),
        str(tmp_path / "material"),
        str(tmp_path / "AssetStudio.CLI.exe"),
    )

    success, fail = worker._stage_fix()

    assert (success, fail) == (1, 1)


def test_import_as_publishes_versioned_lua_and_cleans_material_staging(tmp_path):
    material_dir = tmp_path / "material"
    lua_dir = material_dir / "assets" / "lua"
    lua_dir.mkdir(parents=True)
    (lua_dir / "BaseCard.lua").write_text("card", encoding="utf-8")
    (lua_dir / "BaseWord_cn.lua").write_text("word", encoding="utf-8")
    output_dir = tmp_path / "output" / "lua"

    worker = ImportProcessor(
        [], str(tmp_path / "bundles"), str(material_dir), str(tmp_path / "AssetStudio.CLI.exe"),
        export_categories={"lua"}, version_timestamp=20260811, lua_output_dir=str(output_dir),
    )
    result = worker._sync_lua_output()

    assert result["version"] == 20260811
    assert result["character_sources"] is True
    assert (output_dir / "20260811" / "BaseCard.lua").is_file()
    assert list(lua_dir.iterdir()) == []


def test_import_as_forwards_lua_scan_and_batch_progress_to_ui(tmp_path, monkeypatch):
    material_dir = tmp_path / "material"
    lua_dir = material_dir / "assets" / "lua"
    lua_dir.mkdir(parents=True)
    jar = tmp_path / "unluac.jar"
    jar.write_bytes(b"jar")
    progress = []

    class Locator:
        def unluac_jar(self):
            return str(jar)

        def unluac_opmap(self):
            return str(tmp_path / "opmap.json")

    def fake_decompile(_lua_dir, _jar, _opmap, progress_cb, **_kwargs):
        progress_cb("识别 Lua 文件: 50/100")
        progress_cb("准备反编译 Lua: 50/100")
        progress_cb("正在反编译 Lua: 50/100")
        return 100, 0

    monkeypatch.setattr("app.features.importer.processing.ToolLocator.create", lambda: Locator())
    monkeypatch.setattr("app.features.importer.processing.decompile_lua_dir", fake_decompile)
    worker = ImportProcessor(
        [], str(tmp_path / "bundles"), str(material_dir), "AssetStudio.CLI.exe",
        progress_stage_callback=lambda *values: progress.append(values),
    )

    worker._decompile_lua()

    assert ("识别 Lua 文件", 50, 100) in progress
    assert ("准备反编译 Lua", 50, 100) in progress
    assert ("反编译 Lua", 50, 100) in progress


def test_import_as_keeps_old_output_when_assetstudio_fails(tmp_path, monkeypatch):
    material_dir = tmp_path / "material"
    old_lua = material_dir / "assets" / "lua" / "old.lua"
    old_lua.parent.mkdir(parents=True)
    old_lua.write_text("old", encoding="utf-8")
    as_cli = tmp_path / "AssetStudio.CLI.exe"
    as_cli.write_bytes(b"placeholder")

    class FailedProcess:
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        returncode = 1

        def wait(self):
            return self.returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(
        "app.features.importer.processing.subprocess.Popen",
        lambda *args, **kwargs: FailedProcess(),
    )
    worker = ImportProcessor(
        [], str(tmp_path / "bundles"), str(material_dir), str(as_cli), export_categories={"lua"}
    )

    total, message = worker._stage_export(None)

    assert total == 0
    assert "未替换已有产物" in message
    assert old_lua.read_text(encoding="utf-8") == "old"


def test_import_as_records_assetstudio_output_and_zero_artifact_result(tmp_path):
    cli_script = tmp_path / "fake_assetstudio.py"
    cli_script.write_text("print('simulated AssetStudio scan')\n", encoding="utf-8")
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    worker = ImportProcessor(
        [], str(tmp_path / "bundles"), str(tmp_path / "material"),
        [sys.executable, str(cli_script)], export_categories={"lua"},
    )

    file_count, message = worker._stage_export(None)

    assert file_count == 0
    assert "文件数为 0" in message
    command_log = next((session.directory / "external").rglob("stdout.log"))
    assert "simulated AssetStudio scan" in command_log.read_text(encoding="utf-8")
    manifest = json.loads(command_log.with_name("command.json").read_text(encoding="utf-8"))
    assert manifest["return_code"] == 0
    assert manifest["output_verified"] is False
    assert manifest["output_files_added"] == 0


def test_import_as_commits_partial_assetstudio_output(tmp_path, monkeypatch):
    session = configure_logging(RuntimeConfig(debug=False), logs_dir=tmp_path / "logs")
    material_dir = tmp_path / "material"
    old_lua = material_dir / "assets" / "lua" / "old.lua"
    old_lua.parent.mkdir(parents=True)
    old_lua.write_text("old", encoding="utf-8")
    as_cli = tmp_path / "AssetStudio.CLI.exe"
    as_cli.write_bytes(b"placeholder")

    class PartialProcess:
        returncode = 1

        def __init__(self, output_dir):
            output = output_dir / "assets" / "lua" / "new.lua"
            output.parent.mkdir(parents=True)
            output.write_text("new", encoding="utf-8")
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()

        def wait(self):
            return self.returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(
        "app.features.importer.processing.subprocess.Popen",
        lambda command, **kwargs: PartialProcess(Path(command[2])),
    )
    worker = ImportProcessor(
        [], str(tmp_path / "bundles"), str(material_dir), str(as_cli), export_categories={"lua"}
    )

    total, message = worker._stage_export(None)

    assert total == 1
    assert message == ""
    assert (material_dir / "assets" / "lua" / "new.lua").read_text(encoding="utf-8") == "new"
    command_manifest = json.loads(next((session.directory / "external").rglob("command.json")).read_text(encoding="utf-8"))
    assert command_manifest["output_files"] == [{"path": "assets/lua/new.lua", "size": 3}]


def test_import_as_replaces_only_selected_category(tmp_path):
    material_dir = tmp_path / "material"
    old_lua = material_dir / "assets" / "lua" / "old.lua"
    old_fgui = material_dir / "assets" / "fairygui" / "old.txt"
    old_lua.parent.mkdir(parents=True)
    old_fgui.parent.mkdir(parents=True)
    old_lua.write_text("old", encoding="utf-8")
    old_fgui.write_text("keep", encoding="utf-8")

    staging = tmp_path / "staging" / "material"
    new_lua = staging / "assets" / "lua" / "new.lua"
    new_lua.parent.mkdir(parents=True)
    new_lua.write_text("new", encoding="utf-8")

    worker = ImportProcessor(
        [], str(tmp_path / "bundles"), str(material_dir), str(tmp_path / "AssetStudio.CLI.exe"),
        export_categories={"lua"},
    )
    worker._working_material_dir = str(staging)
    worker._commit_staged_material()

    assert (material_dir / "assets" / "lua" / "new.lua").read_text(encoding="utf-8") == "new"
    assert not old_lua.exists()
    assert old_fgui.read_text(encoding="utf-8") == "keep"


def test_import_as_commits_all_character_resource_roots(tmp_path):
    material_dir = tmp_path / "material"
    old_model = material_dir / "assets" / "art" / "models" / "old.skel"
    old_burst = material_dir / "assets" / "art" / "texturesingle" / "bursthead" / "old.png"
    old_fgui = material_dir / "assets" / "fairygui" / "old.txt"
    old_model.parent.mkdir(parents=True)
    old_burst.parent.mkdir(parents=True)
    old_fgui.parent.mkdir(parents=True)
    old_model.write_text("old", encoding="utf-8")
    old_burst.write_bytes(b"old")
    old_fgui.write_text("keep", encoding="utf-8")

    staging = tmp_path / "staging" / "material"
    new_model = staging / "assets" / "art" / "models" / "new.skel"
    new_burst = staging / "assets" / "art" / "texturesingle" / "bursthead" / "new.png"
    new_lottery = staging / "assets" / "art" / "texturesingle" / "lotterybg" / "new.png"
    new_model.parent.mkdir(parents=True)
    new_burst.parent.mkdir(parents=True)
    new_lottery.parent.mkdir(parents=True)
    new_model.write_text("new", encoding="utf-8")
    new_burst.write_bytes(b"new burst")
    new_lottery.write_bytes(b"new lottery")

    worker = ImportProcessor(
        [], str(tmp_path / "bundles"), str(material_dir), str(tmp_path / "AssetStudio.CLI.exe"),
        export_categories={"character"},
    )
    worker._working_material_dir = str(staging)
    worker._commit_staged_material()

    assert (material_dir / "assets" / "art" / "models" / "new.skel").read_text(encoding="utf-8") == "new"
    assert (material_dir / "assets" / "art" / "texturesingle" / "bursthead" / "new.png").read_bytes() == b"new burst"
    assert (material_dir / "assets" / "art" / "texturesingle" / "lotterybg" / "new.png").read_bytes() == b"new lottery"
    assert not old_model.exists()
    assert not old_burst.exists()
    assert old_fgui.read_text(encoding="utf-8") == "keep"


def test_import_as_isolates_selected_bundles_for_assetstudio(tmp_path):
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    first = bundle_dir / "first.bundle"
    second = bundle_dir / "second.bundle"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    worker = ImportProcessor(
        [str(first)], str(bundle_dir), str(tmp_path / "material"),
        str(tmp_path / "AssetStudio.CLI.exe"), export_categories={"lua"},
        isolate_bundle_dir=True,
    )
    worker._prepare_cli_bundle_dir()

    try:
        assert Path(worker._cli_bundle_dir, "first.bundle").read_bytes() == b"first"
        assert not Path(worker._cli_bundle_dir, "second.bundle").exists()
    finally:
        worker._cleanup_cli_bundle_dir()

    assert worker._cli_bundle_dir == str(bundle_dir)


def test_import_as_rolls_back_all_categories_when_commit_fails(tmp_path, monkeypatch):
    material_dir = tmp_path / "material"
    old_lua = material_dir / "assets" / "lua" / "old.lua"
    old_fgui = material_dir / "assets" / "fairygui" / "old.txt"
    old_lua.parent.mkdir(parents=True)
    old_fgui.parent.mkdir(parents=True)
    old_lua.write_text("old lua", encoding="utf-8")
    old_fgui.write_text("old fgui", encoding="utf-8")

    staging = tmp_path / "staging" / "material"
    new_lua = staging / "assets" / "lua" / "new.lua"
    new_fgui = staging / "assets" / "fairygui" / "new.txt"
    new_lua.parent.mkdir(parents=True)
    new_fgui.parent.mkdir(parents=True)
    new_lua.write_text("new lua", encoding="utf-8")
    new_fgui.write_text("new fgui", encoding="utf-8")

    worker = ImportProcessor(
        [], str(tmp_path / "bundles"), str(material_dir), str(tmp_path / "AssetStudio.CLI.exe"),
        export_categories={"lua", "fgui"},
    )
    worker._working_material_dir = str(staging)
    original_replace = os.replace
    replace_calls = [0]

    def fail_on_second_new_move(source, destination):
        replace_calls[0] += 1
        if replace_calls[0] == 4:
            raise OSError("simulated commit failure")
        original_replace(source, destination)

    monkeypatch.setattr("app.features.importer.processing.os.replace", fail_on_second_new_move)

    try:
        worker._commit_staged_material()
    except OSError:
        pass
    else:
        raise AssertionError("commit failure must be propagated")

    assert old_lua.read_text(encoding="utf-8") == "old lua"
    assert old_fgui.read_text(encoding="utf-8") == "old fgui"
