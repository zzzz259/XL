import json
import unittest

from xl_deploy.router_client import RouterControlClient


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class RouterControlClientTests(unittest.TestCase):
    def test_announcement_posts_stable_per_tier_idempotency_keys(self):
        requests = []

        def transport(request, timeout):
            requests.append(request)
            tier = json.loads(request.data)["tier"]
            return Response({"ok": True, "results": {tier: True}})

        client = RouterControlClient(
            "http://127.0.0.1:8784", "secret", transport=transport
        )

        self.assertEqual(
            client.announce(
                ("debug", "test"),
                "检测到更新，正在更新bot，期间将暂停服务",
                notification_id="transaction-1:starting",
            ),
            {"debug": True, "test": True},
        )
        payloads = [json.loads(request.data) for request in requests]
        self.assertEqual(
            [payload["notification_id"] for payload in payloads],
            [
                "transaction-1:starting:debug",
                "transaction-1:starting:test",
            ],
        )

    def test_release_note_uses_stable_key(self):
        requests = []

        def transport(request, timeout):
            requests.append(request)
            return Response({"ok": True, "results": {"main": True}})

        client = RouterControlClient(
            "http://127.0.0.1:8784", "secret", transport=transport
        )
        client.announce_release_note(
            ("main",), "Release notes", notification_id="transaction-2:release-note"
        )

        self.assertEqual(
            json.loads(requests[0].data)["notification_id"],
            "transaction-2:release-note:main",
        )

    def test_missing_stable_key_is_rejected(self):
        client = RouterControlClient(
            "http://127.0.0.1:8784", "secret", transport=lambda *_args, **_kwargs: None
        )
        with self.assertRaises(TypeError):
            client.announce(("debug",), "检测到更新，正在更新bot，期间将暂停服务")


if __name__ == "__main__":
    unittest.main()
