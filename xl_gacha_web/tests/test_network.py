"""Explicit direct connections, regardless of system/environment HTTP proxies."""

import json
import unittest
from unittest.mock import Mock, patch

from test_app import fake_token

from gacha_core import gacha


class DirectNetworkTests(unittest.TestCase):
    def test_authentication_ignores_proxy_environment_and_has_no_proxy_preflight(self):
        reply = Mock()
        reply.read.return_value = json.dumps(
            {"retCode": 0, "data": {"addresses": ["elpis-gw.haoplay.com.cn:8801"]}}
        ).encode()
        context = Mock()
        context.__enter__ = Mock(return_value=reply)
        context.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = context
        with (
            patch.object(
                gacha.socket, "create_connection", side_effect=OSError("proxy disabled")
            ) as connect,
            patch.object(
                gacha.urllib.request, "build_opener", return_value=opener
            ) as build,
            patch.dict(
                "os.environ",
                {
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "HTTPS_PROXY": "http://127.0.0.1:1",
                },
            ),
        ):
            result = gacha.authenticate(gacha.parse_credentials(fake_token()))
        self.assertEqual(result, ("elpis-gw.haoplay.com.cn", 8801))
        self.assertEqual(build.call_args.args[0].proxies, {})
        connect.assert_not_called()

    def test_game_socket_is_connected_to_official_gateway_without_connect_headers(self):
        sock = Mock()
        with patch.object(
            gacha.socket, "create_connection", return_value=sock
        ) as connect:
            client = gacha.gateway_connection("elpis-gw.haoplay.com.cn", 8801)
        self.assertIs(client, sock)
        connect.assert_called_once_with(("elpis-gw.haoplay.com.cn", 8801), timeout=12)
        sock.sendall.assert_not_called()

    def test_unapproved_gateway_is_rejected_before_any_network(self):
        with (
            patch.object(gacha.socket, "create_connection") as connect,
            self.assertRaises(gacha.GachaError) as caught,
        ):
            gacha.gateway_connection("evil.test", 8801)
        self.assertEqual(caught.exception.code, "GATEWAY_NOT_ALLOWED")
        connect.assert_not_called()
