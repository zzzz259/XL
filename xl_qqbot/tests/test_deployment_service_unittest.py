import unittest

from aiohttp.test_utils import TestClient, TestServer

from bot_app.config import GroupsConfig
from bot_app.deployment_service import build_deployment_service_app
from bot_app.tiers import GroupTier


TOKEN = "deployment-test-token-01234567890123456789"
DEBUG_GROUP = "debug-group"
TEST_GROUP = "test-group"
PRODUCTION_GROUP = "production-group"


class FakeNotificationClient:
    def __init__(self, ready=True):
        self.ready = ready
        self.calls = []
        self.delivery_succeeds = True

    async def health(self):
        return self.ready

    async def enqueue(self, **request):
        self.calls.append(request)
        return [
            {"recipient": item["recipient"], "ordinal": item["ordinal"], "sequence": i + 1}
            for i, item in enumerate(request["messages"])
        ]

    async def wait_for_delivery(self, event_key, *, expected_total, timeout_seconds):
        self.delivery_request = (event_key, expected_total, timeout_seconds)
        return self.delivery_succeeds

class FakeGatewayClient:
    def __init__(self, ready=True):
        self.ready = ready
        self.calls = []

    async def health(self):
        return self.ready

    async def set_maintenance(self, tier, enabled):
        self.calls.append(("maintenance", tier, enabled))
        tiers = ("debug", "test", "production") if tier == "main" else (tier,)
        return {item: enabled for item in tiers}

    async def drained(self, tier):
        self.calls.append(("drained", tier))
        return {"drained": True, "active": 0}


class DeploymentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_notifications = FakeNotificationClient()
        self.main_notifications = FakeNotificationClient()
        self.gateway = FakeGatewayClient()
        app = build_deployment_service_app(
            bearer_token=TOKEN,
            tiers=GroupTier(GroupsConfig(debug=[DEBUG_GROUP], test=[TEST_GROUP])),
            target_groups=[DEBUG_GROUP, TEST_GROUP, PRODUCTION_GROUP],
            gateway_client=self.gateway,
            test_notification_client=self.test_notifications,
            main_notification_client=self.main_notifications,
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def post(self, path, payload, token=TOKEN):
        return await self.client.post(
            path,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def test_announce_preserves_contract_and_routes_each_tier_to_its_notifier(self):
        test_response = await self.post(
            "/deployment/announce",
            {
                "tier": "test",
                "phase": "starting",
                "notification_id": "tx-test-start",
            },
        )
        debug_response = await self.post(
            "/deployment/announce",
            {
                "tier": "debug",
                "phase": "complete",
                "notification_id": "tx-debug-complete",
            },
        )
        main_response = await self.post(
            "/deployment/announce",
            {
                "tier": "main",
                "phase": "release_note",
                "notification_id": "tx-main-release",
                "text": "版本发布说明",
            },
        )

        self.assertEqual(test_response.status, 200)
        self.assertEqual(
            await test_response.json(),
            {"ok": True, "results": {TEST_GROUP: True}},
        )
        self.assertEqual(
            await debug_response.json(),
            {"ok": True, "results": {DEBUG_GROUP: True}},
        )
        self.assertEqual(main_response.status, 200)
        self.assertEqual(
            await main_response.json(),
            {"ok": True, "results": {PRODUCTION_GROUP: True}},
        )
        self.assertEqual(len(self.test_notifications.calls), 2)
        self.assertEqual(self.test_notifications.calls[0]["tier"], "test")
        self.assertEqual(len(self.main_notifications.calls), 1)
        self.assertEqual(self.main_notifications.calls[0]["tier"], "main")
        self.assertEqual(
            self.test_notifications.delivery_request,
            ("deployment:tx-test-start", 1, 30),
        )
        self.assertFalse(hasattr(self.main_notifications, "delivery_request"))

    async def test_invalid_bearer_token_is_rejected(self):
        response = await self.post(
            "/deployment/maintenance",
            {"tier": "test", "enabled": True},
            token="wrong",
        )

        self.assertEqual(response.status, 401)
        self.assertEqual(self.gateway.calls, [])

    async def test_maintenance_and_drained_proxy_to_gateway(self):
        maintenance = await self.post(
            "/deployment/maintenance", {"tier": "main", "enabled": True}
        )
        drained = await self.client.get(
            "/deployment/drained?tier=main",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        self.assertEqual(maintenance.status, 200)
        self.assertTrue((await maintenance.json())["ok"])
        self.assertEqual(drained.status, 200)
        self.assertEqual((await drained.json())["active"], 0)
        self.assertEqual(
            self.gateway.calls,
            [("maintenance", "main", True), ("drained", "main")],
        )

    async def test_health_fails_closed_when_gateway_or_notifier_is_unavailable(self):
        self.gateway.ready = False
        response = await self.client.get(
            "/health", headers={"Authorization": f"Bearer {TOKEN}"}
        )

        self.assertEqual(response.status, 503)
        self.assertFalse((await response.json())["ready"])

        self.gateway.ready = True
        self.test_notifications.ready = False
        response = await self.client.get(
            "/health", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        self.assertEqual(response.status, 503)

    async def test_announcement_is_not_acknowledged_when_notifier_fails(self):
        class UnavailableNotificationClient(FakeNotificationClient):
            async def enqueue(self, **_request):
                raise RuntimeError("notifier offline")

        self.test_notifications = UnavailableNotificationClient()
        app = build_deployment_service_app(
            bearer_token=TOKEN,
            tiers=GroupTier(GroupsConfig(debug=[DEBUG_GROUP], test=[TEST_GROUP])),
            target_groups=[TEST_GROUP],
            gateway_client=self.gateway,
            test_notification_client=self.test_notifications,
            main_notification_client=self.main_notifications,
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post(
                "/deployment/announce",
                json={
                    "tier": "test",
                    "phase": "starting",
                    "notification_id": "tx-failed-start",
                },
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            self.assertEqual(response.status, 503)
            self.assertFalse((await response.json())["ok"])
        finally:
            await client.close()

    async def test_start_announcement_fails_when_delivery_is_not_confirmed(self):
        self.test_notifications.delivery_succeeds = False
        response = await self.post(
            "/deployment/announce",
            {
                "tier": "test",
                "phase": "starting",
                "notification_id": "tx-pending-start",
            },
        )

        self.assertEqual(response.status, 503)
        self.assertFalse((await response.json())["ok"])

    async def test_completion_announcement_does_not_wait_for_delivery(self):
        self.test_notifications.delivery_succeeds = False
        response = await self.post(
            "/deployment/announce",
            {
                "tier": "debug",
                "phase": "complete",
                "notification_id": "tx-complete",
            },
        )

        self.assertEqual(response.status, 200)
        self.assertFalse(hasattr(self.test_notifications, "delivery_request"))



if __name__ == "__main__":
    unittest.main()
