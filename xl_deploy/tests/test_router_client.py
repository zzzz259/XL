import json

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


def test_router_client_authenticates_and_posts_exact_announcement_text():
    requests = []

    def transport(request, timeout):
        requests.append(request)
        return Response({"ok": True, "results": {"group": True}})

    client = RouterControlClient("http://127.0.0.1:8784", "secret", transport=transport)

    assert client.announce(("debug",), "检测到更新，正在更新bot，期间将暂停服务") == {"group": True}
    assert requests[0].get_header("Authorization") == "Bearer secret"
    assert json.loads(requests[0].data) == {
        "tier": "debug", "phase": "starting"
    }


def test_router_client_sends_release_note_text_to_authenticated_endpoint():
    requests = []

    def transport(request, timeout):
        requests.append(request)
        return Response({"ok": True, "results": {"group": True}})

    client = RouterControlClient("http://127.0.0.1:8784", "secret", transport=transport)

    assert client.announce_release_note(("main",), "Release body") == {"group": True}
    assert json.loads(requests[0].data) == {
        "tier": "main", "phase": "release_note", "text": "Release body"
    }


def test_router_client_pause_resume_and_drain_use_per_tier_control():
    requests = []

    def transport(request, timeout):
        requests.append(request)
        if request.method == "GET":
            return Response({"ok": True, "drained": True})
        return Response({"ok": True})

    client = RouterControlClient("http://127.0.0.1:8784", "secret", transport=transport)

    client.pause(("debug", "test"))
    assert client.drain(("debug", "test"), timeout_seconds=1)
    client.resume(("debug", "test"))

    assert [request.method for request in requests] == ["POST", "POST", "GET", "GET", "POST", "POST"]
    assert [json.loads(request.data)["enabled"] for request in requests if request.method == "POST"] == [
        True, True, False, False
    ]
