"""Compile PortraitSceneCLI against bundled .NET 8 assemblies without restore."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def build(root: Path, dotnet: str | None = None, work_dir: Path | None = None) -> Path:
    tools = root / "tools" / "SpineViewer"
    host = dotnet or shutil.which("dotnet")
    if not host:
        raise RuntimeError("dotnet host and an installed SDK are required to build PortraitSceneCLI")
    sdks = subprocess.run([host, "--list-sdks"], check=True, capture_output=True, text=True).stdout.splitlines()
    compilers = [
        Path(line.split("[", 1)[1].rstrip("]")) / line.split(" ", 1)[0] / "Roslyn" / "bincore" / "csc.dll"
        for line in sdks
        if "[" in line
    ]
    compilers = [path for path in compilers if path.is_file()]
    if not compilers:
        raise RuntimeError("No installed C# SDK compiler; install is not performed by this script")
    source = root / "native" / "portrait_scene" / "Program.cs"
    names = {
        "mscorlib.dll",
        "netstandard.dll",
        "Microsoft.CSharp.dll",
        "Spine.dll",
        "SFML.Graphics.dll",
        "SFML.System.dll",
        "SFML.Window.dll",
        "SkiaSharp.dll",
        "NLog.dll",
        "FFMpegCore.dll",
    }
    references = sorted(
        path
        for path in tools.glob("*.dll")
        if (path.name.startswith(("System.", "SpineRuntime")) or path.name in names)
        and path.name != "System.IO.Compression.Native.dll"
    )
    if not (tools / "System.Private.CoreLib.dll").is_file():
        raise RuntimeError(f"Bundled .NET 8 reference assemblies missing: {tools}")
    parent = work_dir or Path(os.environ.get("CODEX_INTERMEDIATE_DIR", root / "build" / "portrait-scene"))
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="portrait-scene-build-", dir=parent) as temporary:
        scratch = Path(temporary)
        output = scratch / "PortraitSceneCLI.dll"
        arguments = [
            "-nologo",
            "-nostdlib+",
            "-target:exe",
            "-langversion:12",
            "-nullable:enable",
            "-optimize+",
            "-deterministic+",
            "-debug-",
            f"-pathmap:{source.parent}=/src",
            f"-out:{output}",
            *(f"-r:{path}" for path in references),
            *(str(path) for path in sorted(source.parent.glob("*.cs"))),
        ]
        response = scratch / "compile.rsp"
        response.write_text("\n".join('"' + value + '"' for value in arguments), encoding="utf-8")
        subprocess.run([host, str(compilers[-1]), "@" + str(response)], check=True)
        runtime = scratch / "PortraitSceneCLI.runtimeconfig.json"
        runtime.write_text(
            json.dumps(
                {
                    "runtimeOptions": {
                        "tfm": "net8.0",
                        "includedFrameworks": [{"name": "Microsoft.NETCore.App", "version": "8.0.30"}],
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        for path in (output, runtime):
            staged = tools / (path.name + ".new")
            shutil.copyfile(path, staged)
            os.replace(staged, tools / path.name)
    return tools / "PortraitSceneCLI.dll"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dotnet")
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    print(build(args.root, args.dotnet, args.work_dir))


if __name__ == "__main__":
    main()
