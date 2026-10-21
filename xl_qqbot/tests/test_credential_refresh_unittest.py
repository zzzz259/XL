import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot_app.config import ServerStatusConfig, load_config
from bot_app.renewing_probe import build_status_probe
from bot_app.status_monitor import Status, classify_sample
from starfall_refresh.core import Store
from starfall_refresh.http import HTTP


def jwt(iat=100, vfp=300, exp=10000, uid="test-owner"):
    raw = json.dumps(
        dict(iat=iat, vfp=vfp, exp=exp, uid=uid, appid="app", provider="sms")
    )
    return (
        "header."
        + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
        + ".fake"
    )


def bundle():
    return dict(
        version=1,
        profile=dict(
            appkey="synthetic",
            signing_input="synthetic",
            params=dict(appid="app", channel="sms"),
        ),
        credential=dict(open_id="test-owner", token=jwt(), channel="sms"),
    )


class RenewalProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "private" / "state.json"
        self.store = Store(self.path)
        self.store.initialize(bundle())
        self.config = ServerStatusConfig(
            enabled=True,
            credentials_file=str(self.path),
            refresh_enabled=True,
            refresh_proxy="direct",
        )
        self.calls = []
        self.candidate = jwt(400, 600)
        self.login = {"retCode": 0, "data": {"addresses": ["gw.haoplay.com.cn:8801"]}}

    def http(self, url, body, headers):
        self.calls.append(
            (url, json.loads(body) if url.endswith("/login") else headers)
        )
        return (
            {"code": 0, "jwt": self.candidate}
            if url.endswith("/api/auth")
            else self.login
        )

    def client(self):
        from unittest.mock import Mock

        client = Mock(side_effect=self.http)
        client.gateway.return_value = "REACHABLE"
        return client

    async def test_refresh_is_persisted_and_next_sample_uses_new_token_without_restart(
        self,
    ):
        client = self.client()
        with patch("bot_app.renewing_probe.HTTP", return_value=client):
            probe = build_status_probe(self.config, now=lambda: 500)
            first = await probe()
            second = await probe()
        self.assertEqual(
            classify_sample(first), Status.OPEN_CANDIDATE_GATEWAY_REACHABLE
        )
        self.assertEqual(
            classify_sample(second), Status.OPEN_CANDIDATE_GATEWAY_REACHABLE
        )
        self.assertEqual(self.store.load()["credential"]["token"], self.candidate)
        login_calls = [payload for url, payload in self.calls if url.endswith("/login")]
        self.assertTrue(
            all(payload["token"] == self.candidate for payload in login_calls)
        )
        self.assertEqual(
            len([url for url, _ in self.calls if url.endswith("/api/auth")]), 1
        )
        self.assertEqual(self.store.load()["pending"], [])

    async def test_invalid_maintenance_field_remains_unknown(self):
        self.login["data"]["maintainState"] = True
        with patch("bot_app.renewing_probe.HTTP", return_value=self.client()):
            sample = await build_status_probe(self.config, now=lambda: 250)()
        self.assertEqual(classify_sample(sample), Status.UNKNOWN)

    async def test_wrong_identity_candidate_preserves_old_credential(self):
        self.candidate = jwt(400, 600, uid="someone-else")
        with patch("bot_app.renewing_probe.HTTP", return_value=self.client()):
            await build_status_probe(self.config, now=lambda: 500)()
        self.assertEqual(self.store.load()["credential"]["token"], jwt())

    async def test_expired_state_never_attempts_network_and_is_unknown(self):
        client = self.client()
        with patch("bot_app.renewing_probe.HTTP", return_value=client):
            sample = await build_status_probe(self.config, now=lambda: 10001)()
        self.assertEqual(classify_sample(sample), Status.UNKNOWN)
        self.assertEqual(sample.error_class, "AUTH_ERROR")
        client.assert_not_called()

    async def test_lock_contention_is_unknown_and_never_calls_network(self):
        client = self.client()
        with patch("bot_app.renewing_probe.HTTP", return_value=client):
            probe = build_status_probe(self.config, now=lambda: 500)
            with self.store.locked():
                sample = await probe()
        self.assertEqual(classify_sample(sample), Status.UNKNOWN)
        client.assert_not_called()

    async def test_renewal_does_not_create_second_notification_state_machine(self):
        with patch("bot_app.renewing_probe.HTTP", return_value=self.client()):
            probe = build_status_probe(self.config, now=lambda: 250)
            await probe()
            self.login["data"]["maintainState"] = 1
            sample = await probe()
        self.assertEqual(classify_sample(sample), Status.MAINTENANCE_CONFIRMED)
        self.assertEqual(self.store.load()["pending"], [])
        self.assertIsNone(self.store.load()["last_state"])

    async def test_failed_refresh_cooldown_survives_provider_restart(self):
        self.candidate = jwt(400, 600, uid="wrong-owner")
        client = self.client()
        with patch("bot_app.renewing_probe.HTTP", return_value=client):
            await build_status_probe(self.config, now=lambda: 500)()
            await build_status_probe(self.config, now=lambda: 600)()
        self.assertEqual(len([u for u, _ in self.calls if u.endswith("/api/auth")]), 1)

    async def test_manual_auth_required_does_not_report_online(self):
        client = self.client()
        original = self.http
        client.side_effect = lambda u, b, h: (
            {"code": 401} if u.endswith("/api/auth") else original(u, b, h)
        )
        with patch("bot_app.renewing_probe.HTTP", return_value=client):
            result = await build_status_probe(self.config, now=lambda: 500)()
        self.assertEqual(classify_sample(result), Status.UNKNOWN)
        self.assertEqual(result.error_class, "AUTH_ERROR")

    def test_separate_process_cannot_enter_store_lock(self):
        code = "from starfall_refresh.core import Store,SafeError; import sys\ntry:\n with Store(sys.argv[1]).locked():print('ENTERED')\nexcept SafeError:print('BUSY')"
        with self.store.locked():
            result = subprocess.run(
                [sys.executable, "-B", "-c", code, str(self.path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "BUSY")

    def test_disabled_renewal_uses_legacy_provider(self):
        with patch("bot_app.renewing_probe.build_login_preflight_probe") as legacy:
            build_status_probe(ServerStatusConfig(credentials_file="legacy.toml"))
        legacy.assert_called_once_with("legacy.toml")

    def test_config_loads_refresh_options_and_rejects_non_boolean_or_remote_proxy(self):
        path = Path(self.tmp.name) / "config.toml"
        base = '[watch]\noutbox_dir="outbox"\n[server_status]\nenabled=true\ncredentials_file="state.json"\n'
        path.write_text(base + 'refresh_enabled=true\nrefresh_proxy="direct"\n')
        self.assertTrue(load_config(path).server_status.refresh_enabled)
        for extra in (
            'refresh_enabled="yes"\n',
            'refresh_enabled=true\nrefresh_proxy="http://remote.invalid:7897"\n',
        ):
            path.write_text(base + extra)
            with self.assertRaises(ValueError):
                load_config(path)


class ExplicitTransportTests(unittest.TestCase):
    def test_direct_mode_ignores_environment_proxy(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://untrusted.invalid:9000"}):
            client = HTTP("direct")
        self.assertIsNone(client.proxy)
        self.assertFalse(
            any(type(h).__name__ == "ProxyHandler" for h in client.opener.handlers)
        )

    def test_direct_gateway_uses_only_allowlisted_target(self):
        with patch("starfall_refresh.http.socket.create_connection") as connect:
            self.assertEqual(
                HTTP("direct").gateway([{"host": "gw.haoplay.com.cn", "port": 8801}]),
                "REACHABLE",
            )
            self.assertEqual(connect.call_args.args[0], ("gw.haoplay.com.cn", 8801))

    def test_configured_proxy_ignores_no_proxy(self):
        from urllib.request import Request

        client = HTTP("http://127.0.0.1:7897")
        handler = next(
            h for h in client.opener.handlers if type(h).__name__ == "ExplicitProxy"
        )
        request = Request("https://elpis-accverify.haoplay.com.cn/v1/account/login")
        with patch.dict(os.environ, {"NO_PROXY": "*"}):
            handler.https_open(request)
        self.assertTrue(request.has_proxy() or request._tunnel_host is not None)


if __name__ == "__main__":
    unittest.main()
