"""Build a signed standalone Android APK using E-drive SDK components; no Gradle download."""

import argparse
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from toolchain import executable, sdk_paths

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


def prepare_assets(desktop, root, assets):
    assets.mkdir(exist_ok=True)
    for name in STATIC_FILES:
        shutil.copyfile(desktop / "static" / name, assets / name)
    for name in ("mobile-engine.js", "android-bridge.js"):
        shutil.copyfile(root / "web" / name, assets / name)
    for name in ("catalog.json", "update-source.json"):
        shutil.copyfile(desktop / "data" / name, assets / name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--jdk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable WebView debugging in QA builds only",
    )
    parser.add_argument(
        "--keystore", type=Path, help="Existing release keystore outside the repository"
    )
    parser.add_argument("--key-alias", default="starfall")
    parser.add_argument("--store-pass-env", default="STARFALL_KEY_PASS")
    parser.add_argument(
        "--development-key",
        action="store_true",
        help="Generate an external development key; NOT a release upgrade identity",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    desktop = root.parent / "xl_gacha_web"
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    android, tools = sdk_paths(args.sdk.resolve())
    jdk = args.jdk.resolve() / "bin"
    if not args.keystore and not args.development_key:
        parser.error(
            "Specify existing --keystore or explicitly opt in to --development-key"
        )
    key = (args.keystore or out.parent / "starfall-android-development.p12").resolve()
    if key.is_relative_to(root.parent):
        parser.error("Signing keys must be outside the repository")
    if args.keystore and not key.is_file():
        parser.error("Release keystore must already exist")
    if args.keystore and not os.environ.get(args.store_pass_env):
        parser.error("Set the requested password environment variable")

    def run(argv, cwd=root):
        subprocess.run([str(v) for v in argv], cwd=cwd, check=True)

    run(["node", "--test", "tests/engine.test.cjs"])
    for js in root.joinpath("web").glob("*.js"):
        run(["node", "--check", js])
    assets = out / "assets"
    if assets.exists():
        if not assets.resolve().is_relative_to(out):
            raise ValueError("Unsafe asset staging path")
        shutil.rmtree(assets)
    prepare_assets(desktop, root, assets)
    html = (assets / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        '<script src="/app.js" defer></script>',
        '<script src="./mobile-engine.js"></script><script src="./android-bridge.js"></script><script src="./app.js" defer></script>',
    )
    (assets / "index.html").write_text(html, encoding="utf-8")
    res = out / "res" / "drawable"
    res.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    Image.open(desktop / "data" / "app.ico").resize((192, 192)).save(res / "icon.png")
    manifest = (root / "AndroidManifest.xml").read_text(encoding="utf-8")
    if args.debug:
        manifest = manifest.replace(
            "<application ", '<application android:debuggable="true" '
        )
    (out / "AndroidManifest.xml").write_text(manifest, encoding="utf-8")
    classes = out / "classes"
    classes.mkdir(exist_ok=True)
    dex = out / "dex"
    dex.mkdir(exist_ok=True)
    sources = list((root / "src").rglob("*.java"))
    run(
        [
            executable(jdk, "javac"),
            "--release",
            "8",
            "-Xlint:-options",
            "-encoding",
            "UTF-8",
            "-cp",
            android,
            "-d",
            classes,
            *sources,
        ]
    )
    wire = out / "wire-test"
    wire.mkdir(exist_ok=True)
    run(
        [
            executable(jdk, "javac"),
            "--release",
            "8",
            "-Xlint:-options",
            "-d",
            wire,
            root / "src/cn/starfall/gacha/local/Wire.java",
            root / "tests/WireTest.java",
        ]
    )
    run([executable(jdk, "java"), "-cp", wire, "cn.starfall.gacha.local.WireTest"])
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
            *classes.rglob("*.class"),
        ]
    )
    run(
        [
            executable(tools, "aapt2"),
            "compile",
            "--dir",
            out / "res",
            "-o",
            out / "resources.zip",
        ]
    )
    base = out / "base.apk"
    run(
        [
            executable(tools, "aapt2"),
            "link",
            "-o",
            base,
            "--manifest",
            out / "AndroidManifest.xml",
            "-I",
            android,
            "-A",
            assets,
            "--min-sdk-version",
            "28",
            "--target-sdk-version",
            "35",
            out / "resources.zip",
        ]
    )
    with zipfile.ZipFile(base, "a", zipfile.ZIP_DEFLATED) as z:
        for p in dex.glob("*.dex"):
            z.write(p, p.name)
    aligned = out / "aligned.apk"
    run([executable(tools, "zipalign"), "-f", "-p", "4", base, aligned])
    # Stable local development key, kept outside repository and distribution.
    env = os.environ.copy()
    if args.development_key and not args.keystore:
        env[args.store_pass_env] = "android-local-build"
    if not key.exists():
        subprocess.run(
            [
                str(executable(jdk, "keytool")),
                "-genkeypair",
                "-keystore",
                str(key),
                "-alias",
                args.key_alias,
                "-keyalg",
                "RSA",
                "-keysize",
                "2048",
                "-validity",
                "10000",
                "-dname",
                "CN=Starfall Local Development",
                "-storepass:env",
                args.store_pass_env,
                "-keypass:env",
                args.store_pass_env,
            ],
            env=env,
            check=True,
        )
    apk = out / (
        "starfall-gacha-android-qa.apk" if args.debug else "星落抽卡手记-安卓版.apk"
    )
    subprocess.run(
        [
            str(executable(jdk, "java")),
            "-jar",
            str(tools / "lib/apksigner.jar"),
            "sign",
            "--ks",
            str(key),
            "--ks-key-alias",
            args.key_alias,
            "--ks-pass",
            "env:" + args.store_pass_env,
            "--out",
            str(apk),
            str(aligned),
        ],
        env=env,
        check=True,
    )
    run(
        [
            executable(jdk, "java"),
            "-jar",
            tools / "lib/apksigner.jar",
            "verify",
            "--verbose",
            apk,
        ]
    )
    print(f"APK ready: {apk} ({apk.stat().st_size / 1024 / 1024:.2f} MiB)")


if __name__ == "__main__":
    main()
