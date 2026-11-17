"""Validate the original signing identity and upload three encrypted repository Secrets."""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from android_release import (
    ReleaseError,
    checked_output,
    decode_secrets,
    require,
    settings,
)
from toolchain import executable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="zzzz259/XL")
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--jdk", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Actually update GitHub Secrets; default is local verification only")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo), "REPOSITORY_INVALID")
    private = args.private_dir.resolve()
    require(all((private / name).is_file() for name in ("release.p12", "keystore-password.txt", "bilibili-client.json")), "PRIVATE_INPUT_MISSING")
    values = {
        "GACHA_ANDROID_KEYSTORE_BASE64": base64.b64encode((private / "release.p12").read_bytes()).decode(),
        "GACHA_ANDROID_KEYSTORE_PASSWORD": (private / "keystore-password.txt").read_text(encoding="utf-8"),
        "GACHA_BILIBILI_CLIENT_JSON": (private / "bilibili-client.json").read_text(encoding="utf-8"),
    }
    decode_secrets(values)
    env = {**os.environ, "GACHA_ANDROID_KEYSTORE_PASSWORD": values["GACHA_ANDROID_KEYSTORE_PASSWORD"]}
    der = checked_output([executable(args.jdk / "bin", "keytool"), "-exportcert", "-keystore", private / "release.p12",
                          "-alias", settings()["key_alias"], "-storepass:env", "GACHA_ANDROID_KEYSTORE_PASSWORD"], env=env)
    require(hashlib.sha256(der).hexdigest() == settings()["certificate_sha256"], "SIGNING_CERTIFICATE_MISMATCH")
    for name, value in values.items():
        if args.apply:
            result = subprocess.run(["gh", "secret", "set", name, "--repo", args.repo],
                                    input=value.encode("utf-8"), capture_output=True, check=False)
            require(result.returncode == 0, "SECRET_UPLOAD_FAILED:" + name)
        print(json.dumps({"name": name, "configured": args.apply, "validated": True}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except ReleaseError as error:
        raise SystemExit(str(error)) from None
