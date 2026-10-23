import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_app import fake_token

from app import WebServer


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = WebServer(("127.0.0.1", 0), archive_dir=cls.temp.name)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_page_and_assets_have_no_store_and_restrictive_csp(self):
        for path in ["/", "/app.js", "/style.css", "/theme.css", "/favicon.svg"]:
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])

    def test_browser_settings_and_environment_check_require_same_origin(self):
        self.assertEqual(
            self.request(
                "POST",
                "/api/environment/browser",
                "{}",
                {"Content-Type": "application/json"},
            )[0],
            403,
        )
        headers = {"Origin": self.origin, "Content-Type": "application/json"}
        self.assertEqual(
            self.request(
                "POST",
                "/api/environment/browser",
                json.dumps({"path": "missing.exe"}),
                headers,
            )[0],
            400,
        )
        status, _, body = self.request("GET", "/api/environment")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["phase"], "idle")
        with patch(
            "browser_support.https_probe",
            return_value={"status": "ok", "code": "REACHABLE"},
        ):
            status, _, body = self.request(
                "POST", "/api/environment/check", "{}", headers
            )
            self.assertEqual(status, 200)
            import time

            for _ in range(100):
                _, _, body = self.request("GET", "/api/environment")
                if json.loads(body)["phase"] == "done":
                    break
                time.sleep(0.02)
        self.assertEqual(len(json.loads(body)["checks"]), 5)

    def test_catalog_default_source_origin_guard_and_explicit_offline_mode(
        self,
    ):
        status, _, body = self.request("GET", "/api/catalog")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["source"].endswith("/gacha-config/current.json"))
        self.server.catalog_store.set_source("")
        status, _, body = self.request("GET", "/api/catalog")
        self.assertEqual(json.loads(body)["phase"], "unconfigured")
        self.assertEqual(
            self.request(
                "POST",
                "/api/catalog/source",
                "{}",
                {"Content-Type": "application/json"},
            )[0],
            403,
        )
        headers = {"Origin": self.origin, "Content-Type": "application/json"}
        status, _, body = self.request(
            "POST",
            "/api/catalog/source",
            json.dumps({"url": "http://unsafe/current.json"}),
            headers,
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "CATALOG_SOURCE_INVALID")
        status, _, body = self.request(
            "POST", "/api/catalog/check", json.dumps({"force": False}), headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["phase"], "unconfigured")

    def test_static_routes_do_not_expose_source_or_traverse_directories(self):
        for path in [
            "/app.py",
            "/../app.py",
            "/data/catalog.json",
            "/preview.json",
            "/missing",
        ]:
            self.assertEqual(self.request("GET", path)[0], 404)

    def test_cross_origin_and_wrong_host_cannot_submit_credentials(self):
        for headers in [
            {"Origin": "https://evil.test", "Content-Type": "application/json"},
            {
                "Host": "evil.test",
                "Origin": self.origin,
                "Content-Type": "application/json",
            },
        ]:
            self.assertEqual(self.request("POST", "/api/query", "{}", headers)[0], 403)

    def test_non_json_and_excessive_body_are_rejected(self):
        self.assertEqual(
            self.request("POST", "/api/query", "{}", {"Origin": self.origin})[0], 415
        )
        self.assertEqual(
            self.request(
                "POST",
                "/api/query",
                "x" * 70001,
                {"Origin": self.origin, "Content-Type": "application/json"},
            )[0],
            413,
        )

    def test_real_http_query_returns_report_without_echoing_input(self):
        with patch(
            "app.fetch_history",
            return_value=([], [{"selector": 1, "ret_code": 0, "record_count": 0}]),
        ):
            status, headers, body = self.request(
                "POST",
                "/api/query",
                json.dumps({"credential": fake_token()}),
                {"Origin": self.origin, "Content-Type": "application/json"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["summary"]["pull_count"], 0)
        self.assertNotIn(b"signature", body)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_invalid_json_returns_sanitized_error(self):
        status, _, body = self.request(
            "POST",
            "/api/query",
            "PRIVATE_NOT_JSON",
            {"Origin": self.origin, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertNotIn(b"PRIVATE", body)

    def test_taptap_wrapper_reaches_history_query_through_http(self):
        value = json.dumps(
            {"HP_2070023": {"jwt": fake_token(appid="2070023", provider="taptap")}}
        )
        with patch(
            "app.fetch_history",
            return_value=([], [{"selector": 1, "ret_code": 0, "record_count": 0}]),
        ):
            status, _, body = self.request(
                "POST",
                "/api/query",
                json.dumps({"credential": value}),
                {"Origin": self.origin, "Content-Type": "application/json"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["summary"]["record_count"], 0)
        self.assertNotIn(b"signature", body)

    def test_local_library_import_backup_and_identity_isolation(self):
        headers = {"Origin": self.origin, "Content-Type": "application/json"}
        original = {
            "records": [
                {
                    "pool_id": 1,
                    "timestamp": 1700000000,
                    "index": 1,
                    "record_id": 1,
                    "drops": [{"item_id": 123, "count": 1}],
                }
            ]
        }
        payload = json.dumps(
            {"content": json.dumps(original), "format": "json", "label": "本地导入测试"}
        )
        status, _, raw = self.request("POST", "/api/library/import", payload, headers)
        self.assertEqual(status, 200)
        key = json.loads(raw)["reports"][0]["account"]["id"]
        status, _, raw = self.request(
            "POST",
            "/api/library/rename",
            json.dumps({"id": key, "label": "主号"}),
            headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["account"]["label"], "主号")
        _, _, raw = self.request("GET", "/api/library")
        self.assertTrue(any(a["id"] == key for a in json.loads(raw)["accounts"]))
        self.assertEqual(
            self.request(
                "POST",
                "/api/library/delete",
                json.dumps({"id": key}),
                {"Content-Type": "application/json"},
            )[0],
            403,
        )
        _, _, raw = self.request("POST", "/api/library/backup", "{}", headers)
        self.assertEqual(json.loads(raw)["format"], "starfall-local-backup")
        self.assertEqual(
            self.request(
                "POST", "/api/library/delete", json.dumps({"id": key}), headers
            )[0],
            200,
        )
        self.assertEqual(
            self.request("POST", "/api/library/open", json.dumps({"id": key}), headers)[
                0
            ],
            400,
        )

    def test_query_automatically_archives_by_credential_identity(self):
        headers = {"Origin": self.origin, "Content-Type": "application/json"}
        raw_record = {
            "pool_id": 1,
            "timestamp": 1700000000,
            "index": 1,
            "record_id": 1,
            "drops": [{"item_id": 123, "count": 2}],
        }
        reports = []
        for uid in [901, 902]:
            with patch(
                "app.fetch_history",
                return_value=(
                    [raw_record],
                    [{"selector": 1, "ret_code": 0, "record_count": 1}],
                ),
            ):
                status, _, body = self.request(
                    "POST",
                    "/api/query",
                    json.dumps({"credential": fake_token(uid=uid)}),
                    headers,
                )
            self.assertEqual(status, 200)
            reports.append(json.loads(body))
        self.assertNotEqual(reports[0]["account"]["id"], reports[1]["account"]["id"])
        for report in reports:
            self.assertEqual(
                self.server.archive.open(report["account"]["id"])["summary"][
                    "pull_count"
                ],
                2,
            )
            self.assertNotIn("signature", json.dumps(report))

    def test_login_uses_same_origin_and_returns_only_archive_report(self):
        import time

        from test_portal import FakeBrowser

        self.assertEqual(
            self.request(
                "POST", "/api/login/start", "{}", {"Content-Type": "application/json"}
            )[0],
            403,
        )
        original = self.server.login.factory
        self.server.login.factory = FakeBrowser
        try:
            with patch(
                "app.fetch_history",
                return_value=([], [{"selector": 1, "ret_code": 0, "record_count": 0}]),
            ):
                status, _, body = self.request(
                    "POST",
                    "/api/login/start",
                    "{}",
                    {"Origin": self.origin, "Content-Type": "application/json"},
                )
                self.assertEqual(status, 200)
                for _ in range(100):
                    _, _, body = self.request("GET", "/api/login")
                    value = json.loads(body)
                    if value["phase"] in ("done", "error"):
                        break
                    time.sleep(0.02)
            self.assertEqual(value["phase"], "done")
            self.assertEqual(value["report"]["account"]["uid"], "555")
            self.assertNotIn(b"signature", body)
        finally:
            self.server.login.close()
            self.server.login.factory = original


if __name__ == "__main__":
    unittest.main()
