import base64
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.android_release import (
    ReleaseError,
    event_metadata,
    gh_api,
    prepare_secrets,
    read_version,
    verify_checksums,
    write_checksums,
)


class AndroidReleaseTests(unittest.TestCase):
    def test_missing_github_release_is_distinct_from_auth_or_network_failure(self):
        for stdout, stderr in [(b'{"status":"404"}', b""), (b'{"message":"Not Found"}', b"gh: HTTP 404")]:
            with self.subTest(stdout=stdout), patch("scripts.android_release.subprocess.run", return_value=subprocess.CompletedProcess([], 1, stdout, stderr)):
                self.assertIsNone(gh_api("repos/example/repo/releases/tags/gacha-v0.4.2", missing_ok=True))
        with patch("scripts.android_release.subprocess.run", return_value=subprocess.CompletedProcess([], 1, b'{"status":"403"}', b"gh: HTTP 403")), self.assertRaisesRegex(ReleaseError, "GITHUB_API_FAILED"):
            gh_api("repos/example/repo/releases/tags/gacha-v0.4.2", missing_ok=True)

    def fixture(self, root):
        android = root / "xl_gacha_android"
        android.mkdir()
        (android / "AndroidManifest.xml").write_text(
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
            'package="cn.starfall.gacha.local" android:versionName="0.4.2" '
            'android:versionCode="25"/>', encoding="utf-8"
        )
        (android / "baseline.json").write_text(
            json.dumps({"integration_version": "0.4.2"}), encoding="utf-8"
        )
        notes = root / "docs/releases"
        notes.mkdir(parents=True)
        (notes / "gacha-v0.4.2.md").write_text("# 合成更新公告\n", encoding="utf-8")
        return read_version(root)

    def test_manifest_controls_version_and_release_note(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            version = self.fixture(root)
            self.assertEqual(version["version"], "0.4.2")
            self.assertEqual(version["version_code"], 25)
            self.assertEqual(version["tag"], "gacha-v0.4.2")
            (root / "xl_gacha_android/baseline.json").write_text(
                '{"integration_version":"0.4.1"}', encoding="utf-8"
            )
            with self.assertRaisesRegex(ReleaseError, "BASELINE_VERSION_MISMATCH"):
                read_version(root)

    def test_release_ref_and_version_must_match_before_signing(self):
        version = {"version": "0.4.2", "tag": "gacha-v0.4.2"}
        self.assertTrue(event_metadata(version, "push", "refs/tags/gacha-v0.4.2", "", False)["publish"])
        self.assertTrue(event_metadata(version, "workflow_dispatch", "refs/heads/main", "0.4.2", True)["publish"])
        self.assertFalse(event_metadata(version, "push", "refs/heads/debug", "", False)["publish"])
        for event, ref, requested, publish in [
            ("push", "refs/tags/v0.4.2", "", False),
            ("push", "refs/tags/gacha-v0.4.3", "", False),
            ("workflow_dispatch", "refs/heads/debug", "", True),
            ("workflow_dispatch", "refs/heads/main", "0.4.3", True),
            ("pull_request", "refs/pull/1/merge", "", False),
        ]:
            with self.subTest(event=event, ref=ref), self.assertRaises(ReleaseError):
                event_metadata(version, event, ref, requested, publish)

    def secrets(self):
        return {
            "GACHA_ANDROID_KEYSTORE_BASE64": base64.b64encode(b"synthetic-keystore").decode(),
            "GACHA_ANDROID_KEYSTORE_PASSWORD": "synthetic-password",
            "GACHA_BILIBILI_CLIENT_JSON": json.dumps({
                "merchant_id": "1", "app_id": "2", "server_id": "3",
                "client_key": "synthetic-client-key", "ignored_account_field": "must-not-copy",
            }),
        }

    def test_restore_only_expected_signing_files_outside_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            private = temp / "gacha-signing"
            result = prepare_secrets(private, temp, self.secrets())
            self.assertEqual((private / "release.p12").read_bytes(), b"synthetic-keystore")
            sdk = json.loads((private / "bilibili-client.json").read_text(encoding="utf-8"))
            self.assertEqual(set(sdk), {"merchant_id", "app_id", "server_id", "client_key"})
            self.assertEqual(set(result), {"keystore", "bilibili_config"})
            self.assertEqual(len(list(private.iterdir())), 2, "password is never written to disk")

    def test_bad_secrets_leave_no_partial_material_and_never_echo_values(self):
        for field, bad in [
            ("GACHA_ANDROID_KEYSTORE_BASE64", "secret invalid base64"),
            ("GACHA_ANDROID_KEYSTORE_PASSWORD", ""),
            ("GACHA_BILIBILI_CLIENT_JSON", '{"private-secret":"value"}'),
        ]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                private = Path(folder) / "gacha-signing"
                values = self.secrets()
                values[field] = bad
                with self.assertRaises(ReleaseError) as error:
                    prepare_secrets(private, Path(folder), values)
                self.assertFalse(private.exists())
                self.assertNotIn("private-secret", str(error.exception))
                self.assertNotIn("synthetic-password", str(error.exception))

    def test_material_path_cannot_escape_runner_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            with self.assertRaisesRegex(ReleaseError, "PRIVATE_PATH_INVALID"):
                prepare_secrets(temp.parent / "gacha-signing", temp, self.secrets())

    def test_checksum_validation_rejects_tampering_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            dist = Path(folder)
            (dist / "星落.apk").write_bytes(b"signed synthetic apk")
            (dist / "build-metadata.json").write_text("{}", encoding="utf-8")
            write_checksums(dist, ["星落.apk", "build-metadata.json"])
            self.assertEqual(set(verify_checksums(dist)), {"星落.apk", "build-metadata.json"})
            (dist / "星落.apk").write_bytes(b"tampered apk")
            with self.assertRaisesRegex(ReleaseError, "CHECKSUM_MISMATCH"):
                verify_checksums(dist)
            (dist / "SHA256SUMS.txt").write_text(
                hashlib.sha256(b"x").hexdigest() + "  ../outside.apk\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ReleaseError, "CHECKSUM_PATH_INVALID"):
                verify_checksums(dist)


if __name__ == "__main__":
    unittest.main()
