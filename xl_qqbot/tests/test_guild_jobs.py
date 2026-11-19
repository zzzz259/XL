import asyncio
import os
from datetime import datetime

import pytest
from bot_app.automation import build_automation_runtime
from bot_app.config import LeaderboardConfig, load_config
from bot_app.guild_ipc import GuildClient
from bot_app.guild_jobs import GuildCollector, GuildJobs, guild_slots
from bot_app.guild_roster import (
    GuildAccountBusy,
    GuildEntry,
    GuildLookup,
    GuildRoster,
    GuildRosterStore,
)
from bot_app.leaderboard_job import LeaderboardArchiveJob
from gacha_core.gacha import blob, integer


def stamp(date):
    return datetime.fromisoformat(date).timestamp()


def test_only_two_beijing_slots_per_day():
    times = ("09:15", "21:15")
    next_morning = stamp("2026-11-20T09:15:00+08:00")
    evening = stamp("2026-11-19T21:15:00+08:00")
    morning = stamp("2026-11-19T09:15:00+08:00")
    assert guild_slots(stamp("2026-11-19T20:15:00+08:00"), times) == (morning, evening)
    assert guild_slots(evening, times) == (evening, next_morning)
    assert guild_slots(stamp("2026-11-20T00:15:00+08:00"), times) == (evening, next_morning)


def test_opt_in_config_uses_existing_credentials_and_exactly_two_slots(tmp_path):
    path = tmp_path / "config.toml"
    watch = '[watch]\noutbox_dir = "./outbox"\n'
    path.write_text(watch, encoding="utf-8")
    assert load_config(str(path)).guild_roster.enabled is False
    path.write_text(watch + '[guild_roster]\nenabled = true\n', encoding="utf-8")
    with pytest.raises(ValueError, match="credentials_file"):
        load_config(str(path))
    path.write_text(
        watch + '[guild_roster]\nenabled = true\nrefresh_times = ["08:15", "20:15"]\n'
        '[leaderboard]\ncredentials_file = "game-state.json"\n',
        encoding="utf-8",
    )
    assert load_config(str(path)).guild_roster.refresh_times == ("08:15", "20:15")
    assert build_automation_runtime(
        load_config(str(path)), notification_client=object()
    ).guild_jobs is not None
    path.write_text(
        watch + '[guild_roster]\nrefresh_times = ["08:15", "08:15"]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="refresh_times"):
        load_config(str(path))


@pytest.mark.asyncio
async def test_job_does_not_repeat_a_finished_slot_after_restart(tmp_path):
    called = []
    row = GuildEntry("1", "休闲屋", 29, 30, 4, 200)

    async def lookup(names):
        called.extend(names)
        return {row.name: GuildLookup((row,))}

    times = ("09:15", "21:15")
    store = GuildRosterStore(tmp_path)
    roster = GuildRoster(store, lookup, lambda *_: b"poster")
    await roster.initialize((row,))
    at = stamp("2026-11-19T11:25:00+08:00")
    runner = GuildJobs(roster, times, now=lambda: at)
    await runner.refresh_if_due()
    assert called == [row.name]
    restarted = GuildJobs(GuildRoster(store, lookup, lambda *_: b"poster"), times, now=lambda: at)
    await restarted.refresh_if_due()
    assert called == [row.name]
    at = stamp("2026-11-19T21:16:00+08:00")
    await restarted.refresh_if_due()
    assert called == [row.name, row.name]


@pytest.mark.asyncio
async def test_busy_account_defers_the_same_slot_without_counting_a_round(tmp_path):
    row = GuildEntry("1", "休闲屋", 29, 30, 4, 200)
    store = GuildRosterStore(tmp_path)
    attempts = 0

    async def lookup(names):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise GuildAccountBusy
        return {row.name: GuildLookup((row,))}

    roster = GuildRoster(store, lookup, lambda *_: b"poster")
    await roster.initialize((row,))
    now = stamp("2026-11-19T11:25:00+08:00")
    jobs = GuildJobs(roster, ("09:15", "21:15"), now=lambda: now)

    assert await jobs.refresh_if_due() == now + 60
    assert store.last_scheduled_at() is None
    assert not store.load().stale_ids
    now += 60
    await jobs.refresh_if_due()
    assert store.last_scheduled_at() == stamp("2026-11-19T09:15:00+08:00")


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


@pytest.mark.skipif(os.name != "posix", reason="test Bot runs on a Unix socket")
@pytest.mark.asyncio
async def test_query_worker_can_use_jobs_socket_and_persist_the_result(tmp_path):
    row = GuildEntry("2", "全世界最好的K.", 25, 30, 6, 200)
    roster_store = GuildRosterStore(tmp_path)

    async def lookup(names):
        return {name: GuildLookup((row,)) for name in names}

    roster = GuildRoster(roster_store, lookup, lambda *_: b"guild-roster-png")
    await roster.initialize(())
    now = stamp("2026-11-19T11:25:00+08:00")
    service = GuildJobs(roster, ("09:15", "21:15"), now=lambda: now)
    stop = asyncio.Event()
    task = asyncio.create_task(service.run(stop))
    try:
        for _ in range(100):
            if (tmp_path / "jobs.sock").exists():
                break
            await asyncio.sleep(0.01)
        client = GuildClient(tmp_path)
        assert (await client.request("add", "全世界最好的K")).status == "added"
        assert roster_store.load().rows == (row,)
        assert roster_store.read_poster() == b"guild-roster-png"
        assert (tmp_path / "jobs.sock").stat().st_mode & 0o777 == 0o600
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
