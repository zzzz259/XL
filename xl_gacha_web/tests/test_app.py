import base64
import json
import unittest
from unittest.mock import patch

from app import QueryService, RequestError, allowed_origin


def fake_token(**extra):
    payload = {
        "uid": 123,
        "appid": "2070021",
        "provider": "sms",
        "exp": 4102444800,
        "guid": "11111111-2222-4333-8444-555555555555",
    }
    payload.update(extra)
    return (
        "header."
        + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        + ".signature"
    )


class TapTapTests(unittest.TestCase):
    def test_taptap_jwt_is_supported_as_a_starfall_credential(self):
        from gacha_core.gacha import parse_credentials

        c = parse_credentials(fake_token(appid="2070023", provider="taptap"))
        self.assertEqual(c.channel, "taptap")

    def test_taptap_hp_wrapper_accepts_object_and_json_string(self):
        from gacha_core.gacha import parse_credentials

        hp = {"jwt": fake_token(appid="2070023", provider="taptap")}
        for value in [hp, json.dumps(hp)]:
            c = parse_credentials(json.dumps({"HP_2070023": value}))
            self.assertEqual(c.channel, "taptap")

    def test_wrapper_and_token_application_must_match(self):
        from gacha_core.gacha import GachaError, parse_credentials

        with self.assertRaises(GachaError):
            parse_credentials(
                json.dumps(
                    {
                        "HP_2070021": {
                            "jwt": fake_token(appid="2070023", provider="taptap")
                        }
                    }
                )
            )

    def test_unrelated_app_and_ambiguous_wrappers_are_still_rejected(self):
        from gacha_core.gacha import GachaError, parse_credentials

        for raw in [
            fake_token(appid="9999999"),
            json.dumps(
                {
                    "HP_2070021": {"jwt": fake_token()},
                    "HP_2070023": {
                        "jwt": fake_token(appid="2070023", provider="taptap")
                    },
                }
            ),
        ]:
            with self.subTest(), self.assertRaises(GachaError):
                parse_credentials(raw)


class AppTests(unittest.TestCase):
    def test_malformed_input_is_rejected_before_any_network(self):
        service = QueryService()
        with patch("app.fetch_history") as fetch:
            for payload in [
                {},
                {"credential": 123},
                {"credential": "x" * 66000},
                {"credential": "not-a-token"},
            ]:
                with self.subTest(), self.assertRaises(RequestError):
                    service.query(payload)
            fetch.assert_not_called()

    def test_query_returns_only_player_data_and_does_not_retain_credentials(self):
        service = QueryService()
        with patch(
            "app.fetch_history",
            return_value=([], [{"selector": 1, "ret_code": 0, "record_count": 0}]),
        ):
            report = service.query({"credential": fake_token()})
        self.assertEqual(report["summary"]["pull_count"], 0)
        self.assertEqual(report["records"], [])
        self.assertNotIn("signature", json.dumps(report))
        self.assertNotIn("signature", repr(vars(service)))

    def test_upstream_exception_never_echoes_its_secret_message(self):
        service = QueryService()
        with (
            patch("app.fetch_history", side_effect=RuntimeError("PRIVATE_SECRET")),
            self.assertRaises(RequestError) as caught,
        ):
            service.query({"credential": fake_token()})
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(caught.exception.code, "QUERY_FAILED")

    def test_all_rejected_is_an_error_and_partial_is_explicit(self):
        service = QueryService()
        with (
            patch(
                "app.fetch_history",
                return_value=(
                    [],
                    [{"selector": 1, "ret_code": 1113, "record_count": 0}],
                ),
            ),
            self.assertRaises(RequestError),
        ):
            service.query({"credential": fake_token()})
        with patch(
            "app.fetch_history",
            return_value=(
                [],
                [
                    {"selector": 1, "ret_code": 0, "record_count": 0},
                    {"selector": 2, "ret_code": 1113, "record_count": 0},
                ],
            ),
        ):
            self.assertTrue(service.query({"credential": fake_token()})["partial"])

    def test_busy_service_rejects_new_work(self):
        service = QueryService()
        service.slots.acquire()
        service.slots.acquire()
        try:
            with self.assertRaises(RequestError) as caught:
                service.query({"credential": fake_token()})
            self.assertEqual(caught.exception.status, 429)
        finally:
            service.slots.release()
            service.slots.release()

    def test_origins_match_exactly_and_reject_cross_site_and_null(self):
        self.assertTrue(
            allowed_origin("http://127.0.0.1:8788", {"http://127.0.0.1:8788"})
        )
        for value in [
            None,
            "null",
            "http://127.0.0.1:8788.evil.test",
            "https://evil.test",
        ]:
            self.assertFalse(allowed_origin(value, {"http://127.0.0.1:8788"}))


if __name__ == "__main__":
    unittest.main()
