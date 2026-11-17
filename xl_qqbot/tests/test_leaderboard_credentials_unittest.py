import asyncio
import base64
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot_app import leaderboard_credentials as credentials_mod
from bot_app.config import LeaderboardConfig
from bot_app.leaderboard_credentials import (
    CredentialError,
    LeaderboardCredentialProvider,
    claim_renewal_owner,
    release_renewal_owner,
    renewal_owner,
    same_credentials_path,
    shared_account_lock,
)


def jwt(iat=100, vfp=300, exp=99999999999, uid="archive-owner"):
    raw = json.dumps(
        {
            "iat": iat,
            "vfp": vfp,
            "exp": exp,
            "uid": uid,
            "appid": "2070035",
            "provider": "sms",
            "guid": "12345678-1234-1234-1234-123456789012",
        }
    )
    return (
        "header."
        + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
        + ".fake"
    )


def bundle(token=None):
    return {
        "version": 1,
        "profile": {
            "appkey": "synthetic",
            "signing_input": "synthetic",
            "params": {"appid": "2070035", "channel": "sms"},
        },
        "credential": {
            "open_id": "archive-owner",
            "token": token or jwt(),
            "channel": "sms",
        },
    }


def make_config(path, *, refresh_enabled=False, proxy="direct"):
    return LeaderboardConfig(
        enabled=True,
        credentials_file=str(path),
        refresh_enabled=refresh_enabled,
        refresh_proxy=proxy,
        scope="cn:test-server:account-context",
        data_dir="/tmp/unused",
    )


