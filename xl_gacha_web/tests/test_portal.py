import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import fake_token

import portal_login


class CandidateTests(unittest.TestCase):
    def test_explicit_custom_browser_overrides_last_remembered_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            edge = root / "msedge.exe"
            chrome = root / "chrome.exe"
            edge.touch()
            chrome.touch()
            (root / "starfall-browser.json").write_text(
                json.dumps({"path": str(chrome)})
            )
            with (
                patch.object(
                    portal_login.ManagedBrowser, "_endpoint_live", return_value=True
                ),
                patch.object(
                    portal_login.ManagedBrowser,
                    "pages",
                    return_value=[{"type": "page"}],
                ),
            ):
                b = portal_login.ManagedBrowser(
                    root,
                    candidates=[edge, chrome],
                    prefer_first=True,
                    startup_timeout=0.02,
                )
                self.assertEqual(b.executable, edge)

    def test_failed_launch_automatically_uses_next_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            edge = Path(folder) / "msedge.exe"
            chrome = Path(folder) / "chrome.exe"
            edge.touch()
            chrome.touch()
            process = Mock()
            process.poll.return_value = None
            with (
                patch(
                    "portal_login.subprocess.Popen",
                    side_effect=[OSError("private failure"), process],
                ),
                patch.object(
                    portal_login.ManagedBrowser,
                    "_endpoint_live",
                    lambda b: getattr(b, "executable", None) == chrome,
                ),
                patch.object(
                    portal_login.ManagedBrowser,
                    "pages",
                    return_value=[{"type": "page"}],
                ),
            ):
                b = portal_login.ManagedBrowser(
                    Path(folder) / "profile",
                    candidates=[edge, chrome],
                    startup_timeout=0.05,
                )
                self.assertEqual(b.executable, chrome)
                self.assertTrue(b.alive)
                b.close()

    def test_started_browser_without_debug_interface_falls_back_too(self):
        with tempfile.TemporaryDirectory() as folder:
            edge = Path(folder) / "msedge.exe"
            chrome = Path(folder) / "chrome.exe"
            edge.touch()
            chrome.touch()
            process = Mock()
            process.poll.return_value = None
            with (
                patch("portal_login.subprocess.Popen", return_value=process),
                patch.object(
                    portal_login.ManagedBrowser,
                    "_endpoint_live",
                    lambda b: getattr(b, "executable", None) == chrome,
                ),
                patch.object(
                    portal_login.ManagedBrowser,
                    "pages",
                    return_value=[{"type": "page"}],
                ),
            ):
                b = portal_login.ManagedBrowser(
                    Path(folder) / "profile",
                    candidates=[edge, chrome],
                    startup_timeout=0.02,
                    retry_interval=0.001,
                )
                self.assertEqual(b.executable, chrome)
                b.close()

    def test_running_process_with_unhealthy_debug_interface_is_not_reported_closed(
        self,
    ):
        b = portal_login.ManagedBrowser.__new__(portal_login.ManagedBrowser)
        b.process = Mock()
        b.process.poll.return_value = None
        b._endpoint_live = lambda: False
        b.started = time.monotonic() - 60
        b.last_alive = time.monotonic() - 12
        b.seen_ready = True
        self.assertTrue(b.alive)
        b.last_alive = time.monotonic() - 40
        self.assertFalse(b.alive)
        self.assertEqual(b.failure_code, "PORTAL_DEBUG_UNAVAILABLE")

    def test_launcher_exit_does_not_mean_browser_window_closed(self):
        browser = portal_login.ManagedBrowser.__new__(portal_login.ManagedBrowser)
        browser.process = Mock()
        browser.process.poll.return_value = 0
        browser._endpoint_live = lambda: True
        browser.pages = lambda: [{"type": "page"}]
        browser.started = time.monotonic() - 30
        self.assertTrue(browser.alive)

    def test_initial_startup_gets_time_to_create_its_browser_process(self):
        browser = portal_login.ManagedBrowser.__new__(portal_login.ManagedBrowser)
        browser.process = Mock()
        browser.process.poll.return_value = 0
        browser._endpoint_live = lambda: False
        browser.started = time.monotonic()
        self.assertTrue(browser.alive)

    def test_disconnected_browser_is_eventually_reported_closed(self):
        browser = portal_login.ManagedBrowser.__new__(portal_login.ManagedBrowser)
        browser.process = Mock()
        browser.process.poll.return_value = 0
        browser._endpoint_live = lambda: False
        browser.started = time.monotonic() - 60
        browser.seen_ready = True
        browser.last_alive = time.monotonic() - 10
        self.assertFalse(browser.alive)

    def test_navigation_transition_is_waited_for_instead_of_treated_as_a_foreign_session(
        self,
    ):
        browser = portal_login.ManagedBrowser.__new__(portal_login.ManagedBrowser)
        browser.port = 4567
        browser.pages = lambda: [
            {
                "type": "page",
                "url": "https://i.haoplay.com.cn/",
                "webSocketDebuggerUrl": "ws://127.0.0.1:4567/devtools/page/ABC",
            }
        ]
        with patch("portal_login.CDP") as client:
            client.return_value.__enter__.return_value.command.return_value = {
                "result": {"value": {"origin": "null", "values": {}}}
            }
            self.assertIsNone(browser.read())

    def test_only_current_official_origin_and_allowlisted_storage_are_used(self):
        source = {
            "origin": "https://i.haoplay.com.cn",
            "provider": "sms",
            "values": {
                "jwt": fake_token(uid=123),
                "HP_2070023": json.dumps(
                    {"jwt": fake_token(uid=456, appid="2070023", provider="taptap")}
                ),
                "privatePhoneNum": "NEVER_READ",
                "unrelated": fake_token(uid=789),
            },
        }
        candidate = portal_login.select_credential(source)
        self.assertEqual(candidate.open_id, "123")
        self.assertNotIn("signature", repr(candidate))
        source["origin"] = "https://i.haoplay.com.cn.evil.test"
        with self.assertRaises(portal_login.LoginError):
            portal_login.select_credential(source)

    def test_expired_or_wrong_provider_does_not_automatically_select_old_account(self):
        source = {
            "origin": "https://i.haoplay.com.cn",
            "provider": "sms",
            "values": {
                "jwt": fake_token(exp=1),
                "HP_2070023": {"jwt": fake_token(provider="taptap", appid="2070023")},
            },
        }
        self.assertIsNone(portal_login.select_credential(source))
        source["values"] = {
            "HP_2070021": {"jwt": fake_token(uid=1)},
            "lastjwt": fake_token(uid=2),
        }
        with self.assertRaises(portal_login.LoginError):
            portal_login.select_credential(source)

    def test_page_targets_are_exact_origin_and_loopback_endpoint_only(self):
        pages = [
            {
                "type": "page",
                "url": "https://i.haoplay.com.cn/?appid=2070021",
                "webSocketDebuggerUrl": "ws://127.0.0.1:4567/devtools/page/ABC",
            }
        ]
        self.assertEqual(
            portal_login.portal_target(pages, 4567), pages[0]["webSocketDebuggerUrl"]
        )
        for url in [
            "ws://evil.test:4567/devtools/page/ABC",
            "ws://127.0.0.1:4568/devtools/page/ABC",
        ]:
            pages[0]["webSocketDebuggerUrl"] = url
            self.assertIsNone(portal_login.portal_target(pages, 4567))


