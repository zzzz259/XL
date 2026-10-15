import unittest
import asyncio
from unittest.mock import AsyncMock

from botpy.connection import ConnectionState

from bot_app.config import GroupsConfig
from bot_app.gateway import RawEventParserMap, create_gateway_event
from bot_app.query_handler import QueryHandler
from bot_app.router import _RouterClient, _install_raw_parsers
from bot_app.tiers import GroupTier


DEBUG_GROUP = "debug-group"
TEST_GROUP = "test-group"
PRODUCTION_GROUP = "production-group"


class TestGatewayEvent(unittest.TestCase):
    def setUp(self):
        self.tiers = GroupTier(GroupsConfig(debug=[DEBUG_GROUP], test=[TEST_GROUP]))

    def test_unknown_business_event_keeps_type_and_raw_data(self):
        data = {
            "group_openid": TEST_GROUP,
            "new_field": {"nested": [1, "原始内容"]},
        }

        event = create_gateway_event(
            "new_business_event",
            data,
            self.tiers,
            muted=True,
            received_at="2026-10-15T08:00:00+00:00",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "new_business_event")
        self.assertEqual(event.tier, "test")
        self.assertEqual(event.data, data)
        self.assertEqual(event.received_at, "2026-10-15T08:00:00+00:00")
        self.assertTrue(event.muted)
        payload = event.to_payload()
        self.assertEqual(payload["event_type"], "new_business_event")
        self.assertEqual(payload["type"], "new_business_event")
        self.assertEqual(payload["data"], data)

    def test_group_message_legacy_type_is_preserved(self):
        event = create_gateway_event(
            "group_at",
            {"group_openid": DEBUG_GROUP, "content": "@bot"},
            self.tiers,
            muted=False,
            received_at="2026-10-15T08:00:00+00:00",
        )

        self.assertEqual(event.to_payload()["type"], "group_at")
        self.assertEqual(event.to_payload()["tier"], "debug")

    def test_event_without_group_is_rejected(self):
        event = create_gateway_event(
            "new_business_event",
            {"content": "no route"},
            self.tiers,
            muted=False,
            received_at="2026-10-15T08:00:00+00:00",
        )

        self.assertIsNone(event)

    def test_non_object_event_data_is_rejected(self):
        event = create_gateway_event(
            "new_business_event",
            ["not", "an", "object"],
            self.tiers,
            muted=False,
            received_at="2026-10-15T08:00:00+00:00",
        )

        self.assertIsNone(event)

    def test_unknown_gateway_opcode_is_dispatched_with_raw_data(self):
        calls = []
        parser_map = RawEventParserMap(
            {}, lambda event_type, data: calls.append((event_type, data))
        )
        payload = {"t": "NEW_BUSINESS_EVENT", "d": {"group_openid": PRODUCTION_GROUP}}

        parser_map["new_business_event"](payload)

        self.assertEqual(
            calls,
            [("new_business_event", {"group_openid": PRODUCTION_GROUP})],
        )

    def test_known_parsers_are_not_replaced_by_unknown_fallback(self):
        calls = []
        known = lambda payload: calls.append(("known", payload["d"]))
        parser_map = RawEventParserMap(
            {"known_event": known},
            lambda event_type, data: calls.append((event_type, data)),
        )

        parser_map["known_event"]({"d": {"value": 1}})

        self.assertEqual(calls, [("known", {"value": 1})])

    def test_botpy_parser_install_dispatches_unknown_raw_event(self):
        calls = []
        _install_raw_parsers()
        connection_state = ConnectionState(lambda *args: calls.append(args), api=None)

        connection_state.parsers["new_business_event"](
            {"t": "NEW_BUSINESS_EVENT", "d": {"group_openid": TEST_GROUP}}
        )

        self.assertEqual(
            calls,
            [(
                "raw_gateway_event",
                "new_business_event",
                {"group_openid": TEST_GROUP},
            )],
        )

    def test_router_exposes_raw_gateway_event_callback(self):
        router = object.__new__(_RouterClient)
        router._route = AsyncMock()

        asyncio.run(
            router.on_raw_gateway_event(
                "new_business_event", {"group_openid": TEST_GROUP}
            )
        )

        router._route.assert_awaited_once_with(
            "new_business_event", {"group_openid": TEST_GROUP}
        )

    def test_query_handler_reads_canonical_event_type(self):
        handler = object.__new__(QueryHandler)
        handler.handle_event = AsyncMock()
        data = {"group_openid": TEST_GROUP, "custom": "raw"}

        asyncio.run(
            handler.handle(
                {
                    "event_type": "new_business_event",
                    "tier": "test",
                    "received_at": "2026-10-15T08:00:00+00:00",
                    "muted": True,
                    "data": data,
                }
            )
        )

        handler.handle_event.assert_awaited_once_with(
            "new_business_event", data, muted=True
        )

if __name__ == "__main__":
    unittest.main()
