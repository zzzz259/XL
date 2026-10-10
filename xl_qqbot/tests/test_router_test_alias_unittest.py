import unittest
from types import SimpleNamespace

from bot_app.config import GroupsConfig
from bot_app.router import TierForwarder, worker_ports
from bot_app.tiers import GroupTier


DEBUG_GROUP = "debug-group"
TEST_GROUP = "test-group"
PRODUCTION_GROUP = "production-group"


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeSession:
    closed = False

    def __init__(self):
        self.urls = []

    def post(self, url, json=None):
        self.urls.append((url, json))
        return FakeResponse()


class TestRouterTestAlias(unittest.IsolatedAsyncioTestCase):
    async def test_debug_and_test_groups_forward_to_test_worker(self):
        config = SimpleNamespace(
            router=SimpleNamespace(test_port=8782, production_port=8783)
        )
        forwarder = TierForwarder(worker_ports(config))
        session = FakeSession()
        forwarder._session = session
        tiers = GroupTier(GroupsConfig(debug=[DEBUG_GROUP], test=[TEST_GROUP]))

        for group, expected_tier in (
            (DEBUG_GROUP, "debug"),
            (TEST_GROUP, "test"),
            (PRODUCTION_GROUP, "production"),
        ):
            tier = tiers.tier_of(group)
            self.assertEqual(tier, expected_tier)
            await forwarder.forward(tier, {"data": {"group_openid": group}})

        self.assertEqual(
            [url for url, _ in session.urls],
            [
                "http://127.0.0.1:8782/event",
                "http://127.0.0.1:8782/event",
                "http://127.0.0.1:8783/event",
            ],
        )
        self.assertEqual(
            [payload["data"]["group_openid"] for _, payload in session.urls],
            [DEBUG_GROUP, TEST_GROUP, PRODUCTION_GROUP],
        )


if __name__ == "__main__":
    unittest.main()
