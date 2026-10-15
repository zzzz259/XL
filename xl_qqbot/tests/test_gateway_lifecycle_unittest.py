import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from bot_app.config import (
    BotConfig,
    Config,
    GatewayConfig,
    GroupsConfig,
    MessageConfig,
    RouterConfig,
    TargetConfig,
    UploadConfig,
    WatchConfig,
)
from bot_app.deploy_control import DeploymentControl
from bot_app.gateway import (
    GatewayRuntime,
    RouterClient,
    TierForwarder,
    build_gateway_control_app,
    run_gateway_client,
)
from bot_app.groups import GroupStore
from bot_app.tiers import GroupTier


TOKEN = "gateway-lifecycle-token-012345678901234567"


def make_config(root, port=8785):
    outbox = root / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    return Config(
        bot=BotConfig(appid="1", secret="2"),
        watch=WatchConfig(outbox_dir=str(outbox), interval_seconds=30, data_dir=str(root / "data")),
        target=TargetConfig(group_openids=[], auto_learn_from_events=True),
        upload=UploadConfig(file_base_url=""),
        message=MessageConfig(template=""),
        groups=GroupsConfig(debug=["debug-group"], test=["test-group"]),
        router=RouterConfig(),
        gateway=GatewayConfig(control_port=port, control_token=TOKEN),
    )


class GatewayControlApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.control = DeploymentControl(Path(self.temp_dir.name) / "maintenance.json")
        self.app = build_gateway_control_app(self.control, bearer_token=TOKEN)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp_dir.cleanup()

    async def test_gateway_api_authenticates_and_controls_maintenance_and_drain(self):
        denied = await self.client.post(
            "/gateway/maintenance", json={"tier": "test", "enabled": True}
        )
        self.assertEqual(denied.status, 401)

        headers = {"Authorization": f"Bearer {TOKEN}"}
        enabled = await self.client.post(
            "/gateway/maintenance",
            json={"tier": "test", "enabled": True},
            headers=headers,
        )
        health = await self.client.get("/gateway/health", headers=headers)
        drained = await self.client.get("/gateway/drained?tier=test", headers=headers)

        self.assertEqual(enabled.status, 200)
        self.assertTrue((await enabled.json())["tiers"]["test"])
        self.assertEqual(health.status, 503)
        self.assertTrue((await drained.json())["drained"])


class GatewayRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_starts_one_gateway_client_and_no_business_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory))
            started = asyncio.Event()
            clients = []

            class FakeClient:
                async def start(self, **_kwargs):
                    started.set()
                    await asyncio.Event().wait()

            def client_factory():
                client = FakeClient()
                clients.append(client)
                return client

            runtime = GatewayRuntime(config, client_factory=client_factory)
            task = asyncio.create_task(runtime.run())
            await asyncio.wait_for(started.wait(), timeout=2)
            runtime.stop()
            await asyncio.wait_for(task, timeout=2)

            self.assertEqual(len(clients), 1)
            self.assertFalse(hasattr(runtime, "watchers"))
            self.assertFalse(hasattr(runtime, "bilibili_watcher"))
            self.assertFalse(hasattr(runtime, "status_monitor"))
            self.assertFalse(hasattr(runtime, "notification_dispatcher"))


class GatewayForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_is_learned_and_forwarded_with_tier_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            received = []

            class Forwarder:
                async def forward(self, tier, payload):
                    received.append((tier, payload))
                    return True

            groups = GroupStore(str(root / "data"))
            router = object.__new__(RouterClient)
            router._group_store = groups
            router._tiers = GroupTier(
                GroupsConfig(debug=["debug-group"], test=["test-group"])
            )
            router._forwarder = Forwarder()
            router._update_status_path = root / "update_status.json"

            await router._route(
                "new_business_event",
                {"group_openid": "test-group", "payload": {"raw": True}},
            )

            self.assertIn("test-group", groups.openids())
            self.assertEqual(received[0][0], "test")
            self.assertEqual(received[0][1]["event_type"], "new_business_event")
            self.assertEqual(received[0][1]["data"]["payload"], {"raw": True})
            self.assertFalse(received[0][1]["muted"])

    async def test_gateway_disconnect_clears_readiness_before_stop(self):
        control = DeploymentControl("unused-maintenance.json")
        control.ready = True
        stopped = asyncio.Event()

        class DisconnectedClient:
            async def start(self, **_kwargs):
                stopped.set()

        await run_gateway_client(
            DisconnectedClient,
            "appid",
            "secret",
            stopped,
            readiness_control=control,
        )

        self.assertFalse(control.ready)

    async def test_gateway_reconnect_backoff_grows_after_disconnects(self):
        stop_event = asyncio.Event()
        clients = []
        delays = []

        class DisconnectedClient:
            async def start(self, **_kwargs):
                if len(clients) == 3:
                    stop_event.set()

        def client_factory():
            client = DisconnectedClient()
            clients.append(client)
            return client

        async def expire_wait(_awaitable, timeout):
            delays.append(timeout)
            _awaitable.close()
            raise asyncio.TimeoutError

        with patch("bot_app.gateway.asyncio.wait_for", expire_wait):
            await run_gateway_client(
                client_factory, "appid", "secret", stop_event, client_name="test"
            )

        self.assertEqual(len(clients), 3)
        self.assertEqual(delays, [5, 10])

    async def test_forwarder_tracks_in_flight_requests_for_drain(self):
        control = DeploymentControl("unused-maintenance.json")
        entered = asyncio.Event()
        release = asyncio.Event()

        class Response:
            status = 200

        class RequestContext:
            async def __aenter__(self):
                entered.set()
                await release.wait()
                return Response()

            async def __aexit__(self, *_args):
                return False

        class Session:
            closed = False

            def post(self, *_args, **_kwargs):
                return RequestContext()

        forwarder = TierForwarder({"test": 8782}, deployment_control=control)
        forwarder._session = Session()
        task = asyncio.create_task(forwarder.forward("test", {"event_type": "x"}))
        await asyncio.wait_for(entered.wait(), timeout=1)
        self.assertEqual(control.active_forwards("test"), 1)
        self.assertFalse(await control.wait_drained("test", timeout=0))
        release.set()
        self.assertTrue(await task)
        self.assertEqual(control.active_forwards("test"), 0)
        self.assertTrue(await control.wait_drained("test", timeout=0))


if __name__ == "__main__":
    unittest.main()
