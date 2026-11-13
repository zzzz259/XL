import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import browser_support as support


class BrowserSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def exe(self, name):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        return p

    def test_registry_path_and_custom_discovery_are_deduplicated_and_custom_is_first(
        self,
    ):
        edge = self.exe("registry/msedge.exe")
        chrome = self.exe("path/chrome.exe")
        custom = self.exe("portable/custom.exe")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(support, "registry_browser_paths", return_value=[edge]),
            patch(
                "browser_support.shutil.which",
                side_effect=lambda n: str(chrome) if n == "chrome.exe" else None,
            ),
        ):
            paths = support.discover_browsers([custom, edge])
        self.assertEqual(paths, [custom.resolve(), edge.resolve(), chrome.resolve()])

    def test_custom_setting_survives_restart_and_invalid_file_does_not_replace_it(self):
        exe = self.exe("portable/chrome.exe")
        settings = support.BrowserSettings(self.root / "data")
        settings.set_path(str(exe))
        self.assertEqual(
            support.BrowserSettings(self.root / "data").paths(), [exe.resolve()]
        )
        with self.assertRaises(ValueError):
            settings.set_path(str(self.root / "missing.exe"))
        self.assertEqual(settings.paths(), [exe.resolve()])
        settings.set_path("")
        self.assertEqual(settings.paths(), [])

    def test_diagnostics_do_not_log_credentials_urls_or_arbitrary_exception_text(self):
        log = support.DiagnosticLog(self.root, limit=1024)
        for i in range(30):
            log.emit(
                "browser_probe",
                browser="chrome.exe",
                code="DEBUG_UNAVAILABLE",
                credential="SECRET",
                url="https://secret/?jwt=SECRET",
                error="SECRET",
            )
        files = list((self.root / "diagnostics").glob("*.jsonl*"))
        self.assertTrue(files)
        for p in files:
            self.assertNotIn("SECRET", p.read_text(encoding="utf-8"))
            self.assertLess(p.stat().st_size, 2048)

    def test_environment_checks_writable_directory_loopback_and_browser_without_login(
        self,
    ):
        exe = self.exe("chrome.exe")
        checks = support.EnvironmentChecks(self.root / "data")
        with (
            patch.object(support, "discover_browsers", return_value=[exe]),
            patch.object(
                support,
                "https_probe",
                return_value={"status": "ok", "code": "REACHABLE"},
            ),
        ):
            result = checks.run()
        self.assertEqual(result["phase"], "done")
        self.assertEqual(
            {v["name"]: v["status"] for v in result["checks"]},
            {
                "browser": "ok",
                "directory": "ok",
                "loopback": "ok",
                "official_login": "ok",
                "official_auth": "ok",
            },
        )
        self.assertFalse(list((self.root / "data").glob("preflight-*")))