class FakeBrowser:
    candidate = fake_token(uid=555)

    def __init__(self, profile, **kwargs):
        self.profile = Path(profile)
        self.profile.mkdir(parents=True, exist_ok=True)
        (self.profile / "browser-session-test").write_text("SESSION_SECRET")
        self.alive = True

    def read(self):
        return {
            "origin": "https://i.haoplay.com.cn",
            "provider": "sms",
            "values": {"jwt": self.candidate},
        }

    def close(self):
        self.alive = False


class LoginManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.manager = portal_login.LoginManager(
            self.folder, self.query, browser_factory=FakeBrowser, timeout=3
        )

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def query(self, payload):
        return {
            "account": {
                "id": "account-test",
                "label": "测试号",
                "uid": "555",
                "channel": "sms",
            },
            "records": [],
            "summary": {"pull_count": 0},
        }

    def wait(self):
        for _ in range(100):
            status = self.manager.status()
            if status["phase"] in ("done", "error", "cancelled"):
                return status
            time.sleep(0.02)
        self.fail("login did not finish")

    def test_automatic_query_never_returns_credentials_and_temporary_profile_is_deleted(
        self,
    ):
        self.manager.start(False)
        result = self.wait()
        self.assertEqual(result["phase"], "done")
        self.assertEqual(result["report"]["account"]["uid"], "555")
        self.assertNotIn("signature", json.dumps(result))
        self.assertFalse(any(self.folder.rglob("browser-session-test")))

    def test_remember_is_explicit_and_saved_profile_is_reusable(self):
        self.manager.start(True)
        self.assertEqual(self.wait()["phase"], "done")
        profiles = self.manager.profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["label"], "测试号")
        self.manager.start(True, profiles[0]["id"])
        self.assertEqual(self.wait()["phase"], "done")
        self.manager.forget(profiles[0]["id"])
        self.assertEqual(self.manager.profiles(), [])

    def test_cancellation_closes_only_managed_browser_and_cleans_transient_session(
        self,
    ):
        class EmptyBrowser(FakeBrowser):
            def read(self):
                return {
                    "origin": "https://i.haoplay.com.cn",
                    "provider": "",
                    "values": {},
                }

        manager = portal_login.LoginManager(
            self.folder / "cancel", self.query, browser_factory=EmptyBrowser, timeout=3
        )
        try:
            manager.start(False)
            time.sleep(0.05)
            manager.cancel()
            for _ in range(100):
                if manager.status()["phase"] == "cancelled":
                    break
                time.sleep(0.02)
            self.assertEqual(manager.status()["phase"], "cancelled")
            self.assertFalse(
                any((self.folder / "cancel").rglob("browser-session-test"))
            )
        finally:
            manager.close()

    def test_arbitrary_profile_path_cannot_be_opened_or_deleted(self):
        for key in ["../outside", "C:/Users", "a" * 31, "b" * 32]:
            with self.subTest(key=key), self.assertRaises(portal_login.LoginError):
                self.manager.start(True, key)
            with self.subTest(key=key), self.assertRaises(portal_login.LoginError):
                self.manager.forget(key)


if __name__ == "__main__":
    unittest.main()
