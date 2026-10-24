"""Build a signed standalone Android APK using E-drive SDK components; no Gradle download."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

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
    for name in ("mobile-engine.js", "android-bridge.js", "tap-app-login.js"):
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
    parser.add_argument("--bilibili-config", type=Path)
    parser.add_argument("--application-id", default="cn.starfall.gacha.local")
    parser.add_argument("--label", default="星落抽卡手记")
    parser.add_argument("--version-code", type=int)
    parser.add_argument("--version-name")
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
    if out.is_relative_to(root.parent):
        parser.error("Build artifacts must stay outside the repository")
    if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", args.application_id):
        parser.error("Invalid application ID")
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

    run(["node", "--test", "tests/engine.test.cjs", "tests/tap-app-login.test.cjs"])
    for js in root.joinpath("web").glob("*.js"):
        run(["node", "--check", js])
    assets = out / "assets"
    if assets.exists():
        if not assets.resolve().is_relative_to(out):
            raise ValueError("Unsafe asset staging path")
        shutil.rmtree(assets)
    prepare_assets(desktop, root, assets)
    if args.bilibili_config:
        config = json.loads(args.bilibili_config.read_text(encoding="utf-8"))
        if any(
            not re.fullmatch(r"[0-9]{1,20}", str(config.get(k, "")))
            for k in ("merchant_id", "app_id", "server_id")
        ):
            parser.error("Invalid Bilibili client configuration")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", config.get("client_key", "")):
            parser.error("Invalid Bilibili client configuration")
        (assets / "bilibili-client.json").write_text(
            json.dumps(
                {
                    k: str(config[k])
                    for k in ("merchant_id", "app_id", "server_id", "client_key")
                }
            ),
            encoding="utf-8",
        )
    html = (assets / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        '<script src="/app.js" defer></script>',
        '<script src="./mobile-engine.js"></script><script src="./android-bridge.js"></script><script src="./app.js" defer></script>',
    )
    html = html.replace(
        '<div id="portal-progress"',
        '<button id="tap-start" type="button" class="outline-button" style="margin-top:12px">TapTap App 授权并查询</button><p class="field-help">官服：在官网选择 TapTap，进入授权页后点击“打开 TapTap App”；App 确认后手动返回手记，继续官网授权。</p><button id="tap-copy-diagnostics" type="button" class="text-button">复制 TapTap 脱敏诊断</button><div id="portal-progress"',
    )
    if args.bilibili_config:
        html = html.replace(
            '<div id="portal-progress"',
            '<button id="bili-start" class="primary-button" type="button" style="margin-top:12px"><span>B 服授权并查询</span><span>↗</span></button><p class="field-help">B 服：在官方哔哩哔哩 App 确认授权，自动识别 UID。每次查询均需授权，不受“记住官网登录”选项影响。</p><button id="bili-copy-diagnostics" class="text-button" type="button">复制 B 服脱敏诊断</button><div id="portal-progress"',
        )
        html = html.replace("官网登录，自动整理记录", "选择渠道，自动整理记录").replace(
            "打开专用的官方登录窗口，正常完成短信或 TapTap 登录，程序会自动获取凭据并查询，无需 F12 或复制粘贴。",
            "官服使用官网登录；B 服使用官方哔哩哔哩 App 授权。完成后自动查询全部已知卡池，并按渠道和账号保存。",
        )
        html = html.replace(
            '<section class="hero">',
            '<details id="bili-receipt" class="storage-note" hidden><summary>最近查询回执</summary><pre id="bili-receipt-text" style="white-space:pre-wrap"></pre></details><section class="hero">',
        )
    (assets / "index.html").write_text(html, encoding="utf-8")
    res = out / "res" / "drawable"
    res.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    Image.open(desktop / "data" / "app.ico").resize((192, 192)).save(res / "icon.png")
    manifest = (root / "AndroidManifest.xml").read_text(encoding="utf-8")
    manifest = manifest.replace(
        'package="cn.starfall.gacha.local"', f'package="{args.application_id}"'
    ).replace(
        'android:authorities="cn.starfall.gacha.local.images"',
        f'android:authorities="{args.application_id}.images"',
    )
    manifest = manifest.replace(
        'android:label="星落抽卡手记"',
        'android:label="' + escape(args.label, {'"': "&quot;"}) + '"',
    )
    if args.version_code is not None:
        manifest = re.sub(
            r'android:versionCode="[^"]+"',
            f'android:versionCode="{args.version_code}"',
            manifest,
        )
    if args.version_name:
        manifest = re.sub(
            r'android:versionName="[^"]+"',
            'android:versionName="' + escape(args.version_name, {'"': "&quot;"}) + '"',
            manifest,
        )
    if args.debug:
        manifest = manifest.replace(
            "<application ", '<application android:debuggable="true" '
        )
    (out / "AndroidManifest.xml").write_text(manifest, encoding="utf-8")
    # Fingerprint the actual source tree and baseline, rather than trusting a version label.
    hashes = {
        str(p.relative_to(root.parent)).replace("\\", "/"): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(
            [
                *root.joinpath("src").rglob("*.java"),
                *root.joinpath("web").glob("*.js"),
                *desktop.joinpath("static").iterdir(),
            ]
        )
        if p.is_file()
        and (p.suffix in (".java", ".js", ".css", ".html") or p.name == "baseline.json")
    }
    info = {
        "baseline": json.loads((root / "baseline.json").read_text(encoding="utf-8")),
        "application_id": args.application_id,
        "bilibili_enabled": bool(args.bilibili_config),
        "source_sha256": hashes,
    }
    (assets / "build-info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
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
            executable(jdk, "javac"),
            "--release",
            "8",
            "-Xlint:-options",
            "-encoding",
            "UTF-8",
            "-d",
            wire,
            root / "src/cn/starfall/gacha/local/BiliProtocol.java",
            root / "tests/BiliProtocolTest.java",
        ]
    )
    run(
        [
            executable(jdk, "java"),
            "-cp",
            wire,
            "cn.starfall.gacha.local.BiliProtocolTest",
        ]
    )
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
    version = re.search(r'android:versionName="([^"]+)"', manifest).group(1)
    filename_version = re.sub(r"[^A-Za-z0-9._-]", "_", version)
    apk = out / (
        "starfall-gacha-android-qa.apk"
        if args.debug
        else f"星落抽卡手记-Android-v{filename_version}.apk"
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
