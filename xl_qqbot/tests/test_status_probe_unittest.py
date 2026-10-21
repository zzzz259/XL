from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot_app.status_monitor import Status, classify_sample
from bot_app.status_probe import (
    _load_credentials,
    _RejectRedirects,
    build_login_preflight_probe,
)


class LoginPreflightProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.credentials_path = Path(self.temp_dir.name) / "probe.toml"
        self.credentials_path.write_text(
            'open_id = "account-id"\ntoken = "opaque-token"\nchannel = "channel-a"\n',
            encoding="utf-8",
        )
        if os.name == "posix":
            self.credentials_path.chmod(0o600)

    @staticmethod
    def _http_response(payload):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response

    async def test_successful_preflight_checks_allowlisted_gateway_without_game_session(self):
        response = {
            "retCode": 0,
            "data": {
                "maintainState": 1,
                "addresses": [{"host": "gw1.haoplay.com.cn", "port": 8801}],
            },
        }
        with (
            patch("bot_app.status_probe.build_opener") as opener_factory,
            patch("bot_app.status_probe.socket.create_connection") as connect,
        ):
            opener_factory.return_value.open.return_value = self._http_response(response)
            sample = await build_login_preflight_probe(self.credentials_path)()

        self.assertIs(opener_factory.call_args.args[0], _RejectRedirects)
        request = opener_factory.return_value.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://elpis-accverify.haoplay.com.cn/v1/account/login")
        self.assertEqual(
            payload,
            {
                "openId": "account-id",
                "token": "opaque-token",
                "platform": 1,
                "channel": "channel-a",
                "buildVersion": "",
                "authRemote": True,
            },
        )
        connect.assert_called_once()
        self.assertEqual(connect.call_args.args[0], ("gw1.haoplay.com.cn", 8801))
        self.assertTrue(sample.accepted)
        self.assertEqual(sample.ret_code, 0)
        self.assertTrue(sample.maintain_state_present)
        self.assertEqual(sample.maintain_state, 1)
        self.assertTrue(sample.gateway_reachable)

    async def test_rejects_gateway_outside_allowlist_without_connecting(self):
        response = {
            "retCode": 0,
            "data": {"maintainState": 0, "addresses": [{"host": "example.com", "port": 8801}]},
        }
        with (
            patch("bot_app.status_probe.build_opener") as opener_factory,
            patch("bot_app.status_probe.socket.create_connection") as connect,
        ):
            opener_factory.return_value.open.return_value = self._http_response(response)
            sample = await build_login_preflight_probe(self.credentials_path)()

        connect.assert_not_called()
        self.assertFalse(sample.gateway_reachable)
        self.assertEqual(sample.error_class, "PARSE_ERROR")

    async def test_non_success_login_response_is_unknown_and_does_not_probe_gateway(self):
        response = {"retCode": 1000, "data": {"addresses": [{"host": "gw1.haoplay.com.cn", "port": 8801}]}}
        with (
            patch("bot_app.status_probe.build_opener") as opener_factory,
            patch("bot_app.status_probe.socket.create_connection") as connect,
        ):
            opener_factory.return_value.open.return_value = self._http_response(response)
            sample = await build_login_preflight_probe(self.credentials_path)()

        connect.assert_not_called()
        self.assertFalse(sample.accepted)
        self.assertEqual(sample.ret_code, 1000)
        self.assertEqual(classify_sample(sample), Status.UNKNOWN)

    def test_missing_credentials_fail_before_monitor_startup(self):
        with self.assertRaisesRegex(ValueError, "credentials file is unavailable"):
            build_login_preflight_probe(Path(self.temp_dir.name) / "missing.toml")

    def test_json_credentials_file_is_supported(self):
        credentials_path = Path(self.temp_dir.name) / "probe.json"
        credentials_path.write_text(
            json.dumps({"open_id": "account-id", "token": "opaque-token", "channel": "channel-a"}),
            encoding="utf-8",
        )
        if os.name == "posix":
            credentials_path.chmod(0o600)
        self.assertEqual(
            _load_credentials(credentials_path),
            {"open_id": "account-id", "token": "opaque-token", "channel": "channel-a"},
        )

    def test_login_probe_rejects_redirects_to_prevent_credential_forwarding(self):
        handler = _RejectRedirects()
        request = MagicMock()
        self.assertIsNone(
            handler.redirect_request(
                request,
                MagicMock(),
                307,
                "Temporary Redirect",
                {},
                "https://attacker.example/login",
            )
        )

    async def test_legacy_probe_reloads_replaced_credentials_without_restart(self):
        probe = build_login_preflight_probe(self.credentials_path)
        self.credentials_path.write_text(
            'open_id = "account-id"\ntoken = "replacement-token"\nchannel = "channel-a"\n',
            encoding="utf-8",
        )
        with patch("bot_app.status_probe._sample") as sample:
            await probe()
        self.assertEqual(sample.call_args.args[0]["token"], "replacement-token")


if __name__ == "__main__":
    unittest.main()
