import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from bot_app.config import GroupsConfig
from bot_app.deploy_control import DeploymentControl, build_deployment_app
from bot_app.proactive_outbox import ProactiveOutbox
from bot_app.tiers import GroupTier

GROUP = "debug-group"
TOKEN = "long-test-token"


class DeploymentOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.outbox = ProactiveOutbox(self.root / "persistent-data")
        self.app = self.make_app(self.outbox)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp_dir.cleanup()

    def make_app(self, outbox):
        return build_deployment_app(
            control=DeploymentControl(self.root / "maintenance.json"),
            bearer_token=TOKEN,
            proactive_outbox=outbox,
            tiers=GroupTier(GroupsConfig(debug=[GROUP])),
            target_groups=[GROUP],
        )

    async def post_announcement(self, payload):
        return await self.client.post(
            "/deployment/announce",
            json=payload,
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    async def test_announcement_ack_means_durable_queue_and_retries_are_idempotent(
        self,
    ):
        payload = {
            "tier": "debug",
            "phase": "starting",
            "notification_id": "tx-1:starting:debug",
        }
        first = await self.post_announcement(payload)
        second = await self.post_announcement(payload)

        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        self.assertEqual(await second.json(), {"ok": True, "results": {GROUP: True}})
        self.assertEqual(self.outbox.count(), 1)
        row = self.outbox.list_pending()[0]
        self.assertEqual(row.event_key, "deployment:tx-1:starting:debug")
        self.assertEqual(row.text, "检测到更新，正在更新bot，期间将暂停服务")

    async def test_missing_stable_notification_id_is_rejected_without_enqueue(self):
        response = await self.post_announcement({"tier": "debug", "phase": "starting"})

        self.assertEqual(response.status, 400)
        self.assertEqual(self.outbox.count(), 0)

    async def test_persistence_failure_is_not_acknowledged(self):
        class FailingOutbox:
            def enqueue_text(self, *_args):
                raise OSError("simulated database write failure")

        client = TestClient(TestServer(self.make_app(FailingOutbox())))
        await client.start_server()
        try:
            response = await client.post(
                "/deployment/announce",
                json={
                    "tier": "debug",
                    "phase": "starting",
                    "notification_id": "tx-2:starting:debug",
                },
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            self.assertEqual(response.status, 503)
            self.assertFalse((await response.json())["ok"])
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
