"""外部工具与 bundled 运行时定位。

Release 打包后的用户机器上没有 Python/Java/.NET，所有外部依赖必须能从
应用自带的 ``tools/`` 与 ``runtimes/`` 目录解析。本模块是 app/ 内解析
这些路径的唯一入口，调用方不再各自拼接工具路径：

- 冻结（PyInstaller）模式：资源根为 ``sys._MEIPASS``；运行时只允许使用
  bundled 副本，找不到即抛 :class:`ToolNotFoundError`，避免静默回退到
  用户机器上并不存在的系统安装。
- 开发模式：优先使用 ``runtimes/`` 下的 bundled 副本（由构建脚本生成），
  不存在时回退系统 PATH，保持开发机现状可用。
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


class ToolNotFoundError(FileNotFoundError):
    """必需的外部工具或 bundled 运行时缺失。

    继承 FileNotFoundError，让既有的 ``except FileNotFoundError`` 容错
    （如 Lua 反编译的单文件重试）在缺失运行时场景下保持原有行为。
    """


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源根目录：冻结时为 ``sys._MEIPASS``，开发时为项目根目录。"""
    if _is_frozen():
        return Path(sys._MEIPASS)
    # 与 paths.get_base_dir 相同的上溯层级，集中一处避免两份目录约定漂移。
    from .paths import get_base_dir

    return Path(get_base_dir())


@dataclass(frozen=True)
class ToolLocator:
    """一次解析、随处复用的外部工具路径集。"""

    root: Path
    frozen: bool = False

    @classmethod
    def create(cls) -> "ToolLocator":
        return cls(root=resource_root(), frozen=_is_frozen())

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def runtimes(self) -> Path:
        return self.root / "runtimes"

    def java(self) -> str:
        """Java 可执行文件：bundled 优先，仅开发模式允许回退系统 PATH。"""
        bundled = self.runtimes / "java" / "bin" / "java.exe"
        if bundled.is_file():
            return str(bundled)
        if self.frozen:
            raise ToolNotFoundError(f"未找到随包 Java 运行时: {bundled}")
        system = shutil.which("java")
        if system:
            return system
        raise ToolNotFoundError("未找到 Java：runtimes/java 不存在且系统 PATH 中没有 java")

    def dotnet(self) -> str:
        """dotnet 可执行文件：bundled 优先，仅开发模式允许回退系统 PATH。"""
        bundled = self.runtimes / "dotnet" / "dotnet.exe"
        if bundled.is_file():
            return str(bundled)
        if self.frozen:
            raise ToolNotFoundError(f"未找到随包 .NET 运行时: {bundled}")
        system = shutil.which("dotnet")
        if system:
            return system
        raise ToolNotFoundError("未找到 dotnet：runtimes/dotnet 不存在且系统 PATH 中没有 dotnet")

    def dotnet_env(self) -> dict[str, str]:
        """bundled dotnet 的环境变量覆盖；无 bundled runtime 时为空 dict。

        framework-dependent 的 CLI 经 bundled dotnet 启动时，需要通过
        DOTNET_ROOT 指回 bundled 目录，否则会误读机器上其他 .NET 安装。
        """
        if not (self.runtimes / "dotnet" / "dotnet.exe").is_file():
            return {}
        root = str(self.runtimes / "dotnet")
        return {"DOTNET_ROOT": root, "DOTNET_ROOT_X64": root}

    def subprocess_env(self) -> dict[str, str]:
        """完整的 subprocess 环境：os.environ 叠加 bundled 运行时变量。"""
        env = dict(os.environ)
        env.update(self.dotnet_env())
        return env

    def assetstudio_dll(self) -> str:
        return str(self.tools / "AssetStudio" / "AssetStudio.CLI.dll")

    def assetstudio_command(self) -> list[str]:
        """AssetStudio.CLI 启动命令前缀（调用方在其后拼接业务参数）。

        AssetStudio.CLI 是 framework-dependent 构建：冻结模式或开发机存在
        bundled dotnet 时经 ``dotnet AssetStudio.CLI.dll`` 启动；开发机没有
        bundled runtime 时回退到 exe 直启（依赖开发机自行安装的 .NET 8），
        保持开发现状可用。
        """
        if not self.frozen and not (self.runtimes / "dotnet" / "dotnet.exe").is_file():
            return [str(self.tools / "AssetStudio" / "AssetStudio.CLI.exe")]
        return [self.dotnet(), self.assetstudio_dll()]

    def spineviewer_cli(self) -> str:
        return str(self.tools / "SpineViewer" / "SpineViewerCLI.exe")

    def spineviewer_gui(self) -> str:
        return str(self.tools / "SpineViewer" / "SpineViewer.exe")

    def ffmpeg(self) -> str:
        """FFmpeg：优先 tools/SpineViewer/ffmpeg.exe，开发模式回退系统 PATH。

        都找不到时返回裸命令名，沿用调用方现有的 FileNotFoundError 容错
        （合成导出会记录"FFmpeg 未找到"并跳过，不中断其他导出）。
        """
        bundled = self.tools / "SpineViewer" / "ffmpeg.exe"
        if bundled.is_file():
            return str(bundled)
        if not self.frozen:
            system = shutil.which("ffmpeg")
            if system:
                return system
            return "ffmpeg"
        return str(bundled)

    def vgmstream(self) -> str:
        return str(self.tools / "vgmstream" / "vgmstream-cli.exe")

    def quickbms(self) -> str:
        return str(self.tools / "epic7_debank_v1_0" / "_subcontractors" / "quickbms.exe")

    def fsb_extractor(self) -> str:
        return str(self.tools / "epic7_debank_v1_0" / "_subcontractors" / "fsb_aud_extr.exe")

    def unluac_jar(self) -> str:
        return str(self.tools / "lua" / "unluac.jar")

    def unluac_opmap(self) -> str:
        return str(self.tools / "lua" / "opmap")
