"""Cloud signing and publication for the Android handbook; no account credentials."""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "xl_gacha_android"))
from toolchain import executable, sdk_paths


class ReleaseError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise ReleaseError(code)


def read_version(repo=ROOT):
    manifest = ET.parse(repo / "xl_gacha_android/AndroidManifest.xml").getroot()
    android = "{http://schemas.android.com/apk/res/android}"
    version = manifest.get(android + "versionName", "")
    code = manifest.get(android + "versionCode", "")
    require(re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version), "VERSION_INVALID")
    require(re.fullmatch(r"[1-9][0-9]*", code) and int(code) <= 2147483647, "VERSION_CODE_INVALID")
    require(manifest.get("package") == "cn.starfall.gacha.local", "APPLICATION_ID_INVALID")
    baseline = json.loads((repo / "xl_gacha_android/baseline.json").read_text(encoding="utf-8"))
    require(baseline.get("integration_version") == version, "BASELINE_VERSION_MISMATCH")
    notes = repo / f"docs/releases/gacha-v{version}.md"
    require(notes.is_file() and notes.read_text(encoding="utf-8").strip(), "RELEASE_NOTES_MISSING")
    return {"version": version, "version_code": int(code), "tag": f"gacha-v{version}",
            "apk_name": f"starfall-gacha-android-v{version}.apk"}


def event_metadata(version, event, ref, requested, publish):
    require(not requested or requested == version["version"], "REQUESTED_VERSION_MISMATCH")
    require(event in ("push", "workflow_dispatch"), "RELEASE_EVENT_INVALID")
    if ref.startswith("refs/tags/"):
        require(ref == "refs/tags/" + version["tag"], "RELEASE_TAG_MISMATCH")
        publish = True
    else:
        require(ref in ("refs/heads/debug", "refs/heads/test", "refs/heads/main"), "RELEASE_REF_INVALID")
        if publish:
            require(ref == "refs/heads/main", "PUBLISH_REQUIRES_MAIN")
    return {**version, "publish": publish}


def private_path(directory, temp):
    directory, temp = directory.resolve(), temp.resolve()
    require(directory.parent == temp and directory.name == "gacha-signing", "PRIVATE_PATH_INVALID")
    require(not directory.is_relative_to(ROOT), "PRIVATE_PATH_INSIDE_CHECKOUT")
    return directory


def decode_secrets(env):
    try:
        key = base64.b64decode(env.get("GACHA_ANDROID_KEYSTORE_BASE64", ""), validate=True)
        require(key and len(key) <= 32768, "KEYSTORE_INVALID")
        password = env.get("GACHA_ANDROID_KEYSTORE_PASSWORD", "")
        require(password and not any(c in password for c in "\x00\r\n"), "KEYSTORE_PASSWORD_INVALID")
        source = json.loads(env.get("GACHA_BILIBILI_CLIENT_JSON", ""))
        config = {k: str(source[k]) for k in ("merchant_id", "app_id", "server_id", "client_key")}
        require(all(re.fullmatch(r"[0-9]{1,20}", config[k]) for k in ("merchant_id", "app_id", "server_id")), "BILIBILI_CONFIG_INVALID")
        require(re.fullmatch(r"[A-Za-z0-9_-]{8,128}", config["client_key"]), "BILIBILI_CONFIG_INVALID")
    except (ValueError, TypeError, KeyError, binascii.Error):
        raise ReleaseError("SIGNING_SECRETS_INVALID") from None
    return key, password, config


def prepare_secrets(directory, temp, env):
    directory = private_path(directory, temp)
    key, _, config = decode_secrets(env)
    require(not directory.exists(), "PRIVATE_DIRECTORY_EXISTS")
    directory.mkdir(mode=0o700)
    paths = {"keystore": directory / "release.p12", "bilibili_config": directory / "bilibili-client.json"}
    try:
        paths["keystore"].write_bytes(key)
        paths["bilibili_config"].write_text(json.dumps(config), encoding="utf-8")
        for path in paths.values():
            path.chmod(0o600)
    except Exception:
        shutil.rmtree(directory)
        raise
    return paths


