import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace

from server_app.control_api import MAX_REQUEST_BYTES, build_control_server, read_api_token


TOKEN = "a" * 40


class FakeCoordinator:
    def __init__(self):
        self.job_id = "job-123"
        self.busy = False
        self.accepting = True
        self.jobs = {}

    def status(self):
        return {
            "environment": "test",
            "cdn_poll_enabled": False,
            "active_job_id": self.job_id if self.busy else None,
            "accepting": self.accepting,
        }

    def enqueue_manual(self):
        if self.busy:
            return None
        self.busy = True
        self.jobs[self.job_id] = {
            "job_id": self.job_id,
            "status": "queued",
            "result": None,
        }
        return self.job_id

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def stop_accepting(self):
        self.accepting = False


class TestControlAPI(unittest.TestCase):
    def setUp(self):
        self.coordinator = FakeCoordinator()
        config = SimpleNamespace(
            api_enabled=True,
            api_host="127.0.0.1",
            api_port=0,
            environment="test",
            cdn_poll_enabled=False,
        )
        self.server = build_control_server(config, self.coordinator, TOKEN, port_override=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, *, method="GET", token=None, data=None):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_health_is_loopback_safe_and_does_not_require_token(self):
        status, body = self.request("/healthz")

        self.assertEqual(status, 200)
        self.assertEqual(
            body,
            {"ok": True, "environment": "test", "cdn_poll_enabled": False},
        )

    def test_status_requires_bearer_and_returns_no_secrets(self):
        self.assertEqual(self.request("/api/v1/status")[0], 401)
        status, body = self.request("/api/v1/status", token=TOKEN)

        self.assertEqual(status, 200)
        self.assertEqual(body["environment"], "test")
        self.assertFalse(body["cdn_poll_enabled"])
        self.assertNotIn("token", json.dumps(body).lower())

    def test_manual_run_enqueues_and_job_can_be_queried(self):
        status, body = self.request(
            "/api/v1/updates/run-once", method="POST", token=TOKEN, data=b""
        )

        self.assertEqual(status, 202)
        self.assertEqual(body["job_id"], "job-123")
        status, job = self.request("/api/v1/jobs/job-123", token=TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(job["status"], "queued")
        self.assertEqual(self.request("/api/v1/jobs/missing", token=TOKEN)[0], 404)

    def test_manual_run_rejects_payload_and_concurrent_request(self):
        self.assertEqual(
            self.request(
                "/api/v1/updates/run-once", method="POST", token=TOKEN, data=b"{}"
            )[0],
            400,
        )
        self.coordinator.busy = True
        status, body = self.request(
            "/api/v1/updates/run-once", method="POST", token=TOKEN, data=b""
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["active_job_id"], "job-123")

    def test_manual_run_rejects_oversized_body(self):
        status, _ = self.request(
            "/api/v1/updates/run-once",
            method="POST",
            token=TOKEN,
            data=b"x" * (MAX_REQUEST_BYTES + 1),
        )
        self.assertEqual(status, 413)

    def test_manual_run_is_rejected_after_shutdown_begins(self):
        self.coordinator.stop_accepting()
        status, body = self.request(
            "/api/v1/updates/run-once", method="POST", token=TOKEN, data=b""
        )

        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "update service is stopping")

    def test_bad_token_fails_closed_and_unknown_route_is_not_found(self):
        self.assertEqual(self.request("/api/v1/status", token="wrong")[0], 401)
        self.assertEqual(self.request("/not-a-route")[0], 404)

    def test_enabled_api_requires_a_long_runtime_token(self):
        old = os.environ.get("XL_TEST_UPDATE_API_TOKEN")
        try:
            os.environ["XL_TEST_UPDATE_API_TOKEN"] = "short"
            with self.assertRaisesRegex(RuntimeError, "token"):
                read_api_token("XL_TEST_UPDATE_API_TOKEN")
            os.environ["XL_TEST_UPDATE_API_TOKEN"] = TOKEN
            self.assertEqual(read_api_token("XL_TEST_UPDATE_API_TOKEN"), TOKEN)
        finally:
            if old is None:
                os.environ.pop("XL_TEST_UPDATE_API_TOKEN", None)
            else:
                os.environ["XL_TEST_UPDATE_API_TOKEN"] = old

    def test_non_loopback_host_is_rejected_before_binding(self):
        config = SimpleNamespace(
            api_enabled=True,
            api_host="0.0.0.0",
            api_port=8791,
            environment="test",
            cdn_poll_enabled=False,
        )
        with self.assertRaisesRegex(ValueError, "loopback"):
            build_control_server(config, self.coordinator, TOKEN)


if __name__ == "__main__":
    unittest.main()
