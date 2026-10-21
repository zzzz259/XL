import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path

from starfall_refresh.core import Engine, SafeError, Store, classify, form


def token(iat=100, uid="owner", appid="app", provider="sms", exp=10000):
    payload = dict(
        uid=uid, appid=appid, provider=provider, iat=iat, vfp=iat + 200, exp=exp
    )
    return (
        "header."
        + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        + ".synthetic"
    )


def bundle():
    return dict(
        version=1,
        profile=dict(
            appkey="TEST_KEY",
            signing_input="TEST_INPUT",
            params=dict(appid="app", channel="sms", bundleid="test.package"),
        ),
        credential=dict(open_id="owner", channel="sms", token=token()),
    )


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "private" / "state.json")
        self.store.initialize(bundle())
        self.calls = []
        self.response = {"code": 0, "jwt": token(400)}
        self.accept = {"retCode": 0, "data": {}}

    def http(self, url, body, headers):
        self.calls.append(url)
        return self.response if url.endswith("/api/auth") else self.accept

    def engine(self, now=500):
        return Engine(self.store, self.http, now=lambda: now)

    def test_not_due_does_not_request_sdk(self):
        r = self.engine(250).tick()
        self.assertEqual(r["refresh"], "NOT_DUE")
        self.assertFalse(any(x.endswith("/api/auth") for x in self.calls))

    def test_accepted_candidate_updates_and_next_cycle_reads_latest_token(self):
        self.assertEqual(self.engine().tick()["refresh"], "UPDATED")
        self.assertEqual(self.store.load()["credential"]["token"], token(400))
        self.response = {"code": 0, "jwt": token(2000)}
        self.assertEqual(self.engine(2100).tick()["refresh"], "UPDATED")
        self.assertEqual(self.store.load()["credential"]["token"], token(2000))

    def test_candidate_wrong_identity_does_not_replace(self):
        self.response = {"code": 0, "jwt": token(400, uid="other")}
        self.assertEqual(self.engine().tick()["refresh"], "CANDIDATE_REJECTED")
        self.assertEqual(self.store.load()["credential"]["token"], token())

    def test_candidate_wrong_app_or_provider_does_not_replace(self):
        for kwargs in ({"appid": "other"}, {"provider": "other"}):
            with self.subTest(kwargs=kwargs):
                self.response = {"code": 0, "jwt": token(400, **kwargs)}
                self.engine().tick()
                self.assertEqual(self.store.load()["credential"]["token"], token())

    def test_older_and_expired_candidate_are_rejected(self):
        for index, candidate in enumerate((token(99), token(400, exp=499))):
            self.response = {"code": 0, "jwt": candidate}
            self.assertEqual(
                self.engine(500 + index * 1000).tick()["refresh"], "CANDIDATE_REJECTED"
            )
            self.assertEqual(self.store.load()["credential"]["token"], token())

    def test_server_rejection_preserves_previous(self):
        self.accept = {"retCode": 1000}
        self.assertEqual(self.engine().tick()["refresh"], "CANDIDATE_NOT_ACCEPTED")
        self.assertEqual(self.store.load()["credential"]["token"], token())

    def test_sms_requirement_is_not_maintenance(self):
        self.response = {"code": 401}
        r = self.engine().tick()
        self.assertEqual(r["refresh"], "AUTH_ACTION_REQUIRED")
        self.assertNotEqual(r["state"], "MAINTENANCE")

    def test_cooldown_survives_new_engine(self):
        self.response = {"code": 500}
        self.engine().tick()
        self.calls = []
        self.assertEqual(self.engine(600).tick()["refresh"], "COOLDOWN")
        self.assertFalse(any(x.endswith("/api/auth") for x in self.calls))

    def test_failure_does_not_leak_exception_or_secret(self):
        def broken(*args):
            raise OSError("TEST_KEY " + token())

        r = Engine(self.store, broken, now=lambda: 500).tick()
        self.assertEqual(r["state"], "UNKNOWN")
        self.assertNotIn("TEST_KEY", json.dumps(r))
        self.assertNotIn(token(), json.dumps(r))

    def test_profile_identity_mismatch_is_rejected_at_import(self):
        b = bundle()
        b["profile"]["params"]["channel"] = "other"
        with self.assertRaises(SafeError):
            Store(Path(self.tmp.name) / "bad" / "state.json").initialize(b)

    def test_no_overwrite_initialization(self):
        with self.assertRaises(SafeError):
            self.store.initialize(bundle())

    def test_unchanged_candidate_is_not_success(self):
        self.response = {"code": 0, "jwt": token()}
        self.assertEqual(self.engine().tick()["refresh"], "CANDIDATE_REJECTED")

    def test_missing_and_boolean_maintenance_are_unmarked(self):
        for data in ({}, {"maintainState": True}, {"maintainState": "1"}):
            self.assertEqual(
                classify({"retCode": 0, "data": data})["state"], "LOGIN_GATE_PASSED"
            )
        self.assertEqual(
            classify({"retCode": 0, "data": {"maintainState": 1}})["state"],
            "MAINTENANCE",
        )

    def test_pending_transition_is_persistent_and_acknowledged(self):
        self.engine(250).tick()
        self.accept = {"retCode": 0, "data": {"maintainState": 1}}
        event = self.engine(250).tick()["event"]
        self.assertEqual(event["state"], "MAINTENANCE")
        self.assertEqual(self.engine(250).tick()["event"]["key"], event["key"])
        self.engine().ack(event["key"])
        self.assertIsNone(self.engine(250).tick()["event"])

    def test_atomic_write_failure_preserves_valid_state(self):
        from unittest.mock import patch

        old = self.store.load()
        with patch(
            "starfall_refresh.core.os.replace", side_effect=OSError("disk error")
        ):
            with self.assertRaises(OSError):
                self.store.save(dict(old, sequence=999))
        self.assertEqual(self.store.load(), old)

    def test_signature_filter_and_unicode_roundtrip(self):
        from urllib.parse import parse_qs

        parsed = parse_qs(
            form(
                {
                    "extra": "space & 测试",
                    "appkey": "omit",
                    "privatejwt": "omit",
                    "config": "omit",
                },
                "a",
                "b",
                1,
            ).decode()
        )
        self.assertEqual(parsed["extra"], ["space & 测试"])
        self.assertEqual(set(parsed), {"extra", "time", "sys", "tz", "sig"})

    def test_signature_matches_fixed_original_sdk_vector(self):
        # Obtained by executing the original SDK Common in a network-disabled VM.
        from urllib.parse import parse_qs

        body = form(
            {"appid": "synthetic-app", "extra": "space & 测试"},
            "synthetic-key",
            "synthetic-private",
            1700000000,
        ).decode()
        self.assertEqual(parse_qs(body)["sig"], ["6QeBZ4JfYYs3o7hVKoFhszsvk58="])


if __name__ == "__main__":
    unittest.main()
