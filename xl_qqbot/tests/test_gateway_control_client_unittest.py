import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot_app.gateway import GatewayControlClient


TOKEN = "gateway-control-token-012345678901234567"


class GatewayControlClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []

        async def health(_request):
            return web.json_response({"ok": True, "ready": True})

        async def maintenance(request):
            payload = await request.json()
            self.calls.append(("maintenance", payload))
            return web.json_response(
                {"ok": True, "tier": payload["tier"], "enabled": payload["enabled"], "tiers": {payload["tier"]: payload["enabled"]}}
            )

        async def drained(request):
            tier = request.query["tier"]
            self.calls.append(("drained", tier))
            return web.json_response({"ok": True, "tier": tier, "drained": True, "active": 0})

        app = web.Application()
        app.router.add_get("/gateway/health", health)
        app.router.add_post("/gateway/maintenance", maintenance)
        app.router.add_get("/gateway/drained", drained)
        self.server = TestServer(app)
        self.client = TestClient(self.server)
        await self.client.start_server()
        self.gateway = GatewayControlClient(
            str(self.client.make_url("/")), bearer_token=TOKEN
        )
        await self.gateway.start()

    async def asyncTearDown(self):
        await self.gateway.close()
        await self.client.close()

    async def test_health_maintenance_and_drain_use_gateway_api(self):
        self.assertTrue(await self.gateway.health())
        state = await self.gateway.set_maintenance("test", True)
        drain = await self.gateway.drained("test")

        self.assertTrue(state["tiers"]["test"])
        self.assertTrue(drain["drained"])
        self.assertEqual(
            self.calls,
            [
                ("maintenance", {"tier": "test", "enabled": True}),
                ("drained", "test"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
