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
                        "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"},
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        deps = scratch / "PortraitSceneCLI.deps.json"
        deps.write_text(json.dumps(_portrait_scene_deps(), indent=2) + "\n", encoding="utf-8")
        for path in (output, runtime, deps):
            staged = tools / (path.name + ".new")
            shutil.copyfile(path, staged)
            os.replace(staged, tools / path.name)
    return tools / "PortraitSceneCLI.dll"


def _portrait_scene_deps() -> dict:
    """Minimal deps.json for the compiled CLI's app-local closure.

    The host only probes app-local assemblies listed here; the compiled CLI
    binds System.Text.Json 9.x, which shadows the framework 8.x copy, so those
    entries must carry an explicit assemblyVersion or the framework copy wins.
    """
    managed = (
        ("Spine", "0.16.28"),
        ("SpineRuntime21", "2.1.25"),
        ("SpineRuntime34", "3.4.2"),
        ("SpineRuntime35", "3.5.51"),
        ("SpineRuntime36", "3.6.53"),
        ("SpineRuntime37", "3.7.94"),
        ("SpineRuntime38", "3.8.99"),
        ("SpineRuntime40", "4.0.64"),
        ("SpineRuntime41", "4.1.54"),
        ("SpineRuntime42", "4.2.74"),
        ("SFML.Graphics", "2.6.1"),
        ("SFML.System", "2.6.1"),
        ("SFML.Window", "2.6.1"),
        ("SkiaSharp", "3.119.0"),
        ("System.Text.Json", "9.0.0"),
        ("System.Text.Encodings.Web", "9.0.0"),
        ("System.IO.Pipelines", "9.0.0"),
        ("NLog", "5.0.0"),
        ("FFMpegCore", "5.0.0"),
        ("Instances", "3.0.1"),
        ("PsdWriter", "0.16.18"),
    )
    shadowed = {"System.Text.Json", "System.Text.Encodings.Web", "System.IO.Pipelines"}
    target = ".NETCoreApp,Version=v8.0"
    app = "PortraitSceneCLI/1.0.0"
    targets = {app: {"dependencies": {}, "runtime": {"PortraitSceneCLI.dll": {}}}}
    libraries = {app: {"type": "project", "serviceable": False, "sha512": ""}}
    for name, version in managed:
        targets[app]["dependencies"][name] = version
        key = f"{name}/{version}"
        meta = {"assemblyVersion": "9.0.0.0", "fileVersion": "9.0.0.0"} if name in shadowed else {}
        targets[key] = {"runtime": {f"{name}.dll": meta}}
        libraries[key] = {"type": "package", "serviceable": False, "sha512": ""}
    return {
        "runtimeTarget": {"name": target, "signature": ""},
        "compilationOptions": {},
        "targets": {target: targets},
        "libraries": libraries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dotnet")
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    print(build(args.root, args.dotnet, args.work_dir))


if __name__ == "__main__":
    main()
