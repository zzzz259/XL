import asyncio

import pytest
from bot_app.config import LeaderboardConfig
from bot_app.guild_local_collection import GuildCollector
from bot_app.guild_roster import GuildEntry
from bot_app.leaderboard_job import LeaderboardArchiveJob
from gacha_core.gacha import blob, integer


@pytest.mark.asyncio
async def test_batch_uses_one_public_session_and_waits_for_account_gate():
    calls = []
    credentials = object()
    gate = asyncio.Lock()

    class Provider:
        async def credentials(self):
            assert gate.locked()
            calls.append("credentials")
            return credentials

    class Session:
        def __init__(self, supplied, **budget):
            assert supplied is credentials
            assert budget["max_requests"] == 2

        def __enter__(self):
            calls.append("open")
            return self

        def __exit__(self, *args):
            calls.append("close")

        def request(self, command, data):
            calls.append(command)
            return blob(1, integer(1, 123) + blob(2, b"test")
                        + integer(5, 10) + integer(11, 29) + integer(26, 4))

    collector = GuildCollector(Provider(), gate, 2, session_factory=Session, now=lambda: 1234)
    async with gate:
        task = asyncio.create_task(collector(("休闲屋", "赴故")))
        await asyncio.sleep(0)
        assert calls == []
    result = await task
    assert calls == ["credentials", "open", 10032, 10032, "close"]
    assert set(result) == {"休闲屋", "赴故"}
    assert result["休闲屋"].guilds == (GuildEntry("123", "test", 29, 30, 4, 1234),)


@pytest.mark.asyncio
async def test_request_spacing_budgets_all_names_across_sessions():
    sessions = []

    class Provider:
        async def credentials(self):
            return object()

    class Session:
        def __init__(self, credentials, **budget):
            assert 25 + budget["max_requests"] * 60 <= budget["max_duration"] <= 180
            sessions.append(budget["max_requests"])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, command, data):
            return b""

    collector = GuildCollector(Provider(), asyncio.Lock(), 60, session_factory=Session)
    names = ("一", "二", "三", "四", "五")
    assert set(await collector(names)) == set(names)
    assert sessions == [2, 2, 1]


@pytest.mark.asyncio
async def test_overlapping_calls_share_one_session_for_the_same_names():
    calls = []
    started = asyncio.Event()
    release = asyncio.Event()

    class Provider:
        async def credentials(self):
            calls.append("credentials")
            started.set()
            await release.wait()
            return object()

    class Session:
        def __init__(self, credentials, **budget):
            pass

        def __enter__(self):
            calls.append("open")
            return self

        def __exit__(self, *args):
            calls.append("close")

        def request(self, command, data):
            calls.append((command, data))
            return b""

    collector = GuildCollector(Provider(), asyncio.Lock(), 2, session_factory=Session)
    names = ("休闲屋", "赴故")
    first = asyncio.create_task(collector(names))
    await started.wait()
    second = asyncio.create_task(collector(names[::-1]))
    await asyncio.sleep(0)
    release.set()

    first_result, second_result = await asyncio.gather(first, second)
    assert first_result == second_result
    assert set(first_result) == set(names)
    filters = b"".join(integer(field, 0) for field in (3, 4, 5, 7, 8))
    requests = [(10032, integer(1, 0) + blob(2, name.encode()) + filters) for name in names]
    expected_calls = ["credentials", "open", *requests, "close"]
    assert calls == expected_calls

    assert await collector(names) == first_result
    assert calls == expected_calls * 2


@pytest.mark.asyncio
async def test_leaderboard_probe_waits_for_same_account_gate(tmp_path, monkeypatch):
    entered = asyncio.Event()
    gate = asyncio.Lock()

    class Provider:
        async def credentials(self):
            assert gate.locked()
            entered.set()
            return object()

    job = LeaderboardArchiveJob(
        LeaderboardConfig(data_dir=str(tmp_path)), provider=Provider(),
        history=None, stage_map=None, session_factory=None, account_lock=gate,
    )
    monkeypatch.setattr(job, "_probe_all_metadata", lambda _: {})
    async with gate:
        task = asyncio.create_task(job._probe(100))
        await asyncio.sleep(0)
        assert not entered.is_set()
    await task
    assert entered.is_set()
