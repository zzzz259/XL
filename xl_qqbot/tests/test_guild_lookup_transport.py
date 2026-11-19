import asyncio
import hmac
import sys
from dataclasses import dataclass

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from bot_app.guild_lookup_transport import (
    GUILD_LOOKUP_PATH,
    GuildLookupClient,
    guild_lookup_handler,
)
from bot_app.guild_roster import GuildAccountBusy, GuildEntry, GuildLookup

TOKEN = "transport-test-control-token-0123456789012345"
AUTH = "Bearer " + hmac.new(
    TOKEN.encode(), b"xl/qqbot/guild-lookup/v1", "sha256"
).hexdigest()


def _app(handler):
    app = web.Application()
    app.router.add_post(GUILD_LOOKUP_PATH, handler)
    return app


def test_loopback_client_round_trips_public_guilds():
    async def run():
        @dataclass(frozen=True)
        class InternalGuildEntry(GuildEntry):
            credential: str = "must-not-be-in-response"

        public = GuildEntry("1234", "公会。", 18, 25, 3, 1720000000.5)

        async def lookup(names):
            assert names == ("公会", "空公会")
            return {
                "公会": GuildLookup((InternalGuildEntry(**vars(public)),), True),
                "空公会": GuildLookup(()),
                "unsolicited": GuildLookup((GuildEntry("987", "other"),)),
            }

        app = _app(guild_lookup_handler(lookup, control_token=TOKEN))
        async with TestClient(TestServer(app)) as server:
            response = await server.post(GUILD_LOOKUP_PATH, headers={"Authorization": AUTH},
                                         json={"names": ["公会", "空公会"]})
            assert response.status == 200
            assert await response.json() == {
                "results": {
                    "公会": {"guilds": [vars(public)], "has_more": True},
                    "空公会": {"guilds": [], "has_more": False},
                }
            }
            client = GuildLookupClient(str(server.make_url("")).rstrip("/"), control_token=TOKEN)
            assert await client(("公会", "空公会")) == {
                "公会": GuildLookup((public,), True), "空公会": GuildLookup(()),
            }

    asyncio.run(run())


def test_loopback_handler_requires_the_scoped_bearer_before_lookup():
    async def run():
        calls = []

        async def lookup(names):
            calls.append(names)
            return {}

        app = _app(guild_lookup_handler(lookup, control_token=TOKEN))
        stale = "Bearer " + hmac.new(
            b"old-control-token-0123456789012345678901234",
            b"xl/qqbot/guild-lookup/v1", "sha256",
        ).hexdigest()
        async with TestClient(TestServer(app)) as server:
            for headers in (
                {}, {"Authorization": "Bearer " + TOKEN}, {"Authorization": stale},
                {"Authorization": "Bearer malformed"},
                [("Authorization", AUTH), ("Authorization", AUTH)],
            ):
                response = await server.post(GUILD_LOOKUP_PATH, headers=headers,
                                             json={"names": ["alpha"]})
                assert response.status == 401
            assert calls == []

    asyncio.run(run())


def test_handler_rejects_malformed_or_oversize_lookups_before_collection():
    async def run():
        calls = []

        async def lookup(names):
            calls.append(names)
            return {}

        headers = {"Authorization": AUTH}
        async with TestClient(TestServer(_app(guild_lookup_handler(lookup, control_token=TOKEN)))) as server:
            for body in (
                ["alpha"], {"names": "alpha"}, {"names": []}, {"names": [None]},
                {"names": ["．．"]}, {"names": ["bad\nname"]}, {"names": ["\ud800"]},
                {"names": ["会" * 81]}, {"names": ["alpha"] * 49},
                {"names": ["alpha"], "action": "mutate"},
            ):
                response = await server.post(GUILD_LOOKUP_PATH, headers=headers, json=body)
                assert response.status == 400
            response = await server.post(GUILD_LOOKUP_PATH,
                                         headers={**headers, "Content-Type": "application/json"},
                                         data="{")
            assert response.status == 400
            response = await server.post(GUILD_LOOKUP_PATH, headers=headers,
                                         json={"names": ["a" * 50_000]})
            assert response.status == 413
            assert calls == []

    asyncio.run(run())


def test_loopback_client_splits_names_and_retains_results_when_a_batch_is_busy():
    async def run():
        calls = []

        async def lookup(names):
            calls.append(names)
            if len(names) == 1:
                raise GuildAccountBusy
            return {name: GuildLookup(()) for name in names}

        async with TestServer(_app(guild_lookup_handler(lookup, control_token=TOKEN))) as server:
            client = GuildLookupClient(str(server.make_url("")).rstrip("/"), control_token=TOKEN)
            names = tuple(f"guild-{i}" for i in range(49))
            assert await client(names) == {name: GuildLookup(()) for name in names[:48]}
            assert calls == [names[:48], names[48:]]

    asyncio.run(run())


def test_loopback_client_propagates_busy_without_results():
    async def run():
        async def lookup(names):
            raise GuildAccountBusy("internal session detail")

        async with TestClient(TestServer(_app(guild_lookup_handler(lookup, control_token=TOKEN)))) as server:
            response = await server.post(GUILD_LOOKUP_PATH, headers={"Authorization": AUTH},
                                         json={"names": ["alpha"]})
            assert response.status == 503
            assert await response.json() == {"code": "guild_account_busy"}
            client = GuildLookupClient(str(server.make_url("")).rstrip("/"), control_token=TOKEN)
            with pytest.raises(GuildAccountBusy):
                await client(("alpha",))

    asyncio.run(run())


def test_loopback_client_stops_on_redirect_without_following_it():
    async def run():
        redirected = asyncio.Event()

        async def redirect(request):
            raise web.HTTPTemporaryRedirect("/credential-capture")

        async def capture(request):
            redirected.set()
            return web.json_response({"results": {}})

        app = _app(redirect)
        app.router.add_post("/credential-capture", capture)
        async with TestServer(app) as server:
            client = GuildLookupClient(str(server.make_url("")).rstrip("/"), control_token=TOKEN)
            assert await client(("alpha",)) == {}
            assert not redirected.is_set()

    asyncio.run(run())


@pytest.mark.parametrize("origin", [
    "http://localhost:8788", "https://127.0.0.1:8788", "http://127.0.0.1:0",
    "http://127.0.0.1:65536", "http://127.0.0.1:8788@192.0.2.1",
    "http://127.0.0.1:8788/path", "http://127.0.0.1:8788?dest=x",
])
def test_http_client_rejects_origins_outside_the_configured_loopback_port(origin):
    with pytest.raises(ValueError):
        GuildLookupClient(origin, control_token=TOKEN)


def test_http_client_requires_a_control_token():
    with pytest.raises(ValueError):
        GuildLookupClient("http://127.0.0.1:8788")


@pytest.mark.skipif(sys.platform == "win32", reason="aiohttp Unix sockets are used on Linux")
def test_unix_client_uses_the_private_socket_without_http_authentication(tmp_path):
    async def run():
        async def lookup(names):
            return {name: GuildLookup(()) for name in names}

        runner = web.AppRunner(_app(guild_lookup_handler(lookup)))
        socket_path = tmp_path / "lookup.sock"
        await runner.setup()
        try:
            await web.UnixSite(runner, str(socket_path)).start()
            client = GuildLookupClient("http://guild-roster", socket_path=socket_path)
            assert await client(("alpha",)) == {"alpha": GuildLookup(())}
        finally:
            await runner.cleanup()

    asyncio.run(run())