class RenewalOwnershipTests(unittest.TestCase):
    def test_one_owner_per_state_path_and_release(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = str(Path(tmp.name) / "state.json")
        self.assertTrue(claim_renewal_owner(path, "status-monitor"))
        self.assertFalse(claim_renewal_owner(path, "leaderboard-job"))
        self.assertEqual(renewal_owner(path), "status-monitor")
        release_renewal_owner(path, "leaderboard-job")
        self.assertEqual(renewal_owner(path), "status-monitor")
        release_renewal_owner(path, "status-monitor")
        self.assertIsNone(renewal_owner(path))
        self.assertTrue(claim_renewal_owner(path, "leaderboard-job"))

    def test_same_credentials_path_resolves_relative_and_absolute(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        absolute = str(base / "state.json")
        with patch.object(os, "getcwd", return_value=str(base)):
            self.assertTrue(same_credentials_path("state.json", absolute))
        self.assertFalse(same_credentials_path(absolute, str(base / "other.json")))

    def test_shared_account_lock_is_per_resolved_path(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        first = shared_account_lock(str(base / "a.json"))
        self.assertIs(first, shared_account_lock(str(base / "a.json")))
        self.assertIsNot(first, shared_account_lock(str(base / "b.json")))


class LegacyProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "credentials.json"

    def write_legacy(self, token=None, **fields):
        payload = {"open_id": "archive-owner", "token": token or jwt(), "channel": "sms"}
        payload.update(fields)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        if os.name == "posix":
            self.path.chmod(0o600)

    async def test_legacy_file_yields_gateway_credentials(self):
        self.write_legacy()
        provider = LeaderboardCredentialProvider(make_config(self.path))
        web = await provider.credentials()
        self.assertEqual(web.open_id, "archive-owner")
        self.assertEqual(web.channel, "sms")

    async def test_legacy_file_requires_private_permissions_on_posix(self):
        if os.name != "posix":
            self.skipTest("posix permission bits only")
        self.write_legacy()
        self.path.chmod(self.path.stat().st_mode | stat.S_IROTH)
        provider = LeaderboardCredentialProvider(make_config(self.path))
        with self.assertLogs(credentials_mod._logger, level="WARNING"), \
                self.assertRaises(CredentialError) as caught:
            await provider.credentials()
        self.assertEqual(caught.exception.code, "PRIVATE_PERMISSIONS_REQUIRED")

    async def test_expired_legacy_token_is_a_fixed_code(self):
        self.write_legacy(token=jwt(iat=1, vfp=2, exp=3))
        provider = LeaderboardCredentialProvider(make_config(self.path))
        with self.assertRaises(CredentialError) as caught:
            await provider.credentials()
        self.assertEqual(caught.exception.code, "CREDENTIAL_EXPIRED")

    async def test_mismatched_identity_fields_are_rejected(self):
        self.write_legacy(open_id="someone-else")
        provider = LeaderboardCredentialProvider(make_config(self.path))
        with self.assertRaises(CredentialError) as caught:
            await provider.credentials()
        self.assertEqual(caught.exception.code, "INVALID_CREDENTIAL_BINDING")


class RenewalProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "private" / "state.json"
        from starfall_refresh.core import Store

        Store(self.path).initialize(bundle())

    def config(self):
        return make_config(self.path, refresh_enabled=True)

    def test_startup_rejects_unsafe_state_with_fixed_code(self):
        if os.name != "posix":
            self.skipTest("posix permission bits only")
        self.path.chmod(0o644)
        with self.assertLogs(credentials_mod._logger, level="WARNING"), \
                self.assertRaises(CredentialError) as caught:
            LeaderboardCredentialProvider(
                self.config(), engine_factory=self._exploding_engine
            )
        self.assertEqual(caught.exception.code, "PRIVATE_PERMISSIONS_REQUIRED")

    def test_store_safe_error_without_code_attribute_is_mapped(self):
        from starfall_refresh.core import SafeError

        class BrokenStore:
            def locked(self):
                return self

            def __enter__(self):
                raise SafeError("PRIVATE_PERMISSIONS_REQUIRED")

            def __exit__(self, *exc_info):
                return False

        provider = LeaderboardCredentialProvider(self.config())
        provider._store = BrokenStore()
        with self.assertRaises(CredentialError) as caught:
            provider.startup_check()
        self.assertEqual(caught.exception.code, "PRIVATE_PERMISSIONS_REQUIRED")

    def _exploding_engine(self, *args, **kwargs):
        raise AssertionError("engine must not be constructed")

    async def test_owner_renews_under_shared_lock_and_serializes_ticks(self):
        active = 0
        peak = 0
        ticks = []

        class FakeEngine:
            def __init__(self, store, http, now, track_events):
                self.store = store

            def tick(self, gateway=False):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                import time

                time.sleep(0.01)
                ticks.append(1)
                active -= 1
                return {"refresh": "NOT_DUE"}

        provider = LeaderboardCredentialProvider(
            self.config(), engine_factory=FakeEngine
        )
        lock = shared_account_lock(str(self.path))
        provider.bind_account_lock(lock)
        first, second = await asyncio.gather(
            provider.credentials(), provider.credentials()
        )
        self.assertEqual(first.open_id, "archive-owner")
        self.assertEqual(second.open_id, "archive-owner")
        self.assertEqual(peak, 1, "renewal ticks must serialize on the account lock")
        self.assertEqual(len(ticks), 2)

    async def test_read_only_provider_never_constructs_an_engine(self):
        self.path.chmod(0o600)
        provider = LeaderboardCredentialProvider(
            self.config(),
            read_only=True,
            engine_factory=self._exploding_engine,
        )
        web = await provider.credentials()
        self.assertEqual(web.open_id, "archive-owner")

    async def test_read_only_expired_state_fails_with_fixed_code(self):
        from starfall_refresh.core import Store

        Store(self.path).save(bundle(token=jwt(iat=1, vfp=2, exp=3)))
        provider = LeaderboardCredentialProvider(
            self.config(),
            read_only=True,
            engine_factory=self._exploding_engine,
        )
        with self.assertRaises(CredentialError) as caught:
            await provider.credentials()
        self.assertEqual(caught.exception.code, "CREDENTIAL_EXPIRED")


if __name__ == "__main__":
    unittest.main()
