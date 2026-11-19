import asyncio
import os
from datetime import datetime

import pytest
from bot_app.config import load_config
from bot_app.guild_ipc import GuildClient
from bot_app.guild_jobs import GuildJobs, guild_slots
from bot_app.guild_lookup_transport import GuildLookupClient
from bot_app.guild_roster import (
    GuildAccountBusy,
    GuildEntry,
    GuildLookup,
    GuildRoster,
    GuildRosterStore,
)


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
    service = GuildJobs(roster, ("09:15", "21:15"), served_lookup=lookup, now=lambda: now)
    stop = asyncio.Event()
    task = asyncio.create_task(service.run(stop))
    try:
        for _ in range(100):
            if (tmp_path / "jobs.sock").exists():
                break
            await asyncio.sleep(0.01)
        client = GuildClient(tmp_path)
        private_lookup = GuildLookupClient("http://guild-roster", socket_path=tmp_path / "jobs.sock")
        assert await private_lookup((row.name,)) == {row.name: GuildLookup((row,))}
        assert roster_store.load().rows == ()
        assert (await client.request("add", "全世界最好的K")).status == "added"
        assert roster_store.load().rows == (row,)
        assert roster_store.read_poster() == b"guild-roster-png"
        assert (tmp_path / "jobs.sock").stat().st_mode & 0o777 == 0o600
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
