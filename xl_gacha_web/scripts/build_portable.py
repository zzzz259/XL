"""Build portable Windows runtime artifacts; excludes user-data and source."""

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urljoin

STATIC_FILES = (
    "app.js",
    "catalog-ui.js",
    "environment.js",
    "image-export.js",
    "local.js",
    "login.js",
    "demo.json",
    "favicon.svg",
    "index.html",
    "local.css",
    "style.css",
    "theme.css",
)


def prepare_resources(root, output):
    for directory, names in [
        ("static", STATIC_FILES),
        ("data", ("catalog.json", "update-source.json", "app.ico")),
    ]:
        target = output / directory
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copyfile(root / directory / name, target / name)


def prepare_instructions(root, output):
    base = "https://github.com/zzzz259/XL/blob/debug/xl_gacha_web/"
    text = (root / "README.md").read_text(encoding="utf-8")
    text = re.sub(
        r"\]\(([^)]+)\)", lambda match: "](" + urljoin(base, match[1]) + ")", text
    )
    (output / "使用说明.md").write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise SystemExit(
            "Windows x64 required: run scripts/build_windows.ps1 or the GitHub Actions Windows job."
        )
    if sys.version_info < (3, 11):  # noqa: UP036 - Friendly early error for unsupported build interpreters.
        raise SystemExit("Python 3.11+ required for this build.")
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=root,
        check=True,
    )
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required to verify JavaScript before packaging.")
    if node:
        subprocess.run(
            [node, "--test", "tests/image-export.test.cjs"], cwd=root, check=True
        )
        for source in (
            "app.js",
            "local.js",
            "image-export.js",
            "login.js",
            "environment.js",
            "catalog-ui.js",
        ):
            subprocess.run([node, "--check", str(root / "static" / source)], check=True)
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(output / "cache"))
    resources = output / "resources"
    if resources.exists():
        if not resources.resolve().is_relative_to(output):
            raise SystemExit("Unsafe resource staging path")
        shutil.rmtree(resources)
    prepare_resources(root, resources)
    subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onedir",
            "--windowed",
            "--name",
            "StarfallGacha",
            "--icon",
            str(root / "data" / "app.ico"),
            "--add-data",
            f"{resources / 'static'};static",
            "--add-data",
            f"{resources / 'data'};data",
            "--distpath",
            str(output / "dist"),
            "--workpath",
            str(output / "build"),
            "--specpath",
            str(output),
            str(root / "desktop.py"),
        ],
        cwd=root,
        env=env,
        check=True,
    )
    app = output / "dist" / "StarfallGacha"
    if (app / "user-data").exists():
        raise SystemExit("Refusing to package a distribution containing user-data.")
    prepare_instructions(root, app)
    license_file = Path(sys.base_prefix) / "LICENSE.txt"
    if license_file.is_file():
        shutil.copyfile(license_file, app / "Python-LICENSE.txt")
    target = output / "星落抽卡手记-本地版.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(app.parent))
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert {n.split("/")[0] for n in archive.namelist()} == {"StarfallGacha"}
        assert not any(
            "user-data/" in n or n.endswith(".sqlite3") for n in archive.namelist()
        )
    print(f"Portable archive: {target} ({target.stat().st_size / 1024 / 1024:.1f} MiB)")


if __name__ == "__main__":
    main()
