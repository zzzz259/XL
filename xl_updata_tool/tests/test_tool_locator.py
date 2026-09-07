import sys
from pathlib import Path

import pytest

from app.platform import tool_locator
from app.platform.tool_locator import ToolLocator, ToolNotFoundError, resource_root


def _make_bundled_runtimes(root: Path, *, java=True, dotnet=True):
    if java:
        (root / "runtimes" / "java" / "bin").mkdir(parents=True, exist_ok=True)
        (root / "runtimes" / "java" / "bin" / "java.exe").write_bytes(b"placeholder")
    if dotnet:
        (root / "runtimes" / "dotnet").mkdir(parents=True, exist_ok=True)
        (root / "runtimes" / "dotnet" / "dotnet.exe").write_bytes(b"placeholder")


def test_resource_root_follows_project_layout_in_dev():
    assert resource_root() == Path(tool_locator.__file__).resolve().parents[2]
    assert (resource_root() / "tools").is_dir()


def test_resource_root_uses_meipass_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert resource_root() == tmp_path
    assert ToolLocator.create().tools == tmp_path / "tools"


def test_java_prefers_bundled_runtime(tmp_path):
    _make_bundled_runtimes(tmp_path)
    locator = ToolLocator(root=tmp_path, frozen=False)

    assert locator.java() == str(tmp_path / "runtimes" / "java" / "bin" / "java.exe")


def test_java_falls_back_to_system_path_only_in_dev(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_locator.shutil, "which", lambda name: f"/system/{name}")
    locator = ToolLocator(root=tmp_path, frozen=False)

    assert locator.java() == "/system/java"
    assert locator.dotnet() == "/system/dotnet"


def test_java_never_falls_back_when_frozen(tmp_path, monkeypatch):
    # 冻结模式即使 PATH 里有 java 也不允许使用，避免干净机器上行为漂移。
    monkeypatch.setattr(tool_locator.shutil, "which", lambda name: f"/system/{name}")
    locator = ToolLocator(root=tmp_path, frozen=True)

    with pytest.raises(ToolNotFoundError):
        locator.java()


def test_dotnet_never_falls_back_when_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_locator.shutil, "which", lambda name: f"/system/{name}")
    locator = ToolLocator(root=tmp_path, frozen=True)

    with pytest.raises(ToolNotFoundError):
        locator.dotnet()


def test_java_dev_without_any_runtime_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_locator.shutil, "which", lambda name: None)
    locator = ToolLocator(root=tmp_path, frozen=False)

    with pytest.raises(ToolNotFoundError):
        locator.java()


def test_tool_not_found_is_file_not_found_for_legacy_handlers():
    # 既有调用方用 except FileNotFoundError 兜底，缺失运行时必须兼容该契约。
    assert issubclass(ToolNotFoundError, FileNotFoundError)


def test_assetstudio_command_uses_bundled_dotnet_when_frozen(tmp_path):
    _make_bundled_runtimes(tmp_path)
    locator = ToolLocator(root=tmp_path, frozen=True)

    command = locator.assetstudio_command()

    assert command == [
        str(tmp_path / "runtimes" / "dotnet" / "dotnet.exe"),
        str(tmp_path / "tools" / "AssetStudio" / "AssetStudio.CLI.dll"),
    ]


def test_assetstudio_command_falls_back_to_exe_in_plain_dev(tmp_path):
    # 开发机没有 bundled dotnet 时保持现状：exe 直启，依赖系统 .NET 8。
    locator = ToolLocator(root=tmp_path, frozen=False)

    assert locator.assetstudio_command() == [
        str(tmp_path / "tools" / "AssetStudio" / "AssetStudio.CLI.exe")
    ]


def test_assetstudio_command_prefers_bundled_dotnet_in_dev(tmp_path):
    _make_bundled_runtimes(tmp_path)
    locator = ToolLocator(root=tmp_path, frozen=False)

    command = locator.assetstudio_command()

    assert command[0] == str(tmp_path / "runtimes" / "dotnet" / "dotnet.exe")
    assert command[1].endswith("AssetStudio.CLI.dll")


def test_assetstudio_command_requires_dotnet_when_frozen(tmp_path):
    locator = ToolLocator(root=tmp_path, frozen=True)

    with pytest.raises(ToolNotFoundError):
        locator.assetstudio_command()


def test_dotnet_env_points_to_bundled_runtime(tmp_path):
    _make_bundled_runtimes(tmp_path)
    locator = ToolLocator(root=tmp_path, frozen=True)

    env = locator.dotnet_env()

    root = str(tmp_path / "runtimes" / "dotnet")
    assert env == {"DOTNET_ROOT": root, "DOTNET_ROOT_X64": root}
    assert locator.subprocess_env()["DOTNET_ROOT"] == root


def test_dotnet_env_empty_without_bundled_runtime(tmp_path):
    locator = ToolLocator(root=tmp_path, frozen=False)

    assert locator.dotnet_env() == {}


def test_ffmpeg_prefers_bundled_then_dev_path_fallback(tmp_path, monkeypatch):
    locator = ToolLocator(root=tmp_path, frozen=False)
    monkeypatch.setattr(tool_locator.shutil, "which", lambda name: f"/system/{name}")

    assert locator.ffmpeg() == "/system/ffmpeg"

    bundled = tmp_path / "tools" / "SpineViewer" / "ffmpeg.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"placeholder")
    assert locator.ffmpeg() == str(bundled)


def test_ffmpeg_never_uses_system_path_when_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_locator.shutil, "which", lambda name: f"/system/{name}")
    locator = ToolLocator(root=tmp_path, frozen=True)

    assert locator.ffmpeg() == str(tmp_path / "tools" / "SpineViewer" / "ffmpeg.exe")


def test_quickbms_and_fsb_extractor_live_under_subcontractors(tmp_path):
    # 回归：environment.py 曾把 quickbms 错查成 tools/quickbms/ 独立目录。
    locator = ToolLocator(root=tmp_path, frozen=True)

    assert locator.quickbms() == str(
        tmp_path / "tools" / "epic7_debank_v1_0" / "_subcontractors" / "quickbms.exe"
    )
    assert locator.fsb_extractor().endswith(str(Path("_subcontractors") / "fsb_aud_extr.exe"))


def test_lua_and_viewer_tool_paths(tmp_path):
    locator = ToolLocator(root=tmp_path, frozen=True)

    assert locator.unluac_jar().endswith(str(Path("lua") / "unluac.jar"))
    assert locator.unluac_opmap().endswith(str(Path("lua") / "opmap"))
    assert locator.spineviewer_cli().endswith("SpineViewerCLI.exe")
    assert locator.spineviewer_gui().endswith("SpineViewer.exe")
    assert locator.vgmstream().endswith("vgmstream-cli.exe")
