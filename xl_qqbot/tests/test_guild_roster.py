import asyncio

import pytest
from bot_app.guild_roster import (
    GuildEntry,
    GuildLookup,
    GuildRoster,
    GuildRosterStore,
    load_guild_seed,
)


def entry(uid, name, count=25, rank=6, captured_at=100):
    return GuildEntry(str(uid), name, count, 30, rank, captured_at)


class Lookup:
    def __init__(self, matches):
        self.matches = matches
        self.calls = []

    async def __call__(self, names):
        self.calls.append(names)
        return {name: self.matches[name] for name in names if name in self.matches}


def poster(rows, stale_ids):
    return "|".join(row.name for row in rows).encode("utf-8")


async def make_roster(tmp_path, rows, lookup):
    store = GuildRosterStore(tmp_path)
    roster = GuildRoster(store, lookup, poster)
    await roster.initialize(rows)
    return roster, store


def test_initial_names_use_unique_public_ids():
    seed = load_guild_seed()
    assert len(seed) == len({row.guild_uid for row in seed}) == 18
    assert {"休闲屋", "全世界最好的K.", "亞士世", "赴故"} <= {row.name for row in seed}
    assert all(row.member_count is None and row.captured_at is None for row in seed)


@pytest.mark.asyncio
async def test_add_omitted_dot_queries_one_name_and_persists_canonical_name(tmp_path):
    query = "全世界最好的K"
    dotted = entry(2, query + ".")
    lookup = Lookup({query: GuildLookup((entry(3, query + "同好会"), dotted))})
    roster, store = await make_roster(tmp_path, (entry(1, "休闲屋"),), lookup)

    added = await roster.add(query)

    assert added.status == "added"
    assert added.name == dotted.name
    assert lookup.calls == [(query,)]
    assert store.load().rows == (entry(1, "休闲屋"), dotted)
    assert dotted.name in store.read_poster().decode("utf-8")
    assert (await roster.add(dotted.name)).status == "exists"
    assert lookup.calls == [(query,)]


@pytest.mark.asyncio
async def test_exact_name_wins_over_dotted_candidate(tmp_path):
    exact = entry(2, "会名")
    lookup = Lookup({"会名": GuildLookup((entry(3, "会名."), exact))})
    roster, store = await make_roster(tmp_path, (), lookup)

    assert (await roster.add("会名")).status == "added"
    assert store.load().rows == (exact,)


@pytest.mark.asyncio
async def test_inexact_candidate_selection_and_uid_deduplication(tmp_path):
    candidates = (entry(1, "甲公会"), entry(2, "乙公会"))
    lookup = Lookup({"公会": GuildLookup(candidates)})
    roster, store = await make_roster(tmp_path, (), lookup)

    choice = await roster.add("公会")

    assert choice.status == "candidates"
    assert choice.options == candidates
    assert store.load().rows == ()
    assert (await roster.add_selected(choice.options[1])).status == "added"
    assert (await roster.add_selected(entry(2, "乙公会."))).status == "exists"
    assert store.load().rows == (candidates[1],)
    assert lookup.calls == [("公会",)]


@pytest.mark.asyncio
async def test_truncated_search_asks_before_using_punctuation_match(tmp_path):
    row = entry(1, "全世界最好的K.")
    lookup = Lookup({"全世界最好的K": GuildLookup((row,), has_more=True)})
    roster, _ = await make_roster(tmp_path, (), lookup)

    result = await roster.add("全世界最好的K")

    assert result.status == "candidates"
    assert result.options == (row,)


@pytest.mark.asyncio
async def test_delete_selects_from_roster_and_empty_state_stays_empty_after_restart(tmp_path):
    candidates = (entry(1, "恶魔の心跳"), entry(2, "恶魔の呢喃"))
    lookup = Lookup({})
    roster, store = await make_roster(tmp_path, candidates, lookup)

    choices = await roster.delete("恶魔")
    assert choices.status == "candidates"
    assert choices.options == candidates
    assert (await roster.delete_selected(choices.options[0].guild_uid)).status == "deleted"
    assert (await roster.delete(candidates[1].name)).status == "deleted"
    assert lookup.calls == []
    assert store.load().rows == ()

    restarted, reopened = await make_roster(tmp_path, candidates, lookup)
    assert reopened.load().rows == ()
    assert (await restarted.refresh()).status == "empty"
    assert lookup.calls == []


@pytest.mark.asyncio
async def test_initialize_recovers_an_interrupted_database_creation(tmp_path):
    row = entry(1, "休闲屋")
    store = GuildRosterStore(tmp_path)
    store.path.touch()
    roster = GuildRoster(store, Lookup({}), poster)

    await roster.initialize((row,))

    assert store.load().rows == (row,)
    assert store.read_poster() == row.name.encode()


@pytest.mark.asyncio
async def test_refresh_updates_only_matching_ids_and_marks_previous_data(tmp_path):
    kept = entry(1, "甲", count=24, rank=4)
    previous = entry(2, "乙", count=29, rank=8)
    refreshed = entry(1, "甲", count=30, rank=5, captured_at=200)
    lookup = Lookup({
        "甲": GuildLookup((entry(3, "甲"), refreshed)),
        "乙": GuildLookup((entry(8, "乙"),)),
    })
    roster, store = await make_roster(tmp_path, (kept, previous), lookup)

    result = await roster.refresh(scheduled_at=500)

    assert lookup.calls == [("甲", "乙")]
    assert result.status == "partial" and result.failed == 1
    assert store.load().rows == (refreshed, previous)
    assert store.load().stale_ids == frozenset({"2"})
    assert store.last_scheduled_at() == 500
    assert store.read_poster() == "甲|乙".encode()


@pytest.mark.asyncio
async def test_two_overlapping_refreshes_share_one_lookup(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []
    row = entry(1, "休闲屋")

    async def lookup(names):
        calls.append(names)
        entered.set()
        await release.wait()
        return {"休闲屋": GuildLookup((row,))}

    roster, store = await make_roster(tmp_path, (row,), lookup)
    first = asyncio.create_task(roster.refresh())
    await entered.wait()
    second = asyncio.create_task(roster.refresh(scheduled_at=500))
    await asyncio.sleep(0)
    release.set()

    assert (await first).status == (await second).status == "updated"
    assert calls == [("休闲屋",)]
    assert store.last_scheduled_at() == 500


@pytest.mark.asyncio
async def test_render_error_leaves_old_roster_and_picture_together(tmp_path):
    old = entry(1, "休闲屋")
    new = entry(2, "全世界最好的K.")
    lookup = Lookup({new.name: GuildLookup((new,))})
    store = GuildRosterStore(tmp_path)

    def failed_render(rows, stale_ids):
        if len(rows) > 1:
            raise OSError("rendering failed")
        return poster(rows, stale_ids)

    roster = GuildRoster(store, lookup, failed_render)
    await roster.initialize((old,))
    with pytest.raises(OSError):
        await roster.add(new.name)

    assert store.load().rows == (old,)
    assert store.read_poster() == old.name.encode("utf-8")
