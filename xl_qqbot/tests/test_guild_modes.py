from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from bot_app import service
from bot_app.automation import build_automation_runtime
from bot_app.config import load_config
from bot_app.guild_roster import GuildEntry, GuildLookup

_TOKEN = "internal-token-for-guild-lookup-tests"
_LOOKUP_PATH = "/internal/guild-lookup"


def read_config(root: Path, guild_settings: str, *, leaderboard: bool = False):
    path = root / "config.toml"
    config = (
        '[watch]\noutbox_dir = "notified"\n'
        f'data_dir = "{root.as_posix()}/bot-state"\n'
        f'[notification]\napi_token = "{_TOKEN}"\n'
        f"[guild_roster]\n{guild_settings}\n"
    )
    if leaderboard:
        config += '[leaderboard]\ncredentials_file = "game-state.json"\n'
    path.write_text(config, encoding="utf-8")
    return load_config(str(path))


def test_main_can_assemble_a_consumer_with_only_a_private_origin(tmp_path):
    config = read_config(tmp_path, 'enabled = true\nlookup_origin = "http://127.0.0.1:18782"')
    assert config.guild_roster.lookup_origin == "http://127.0.0.1:18782"
    assert not config.guild_roster.serve_lookup
    assert build_automation_runtime(config, notification_client=object()).guild_jobs is not None


@pytest.mark.parametrize("origin", ("http://example.com:18782", "http://127.0.0.1:18782/path"))
def test_public_or_path_bearing_lookup_origins_are_rejected(tmp_path, origin):
    with pytest.raises(ValueError, match="lookup_origin"):
        read_config(tmp_path, f'enabled = true\nlookup_origin = "{origin}"')


def test_a_consumer_cannot_also_serve_the_same_route(tmp_path):
    with pytest.raises(ValueError, match="serve_lookup"):
        read_config(
            tmp_path,
            'enabled = true\nserve_lookup = true\nlookup_origin = "http://127.0.0.1:18782"',
            leaderboard=True,
        )


@pytest.mark.asyncio
async def test_only_serving_worker_mounts_the_authenticated_lookup(tmp_path, monkeypatch):
    from bot_app.guild_lookup_transport import GuildLookupClient

    requested = []
    match = GuildLookup((GuildEntry("1", "休闲屋", 29, 30, 4, 200),))

    class Sender:
        async def start(self):
            pass

        async def close(self):
            pass

    def unix_client(origin, *, socket_path):
        assert origin == "http://guild-roster"
        assert socket_path == tmp_path / "bot-state/guild-roster/jobs.sock"

        async def lookup(names):
            requested.extend(names)
            return {"休闲屋": match}

        return lookup

    monkeypatch.setattr(service, "QQSender", lambda _: Sender())
    monkeypatch.setattr(service, "GuildLookupClient", unix_client)
    producer = read_config(tmp_path, "enabled = true\nserve_lookup = true", leaderboard=True)
    async with TestClient(TestServer(service.build_app(producer))) as app_client:
        invalid = await app_client.post(_LOOKUP_PATH, json={"names": ["休闲屋"]})
        assert invalid.status == 401
        assert requested == []
        peer = GuildLookupClient(str(app_client.make_url("/")).rstrip("/"), control_token=_TOKEN)
        assert await peer(("休闲屋",)) == {"休闲屋": match}
        assert requested == ["休闲屋"]

    consumer = read_config(tmp_path, 'enabled = true\nlookup_origin = "http://127.0.0.1:18782"')
    async with TestClient(TestServer(service.build_app(consumer))) as app_client:
        absent = await app_client.post(_LOOKUP_PATH)
        assert absent.status == 404
