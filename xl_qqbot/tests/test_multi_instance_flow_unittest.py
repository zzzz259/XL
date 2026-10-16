import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from bot_app.deploy_control import DeploymentControl
from bot_app.gateway import GatewayTransportClient, RouterClient, build_gateway_control_app
from bot_app.proactive_outbox import ProactiveOutbox


TOKEN = "multi-instance-transport-token-0123456789"


class MultiInstanceFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_group_routes_to_its_only_instance_and_outbound_uses_one_gateway(self):
        received = []

        class Forwarder:
            async def forward(self, instance_id, payload):
                received.append((instance_id, payload))
                return True

        router = object.__new__(RouterClient)
        router._group_instances = {"group-a": "copy-a", "group-b": "copy-b"}
        router._forwarder = Forwarder()
        router._group_store = None
        router._deployment_control = None
        router._update_status_path = None
        router._tiers = None

        await router._route("unmodified_event", {"group_openid": "group-b", "opaque": [1, 2]})

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "copy-b")
        self.assertEqual(received[0][1]["data"], {"group_openid": "group-b", "opaque": [1, 2]})

        sent = []

        class Sender:
            async def start(self):
                return None

            async def close(self):
                return None

            async def send_text(self, group, text, reply_to=""):
                sent.append((group, text, reply_to))
                return True

        with tempfile.TemporaryDirectory() as directory:
            control = DeploymentControl(
                Path(directory) / "maintenance.json", instance_ids=("copy-a", "copy-b")
            )
            app = build_gateway_control_app(control, bearer_token=TOKEN, sender=Sender())
            async with TestClient(TestServer(app)) as server:
                clients = [
                    GatewayTransportClient(
                        str(server.make_url("/")), bearer_token=TOKEN, instance_id=instance_id
                    )
                    for instance_id in ("copy-a", "copy-b")
                ]
                for client in clients:
                    await client.start()
                try:
                    self.assertTrue(await clients[0].send_text("group-a", "raw A"))
                    self.assertTrue(await clients[1].send_text("group-b", "raw B"))
                finally:
                    for client in clients:
                        await client.close()

        self.assertEqual(sent, [("group-a", "raw A", ""), ("group-b", "raw B", "")])

    def test_same_event_key_is_isolated_by_instance_outbox_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = ProactiveOutbox(root / "copy-a")
            second = ProactiveOutbox(root / "copy-b")

            first.enqueue_text("event-1", "group-a", 0, "A")
            second.enqueue_text("event-1", "group-b", 0, "B")

            self.assertEqual(first.count(), 1)
            self.assertEqual(second.count(), 1)
            self.assertEqual(first.next_due_heads(now=0)[0].text, "A")
            self.assertEqual(second.next_due_heads(now=0)[0].text, "B")


if __name__ == "__main__":
    unittest.main()
