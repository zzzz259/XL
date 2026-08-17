import os
from pathlib import Path

from app.ui.workers.import_as import ImportASWorker


def test_import_as_counts_unrepairable_bundle_as_failure(tmp_path):
    valid_bundle = tmp_path / "valid.bundle"
    invalid_bundle = tmp_path / "invalid.bundle"
    valid_bundle.write_bytes(b"UnityFS" + b"\x00" * 16)
    invalid_bundle.write_bytes(b"not a bundle")

    worker = ImportASWorker(
        [str(valid_bundle), str(invalid_bundle)],
        str(tmp_path),
        str(tmp_path / "material"),
        str(tmp_path / "AssetStudio.CLI.exe"),
    )

    success, fail = worker._stage_fix()

    assert (success, fail) == (1, 1)


def test_import_as_keeps_old_output_when_assetstudio_fails(tmp_path, monkeypatch):
    material_dir = tmp_path / "material"
    old_lua = material_dir / "assets" / "lua" / "old.lua"
    old_lua.parent.mkdir(parents=True)
    old_lua.write_text("old", encoding="utf-8")
    as_cli = tmp_path / "AssetStudio.CLI.exe"
    as_cli.write_bytes(b"placeholder")

    class FailedProcess:
        stdout = ()
        returncode = 1

        def wait(self):
            return self.returncode

    monkeypatch.setattr(
        "app.ui.workers.import_as.subprocess.Popen",
        lambda *args, **kwargs: FailedProcess(),
    )
    worker = ImportASWorker(
        [], str(tmp_path / "bundles"), str(material_dir), str(as_cli), export_categories={"lua"}
    )

    total, message = worker._stage_export(None)

    assert total == 0
    assert "未替换已有产物" in message
    assert old_lua.read_text(encoding="utf-8") == "old"


def test_import_as_commits_partial_assetstudio_output(tmp_path, monkeypatch):
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
            self.stdout = ()

        def wait(self):
            return self.returncode

    monkeypatch.setattr(
        "app.ui.workers.import_as.subprocess.Popen",
        lambda command, **kwargs: PartialProcess(Path(command[2])),
    )
    worker = ImportASWorker(
        [], str(tmp_path / "bundles"), str(material_dir), str(as_cli), export_categories={"lua"}
    )

    total, message = worker._stage_export(None)

    assert total == 1
    assert message == ""
    assert (material_dir / "assets" / "lua" / "new.lua").read_text(encoding="utf-8") == "new"


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

    worker = ImportASWorker(
        [], str(tmp_path / "bundles"), str(material_dir), str(tmp_path / "AssetStudio.CLI.exe"),
        export_categories={"lua"},
    )
    worker._working_material_dir = str(staging)
    worker._commit_staged_material()

    assert (material_dir / "assets" / "lua" / "new.lua").read_text(encoding="utf-8") == "new"
    assert not old_lua.exists()
    assert old_fgui.read_text(encoding="utf-8") == "keep"


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

    worker = ImportASWorker(
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

    monkeypatch.setattr("app.ui.workers.import_as.os.replace", fail_on_second_new_move)

    try:
        worker._commit_staged_material()
    except OSError:
        pass
    else:
        raise AssertionError("commit failure must be propagated")

    assert old_lua.read_text(encoding="utf-8") == "old lua"
    assert old_fgui.read_text(encoding="utf-8") == "old fgui"
