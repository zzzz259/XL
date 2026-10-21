import unittest
from unittest.mock import patch

from starfall_refresh.core import SDK_URL, SafeError
from starfall_refresh.http import HTTP


class HTTPTests(unittest.TestCase):
    def test_requires_loopback_http_proxy(self):
        for proxy in (
            "",
            "https://127.0.0.1:7897",
            "http://remote.example:7897",
            "http://user:pass@127.0.0.1:7897",
        ):
            with self.subTest(proxy=proxy), self.assertRaises(SafeError):
                HTTP(proxy)

    def test_arbitrary_endpoint_rejected_before_network(self):
        with self.assertRaises(SafeError):
            HTTP()("https://example.com", b"", {})

    def test_proxy_failure_never_falls_back_to_direct(self):
        client = HTTP()
        with patch.object(
            client.opener, "open", side_effect=OSError("secret")
        ) as opened:
            with self.assertRaises(SafeError) as raised:
                client(SDK_URL, b"", {})
        self.assertEqual(str(raised.exception), "HTTP_FAILED")
        self.assertEqual(opened.call_count, 1)

    def test_gateway_rejects_untrusted_target(self):
        with patch("starfall_refresh.http.socket.create_connection") as connect:
            self.assertEqual(
                HTTP().gateway(["example.com:8801", "x.haoplay.com.cn:80"]), "UNKNOWN"
            )
        connect.assert_not_called()

    def test_gateway_uses_proxy_connect(self):
        class Socket:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def sendall(self, data):
                self.sent = data

            def recv(self, n):
                return b"HTTP/1.1 200 Connection established\r\n\r\n"

        sock = Socket()
        with patch(
            "starfall_refresh.http.socket.create_connection", return_value=sock
        ) as connect:
            self.assertEqual(
                HTTP().gateway(["elpis-gw.haoplay.com.cn:8801"]), "REACHABLE"
            )
        self.assertEqual(connect.call_args.args[0], ("127.0.0.1", 7897))
        self.assertTrue(sock.sent.startswith(b"CONNECT elpis-gw.haoplay.com.cn:8801 "))


if __name__ == "__main__":
    unittest.main()
