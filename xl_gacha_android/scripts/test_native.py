"""Compile real native sources, run WireTest and prepare Android JSON runtime tests.

No adb or application installation is performed. Run catalog-tests.jar via
app_process on an emulator/device; android.jar stubs cannot execute org.json.
"""

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from toolchain import executable, sdk_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--jdk", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    android, tools = sdk_paths(args.sdk)
    jdk = args.jdk / "bin"

    def run(argv):
        subprocess.run([str(v) for v in argv], cwd=ROOT, check=True)

    classes = out / "classes"
    classes.mkdir(exist_ok=True)
    run(
        [
            executable(jdk, "javac"),
            "--release",
            "8",
            "-encoding",
            "UTF-8",
            "-cp",
            android,
            "-d",
            classes,
            *sorted((ROOT / "src").rglob("*.java")),
            ROOT / "tests/CatalogUpdatesTest.java",
        ]
    )
    wire = out / "wire"
    wire.mkdir(exist_ok=True)
    run(
        [
            executable(jdk, "javac"),
            "--release",
            "8",
            "-encoding",
            "UTF-8",
            "-d",
            wire,
            ROOT / "src/cn/starfall/gacha/local/Wire.java",
            ROOT / "tests/WireTest.java",
        ]
    )
    run([executable(jdk, "java"), "-cp", wire, "cn.starfall.gacha.local.WireTest"])
    dex = out / "dex"
    dex.mkdir(exist_ok=True)
    run(
        [
            executable(jdk, "java"),
            "-cp",
            tools / "lib/d8.jar",
            "com.android.tools.r8.D8",
            "--min-api",
            "28",
            "--lib",
            android,
            "--output",
            dex,
            *sorted(classes.rglob("*.class")),
        ]
    )
    with zipfile.ZipFile(out / "catalog-tests.jar", "w") as archive:
        for path in dex.glob("*.dex"):
            archive.write(path, path.name)
    print(
        "Native compile and WireTest passed. Android org.json runtime execution is still required."
    )


if __name__ == "__main__":
    main()
