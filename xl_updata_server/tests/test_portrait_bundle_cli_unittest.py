"""Production config entrypoint tests; verified bundle boundary is explicit."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from server_app.portraits import cli


class BundleCliTests(unittest.TestCase):
    def fixture(self, root):
        metadata = {
            key: str(root / key)
            for key in (
                "updater_data_root",
                "prepared_source_root",
                "portrait_root",
                "work_root",
                "ledger_path",
                "store_path",
            )
        }
        metadata.update(
            enabled=False,
            instance_id="test",
            source_id="test-source",
            cdn_base="https://example.invalid/Bundles",
            publication_guards=[[str(root / "guard"), "a" * 64]],
            legacy_status_paths=[],
            resource_policy={
                "lock_path": str(root / "lock"),
                "updater_status_paths": [str(root / "guard")],
                "disk_roots": [str(root)],
            },
        )
        value = {
            "metadata": metadata,
            "bundle": {"trusted": "config"},
            "decrypt_key_env": "PORTRAIT_TEST_SECRET",
        }
        path = root / "config.json"
        path.write_text(json.dumps(value))
        return path, value

    def test_enabled_loader_only_accepts_verified_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            path, value = self.fixture(Path(temp))
            verified = SimpleNamespace(
                source_runtime=object(),
                package_runtime=object(),
                tool_fingerprint="f" * 64,
            )
            with (
                patch.dict(os.environ, {"PORTRAIT_TEST_SECRET": "ab" * 16}),
                patch(
                    "server_app.portraits.bundle.resolve_bundle", return_value=verified
                ) as resolver,
            ):
                config = cli.load_config(path)
            resolver.assert_called_once_with(value["bundle"], decrypt_key_hex="ab" * 16)
            self.assertIs(config.metadata.runtime, verified.source_runtime)
            self.assertEqual(config.metadata.tool_fingerprint, "f" * 64)
            self.assertIs(config.package_runtime, verified.package_runtime)

    def test_old_unchecked_runtime_forms_rejected(self):
        for key in ("runtime", "tool_fingerprint", "package_runtime"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp:
                path, value = self.fixture(Path(temp))
                (value if key == "package_runtime" else value["metadata"])[key] = {}
                path.write_text(json.dumps(value))
                with patch("server_app.portraits.bundle.resolve_bundle") as resolver:
                    with self.assertRaisesRegex(ValueError, "hand-built"):
                        cli.load_config(path)
                    resolver.assert_not_called()

    def test_secret_errors_do_not_echo_values(self):
        with tempfile.TemporaryDirectory() as temp:
            path, _ = self.fixture(Path(temp))
            with (
                patch.dict(os.environ, {"PORTRAIT_TEST_SECRET": "sensitive-invalid"}),
                self.assertRaises(ValueError) as caught,
            ):
                cli.load_config(path)
            self.assertNotIn("sensitive-invalid", str(caught.exception))

    def test_strict_json_rejects_duplicates_and_nonfinite(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            for raw in ('{"x":1,"x":2}', '{"x":NaN}'):
                path.write_text(raw)
                with self.assertRaises(ValueError):
                    cli.read_json(path)

    def test_disabled_does_not_load_config_or_secrets(self):
        with (
            patch.object(
                cli, "load_config", side_effect=AssertionError("must stay inert")
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(cli.main(["health", "--config", "missing"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"enabled": False})


if __name__ == "__main__":
    unittest.main()
