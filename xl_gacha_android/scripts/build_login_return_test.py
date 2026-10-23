"""Build an isolated synthetic share test APK, never an upgrade of the user app.

Does not install anything. The only product-source substitution disables initial
provider navigation. All callback, window and session handling is compiled as-is.
The generated QA key stays in the explicitly selected external output directory.
"""

import argparse
import os
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
    if out.is_relative_to(ROOT.parent):
        parser.error("QA output and development key must stay outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    android, tools = sdk_paths(args.sdk)
    jdk = args.jdk / "bin"

    def run(argv, env=None):
        subprocess.run([str(v) for v in argv], check=True, env=env)

    login = ROOT / "src/cn/starfall/gacha/local/LoginActivity.java"
    source = login.read_text(encoding="utf-8")
    initial = 'web.loadUrl(ORIGIN+"/?appid=2070021&l=cn")'
    if source.count(initial) != 1:
        raise ValueError("Initial provider navigation changed; review QA isolation")
    staged = out / "LoginActivity.java"
    staged.write_text(
        source.replace(initial, 'web.loadUrl("about:blank")'), encoding="utf-8"
    )
    classes, dex = out / "classes", out / "dex"
    classes.mkdir(exist_ok=True)
    dex.mkdir(exist_ok=True)
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
            *[p for p in (ROOT / "src").rglob("*.java") if p != login],
            staged,
            ROOT / "tests/LoginReturnIntegration.java",
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
    manifest = (ROOT / "AndroidManifest.xml").read_text(encoding="utf-8")
    manifest = manifest.replace(
        'package="cn.starfall.gacha.local"', 'package="cn.starfall.gacha.returnqa"'
    )
    manifest = manifest.replace(
        'android:label="星落抽卡手记"', 'android:label="QQ回跳临时测试"'
    )
    manifest = manifest.replace('android:icon="@drawable/icon"', "")
    manifest = manifest.replace(
        'android:name=".', 'android:name="cn.starfall.gacha.local.'
    )
    manifest = manifest.replace(
        "</manifest>",
        '<instrumentation android:name="cn.starfall.gacha.local.LoginReturnIntegration" android:targetPackage="cn.starfall.gacha.returnqa" /></manifest>',
    )
    (out / "AndroidManifest.xml").write_text(manifest, encoding="utf-8")
    run(
        [
            executable(tools, "aapt2"),
            "link",
            "-o",
            out / "base.apk",
            "--manifest",
            out / "AndroidManifest.xml",
            "-I",
            android,
            "--min-sdk-version",
            "28",
            "--target-sdk-version",
            "35",
        ]
    )
    with zipfile.ZipFile(out / "base.apk", "a", zipfile.ZIP_DEFLATED) as archive:
        for path in dex.glob("*.dex"):
            archive.write(path, path.name)
    run(
        [
            executable(tools, "zipalign"),
            "-f",
            "-p",
            "4",
            out / "base.apk",
            out / "aligned.apk",
        ]
    )
    env = os.environ.copy()
    env["STARFALL_QA_KEY_PASS"] = "isolated-qa-only"
    key = out / "qa-only-signing.p12"
    if not key.exists():
        run(
            [
                executable(jdk, "keytool"),
                "-genkeypair",
                "-keystore",
                key,
                "-alias",
                "qa",
                "-keyalg",
                "RSA",
                "-keysize",
                "2048",
                "-validity",
                "3650",
                "-dname",
                "CN=Isolated Share Regression",
                "-storepass:env",
                "STARFALL_QA_KEY_PASS",
                "-keypass:env",
                "STARFALL_QA_KEY_PASS",
            ],
            env,
        )
    run(
        [
            executable(jdk, "java"),
            "-jar",
            tools / "lib/apksigner.jar",
            "sign",
            "--ks",
            key,
            "--ks-key-alias",
            "qa",
            "--ks-pass",
            "env:STARFALL_QA_KEY_PASS",
            "--out",
            out / "login-return-qa.apk",
            out / "aligned.apk",
        ],
        env,
    )
    run(
        [
            executable(jdk, "java"),
            "-jar",
            tools / "lib/apksigner.jar",
            "verify",
            out / "login-return-qa.apk",
        ]
    )
    print("Isolated synthetic APK ready: cn.starfall.gacha.returnqa")


if __name__ == "__main__":
    main()
