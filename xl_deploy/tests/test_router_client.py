import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

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

    assert client.announce(
        ("debug",),
        "检测到更新，正在更新bot，期间将暂停服务",
        notification_id="tx-1:starting",
    ) == {"group": True}
    assert requests[0].get_header("Authorization") == "Bearer secret"
    assert json.loads(requests[0].data) == {
        "tier": "debug",
        "phase": "starting",
        "notification_id": "tx-1:starting:debug",
    }


def test_router_client_sends_release_note_text_to_authenticated_endpoint():
    requests = []

    def transport(request, timeout):
        requests.append(request)
        return Response({"ok": True, "results": {"group": True}})

    client = RouterControlClient("http://127.0.0.1:8784", "secret", transport=transport)

    assert client.announce_release_note(
        ("main",), "Release body", notification_id="tx-1:release"
    ) == {"group": True}
    assert json.loads(requests[0].data) == {
        "tier": "main",
        "phase": "release_note",
        "notification_id": "tx-1:release:main",
        "text": "Release body",
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

    assert [request.method for request in requests] == [
        "POST",
        "POST",
        "GET",
        "GET",
        "POST",
        "POST",
    ]
    assert [
        json.loads(request.data)["enabled"]
        for request in requests
        if request.method == "POST"
    ] == [True, True, False, False]


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8784@attacker.example", "http://user@127.0.0.1:8784"],
)
def test_router_client_rejects_non_loopback_authorities(url):
    with pytest.raises(ValueError, match="loopback"):
        RouterControlClient(url, "secret")


def test_default_transport_does_not_follow_router_redirects():
    sink_requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{self.server.server_port}/sink"
            )
            self.end_headers()

        def do_GET(self):
            sink_requests.append(self.path)
            payload = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = RouterControlClient(f"http://127.0.0.1:{server.server_port}", "secret")
    try:
        with pytest.raises(RuntimeError, match="request failed"):
            client.pause(("debug",))
        assert sink_requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