def write_checksums(dist, names):
    lines = [hashlib.sha256((dist / name).read_bytes()).hexdigest() + "  " + name for name in names]
    (dist / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_checksums(dist):
    names = []
    for line in (dist / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
        require(match, "CHECKSUM_FORMAT_INVALID")
        digest, name = match.groups()
        require(Path(name).name == name and "/" not in name and "\\" not in name and not name.startswith("."), "CHECKSUM_PATH_INVALID")
        require(name not in names, "CHECKSUM_DUPLICATE")
        require(hashlib.sha256((dist / name).read_bytes()).hexdigest() == digest, "CHECKSUM_MISMATCH")
        names.append(name)
    require(names, "CHECKSUM_EMPTY")
    return names


def checked_output(args, env=None):
    result = subprocess.run([str(v) for v in args], capture_output=True, env=env, check=False)
    require(result.returncode == 0, "EXTERNAL_COMMAND_FAILED")
    return result.stdout


def settings():
    return json.loads((ROOT / "xl_gacha_android/release-config.json").read_text(encoding="utf-8"))


def verify_keystore(private, jdk):
    config = settings()
    der = checked_output([executable(jdk / "bin", "keytool"), "-exportcert", "-keystore", private / "release.p12",
                          "-alias", config["key_alias"], "-storepass:env", "GACHA_ANDROID_KEYSTORE_PASSWORD"])
    require(hashlib.sha256(der).hexdigest() == config["certificate_sha256"], "SIGNING_CERTIFICATE_MISMATCH")


def audit_apk(apk, sdk, jdk, version):
    _, tools = sdk_paths(sdk)
    output = checked_output([executable(jdk / "bin", "java"), "-jar", tools / "lib/apksigner.jar",
                             "verify", "--verbose", "--print-certs", apk]).decode("utf-8")
    match = re.search(r"certificate SHA-256 digest: ([a-f0-9]+)", output)
    require(match and match[1] == settings()["certificate_sha256"], "APK_CERTIFICATE_MISMATCH")
    badging = checked_output([executable(tools, "aapt2"), "dump", "badging", apk]).decode("utf-8")
    require("name='cn.starfall.gacha.local'" in badging and f"versionCode='{version['version_code']}'" in badging
            and f"versionName='{version['version']}'" in badging and "application-debuggable" not in badging, "APK_MANIFEST_MISMATCH")
    with zipfile.ZipFile(apk) as archive:
        info = json.loads(archive.read("assets/build-info.json"))
        require(info["application_id"] == "cn.starfall.gacha.local" and info["bilibili_enabled"] is True, "APK_FEATURES_MISMATCH")
        require(info["baseline"]["integration_version"] == version["version"], "APK_BASELINE_MISMATCH")
        for name, digest in info["source_sha256"].items():
            path = (ROOT / name).resolve()
            require(path.is_relative_to(ROOT) and path.is_file(), "SOURCE_PATH_INVALID")
            require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, "SOURCE_FINGERPRINT_MISMATCH")
        require(archive.read("assets/catalog.json") == (ROOT / "xl_gacha_web/data/catalog.json").read_bytes(), "APK_CATALOG_MISMATCH")
        require(not any(n.endswith((".p12", ".jks", ".keystore")) for n in archive.namelist()), "PRIVATE_FILE_IN_APK")
        require("assets/qa-host.js" not in archive.namelist(), "QA_HOST_IN_APK")
        dex = b"".join(archive.read(n) for n in archive.namelist() if n.endswith(".dex"))
        require(not any(n in dex for n in (b"MobileUiIntegration", b"CredentialsIntegration", b"VisualAssetsTest")), "QA_CLASSES_IN_APK")
    return {"certificate_sha256": match[1], "bilibili_enabled": True,
            "source_fingerprints_verified": len(info["source_sha256"])}


def build_release(args):
    version = read_version()
    private = private_path(args.private_dir, args.temp)
    verify_keystore(private, args.jdk)
    subprocess.run([sys.executable, "-X", "utf8", "-B", str(ROOT / "xl_gacha_android/build.py"),
                    "--sdk", str(args.sdk), "--jdk", str(args.jdk), "--output", str(args.output),
                    "--keystore", str(private / "release.p12"), "--key-alias", settings()["key_alias"],
                    "--store-pass-env", "GACHA_ANDROID_KEYSTORE_PASSWORD",
                    "--bilibili-config", str(private / "bilibili-client.json")], check=True)
    apk = args.output / f"星落抽卡手记-Android-v{version['version']}.apk"
    audit = audit_apk(apk, args.sdk, args.jdk, version)
    args.dist.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(apk, args.dist / version["apk_name"])
    shutil.copyfile(ROOT / f"docs/releases/{version['tag']}.md", args.dist / "RELEASE_NOTES.md")
    sha = checked_output(["git", "-C", ROOT, "rev-parse", "HEAD"]).decode().strip()
    metadata = {**version, **audit, "source_commit": sha,
                "workflow_run": os.environ.get("GITHUB_RUN_ID", "local"),
                "sha256": hashlib.sha256(apk.read_bytes()).hexdigest()}
    (args.dist / "build-metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    write_checksums(args.dist, [version["apk_name"], "build-metadata.json", "RELEASE_NOTES.md"])
    print(json.dumps(metadata, ensure_ascii=True))


def gh_api(path, method="GET", body=None, missing_ok=False):
    command = ["gh", "api", "--method", method, path]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(command, input=json.dumps(body).encode() if body is not None else None, capture_output=True, check=False)
    value = json.loads(result.stdout or b"{}")
    missing = str(value.get("status")) == "404" or b"HTTP 404" in result.stderr
    if missing_ok and result.returncode and missing:
        return None
    require(result.returncode == 0, "GITHUB_API_FAILED")
    return value


def publish_release(dist):
    version = read_version()
    files = verify_checksums(dist)
    require(set(files) == {version["apk_name"], "build-metadata.json", "RELEASE_NOTES.md"}, "RELEASE_FILES_INVALID")
    data = json.loads((dist / "build-metadata.json").read_text(encoding="utf-8"))
    sha = checked_output(["git", "rev-parse", "HEAD"]).decode().strip()
    require(data["source_commit"] == sha and data["version"] == version["version"], "RELEASE_SOURCE_MISMATCH")
    require(data["certificate_sha256"] == settings()["certificate_sha256"], "RELEASE_CERTIFICATE_MISMATCH")
    require(subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], check=False).returncode == 0, "PUBLISH_REQUIRES_MAIN")
    repo = os.environ.get("GH_REPO", "")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "GITHUB_REPOSITORY_INVALID")
    path = "repos/" + repo
    require(gh_api(path + "/releases/tags/" + version["tag"], missing_ok=True) is None, "RELEASE_ALREADY_EXISTS")
    ref = gh_api(path + "/git/ref/tags/" + version["tag"], missing_ok=True)
    if ref is None:
        ref = gh_api(path + "/git/refs", "POST", {"ref": "refs/tags/" + version["tag"], "sha": sha})
    target = ref["object"]
    for _ in range(4):
        if target["type"] == "commit":
            break
        require(target["type"] == "tag", "RELEASE_TAG_INVALID")
        target = gh_api(path + "/git/tags/" + target["sha"])["object"]
    require(target["type"] == "commit" and target["sha"] == sha, "RELEASE_TAG_COMMIT_MISMATCH")
    subprocess.run(["gh", "release", "create", version["tag"], str(dist / version["apk_name"]),
                    str(dist / "SHA256SUMS.txt"), str(dist / "build-metadata.json"), str(dist / "RELEASE_NOTES.md"), "--repo", repo,
                    "--title", f"星落抽卡手记 v{version['version']}", "--notes-file", str(dist / "RELEASE_NOTES.md"),
                    "--verify-tag", "--latest=false"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("metadata", "prepare", "build", "publish", "cleanup"))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--version", default="")
    parser.add_argument("--publish", choices=("true", "false"), default="false")
    parser.add_argument("--temp", type=Path, default=Path(os.environ.get("RUNNER_TEMP", ".")))
    parser.add_argument("--private-dir", type=Path)
    parser.add_argument("--sdk", type=Path)
    parser.add_argument("--jdk", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    if args.command == "metadata":
        value = event_metadata(read_version(), args.event, args.ref, args.version, args.publish == "true")
        if value["publish"]:
            require(subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"], check=False).returncode == 0, "PUBLISH_REQUIRES_MAIN")
        value["source_commit"] = checked_output(["git", "rev-parse", "HEAD"]).decode().strip()
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
                stream.writelines(f"{name}={str(result).lower() if isinstance(result, bool) else result}\n" for name, result in value.items())
        print(json.dumps(value))
    elif args.command == "prepare":
        prepare_secrets(args.private_dir, args.temp, os.environ)
        print("Signing inputs restored; secret values omitted.")
    elif args.command == "build":
        build_release(args)
    elif args.command == "publish":
        publish_release(args.dist)
    elif args.command == "cleanup":
        directory = private_path(args.private_dir, args.temp)
        if directory.exists():
            shutil.rmtree(directory)


if __name__ == "__main__":
    try:
        main()
    except ReleaseError as error:
        raise SystemExit(str(error)) from None
