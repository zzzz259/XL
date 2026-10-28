"""Scheduled leaderboard archive job: synthetic sessions and clock only."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from bot_app.config import LeaderboardConfig
from bot_app.leaderboard_job import LeaderboardArchiveJob, SlotPlanner
from gacha_core.gacha import GachaError
from gacha_core.gacha import blob as b
from gacha_core.gacha import integer as i
from gacha_core.guild_stages import GuildWarStageMap
from leaderboard_seasons import SeasonHistory

T0 = 1_800_000_000  # divisible by the 1800s half-hour grid
SEASON = "27"


# ---- wire fixtures ----------------------------------------------------------

def schedule_pb(season=27, phase=2, state=2, start=T0 - 300, end=T0 + 3600):
    return i(1, season) + i(2, phase) + i(3, state) + i(4, start) + i(5, end)


def guild_meta_pb(season=27, phase=2, state=2, start=T0 - 300, end=T0 + 3600):
    return b(1, schedule_pb(season, phase, state, start, end)) + i(2, 1)


def illusion_meta_pb(season=27, end=T0 + 3600):
    return i(1, season) + i(2, end) + i(3, end + 86400) + i(14, 0) + i(17, 1)


def guild_row_pb(uid, rank, score):
    return (i(1, uid) + i(2, 30) + i(3, 7) + b(4, "公会%d" .encode() % uid)
            + i(5, rank) + i(6, score) + i(7, 20) + i(8, 18) + i(9, 3))


def member_row_pb(uid, rank=1, score=10):
    return i(1, uid) + i(2, 60) + b(3, "成员%d" .encode() % uid) + i(5, rank) + i(6, score)


def guild_page_pb(rows, members=()):
    return b"".join(b(1, row) for row in rows) + b"".join(b(2, m) for m in members)


def boss_row_pb(uin, rank, score):
    return (i(1, uin) + i(2, 60) + b(4, "玩家%d" .encode() % uin)
            + i(5, 7001) + b(6, "幻影旅团".encode()) + i(7, rank) + i(8, score))


def boss_page_pb(rows, total, cursor=1):
    return b"".join(b(3, row) for row in rows) + i(4, cursor) + i(5, total)


def world_row_pb(uin, rank, damage):
    return (i(1, uin) + i(2, 60) + b(3, "挑战者%d" .encode() % uin)
            + i(5, rank) + i(6, damage) + b(7, "远征队".encode()))


def world_page_pb(rows, schedule, total):
    return i(1, 0) + i(2, total) + b(3, schedule) + b"".join(b(4, row) for row in rows)


def card_map_pb(card_id, skin_id):
    return i(1, card_id) + i(2, skin_id)


def lineup_round_pb(score, *cards):
    return i(1, score) + b"".join(b(2, card) for card in cards)


def lineup_response_pb(uin, *rounds):
    return i(1, uin) + b"".join(b(2, round_) for round_ in rounds)


def card_simple_pb(card_id, level=60, quality=5, grade=7, fashion=900, position=1):
    return (i(3, card_id) + i(4, level) + i(5, quality)
            + i(6, grade) + i(7, fashion) + i(8, position))


def skill_level_pb(skill_id, level):
    return i(1, skill_id) + i(2, level)


def formation_pb(stage_id, fighter_uin, name, damage, battle_uid,
                 battle_time=65, version=2, cards=(), skills=()):
    return (i(1, stage_id) + i(2, fighter_uin) + b(3, name.encode())
            + b"".join(b(4, card) for card in cards)
            + i(6, damage) + i(7, battle_uid)
            + b"".join(b(8, skill) for skill in skills)
            + i(9, battle_time) + i(10, version))


def recommendation_response_pb(*rows):
    return b"".join(b(1, row) for row in rows)


def first_varint(body):
    value = 0
    shift = 0
    for byte in body:
        value |= (byte & 127) << shift
        if not byte & 128:
            return value
        shift += 7
    raise ValueError("truncated")


# ---- world model ------------------------------------------------------------

class World:
    """Command-dispatched synthetic responder for every leaderboard RPC."""

    def __init__(self, *, season=27, phase=2, start=T0 - 300, end=T0 + 3600,
                 illusion_players=2, lineup_alt_season=None, reject=None):
        self.schedule = schedule_pb(season, phase, 2, start, end)
        self.guild_meta = guild_meta_pb(season, phase, 2, start, end)
        self.illusion_meta = illusion_meta_pb(season, end)
        self.lineup_alt_season = lineup_alt_season
        self._illusion_meta_calls = 0
        self.guild_rank_page = guild_page_pb(
            [guild_row_pb(9001, 1, 100), guild_row_pb(9002, 2, 90)],
            [member_row_pb(1001, rank=1), member_row_pb(1002, rank=2)])
        self.guild_ext_page = guild_page_pb(
            [guild_row_pb(9003, 3, 80)], [member_row_pb(1003, rank=3)])
        uins = [100000 + n for n in range(illusion_players)]
        self.boss_page = boss_page_pb(
            [boss_row_pb(uin, n + 1, 5000 - n) for n, uin in enumerate(uins)],
            len(uins))
        self.world_page = world_page_pb(
            [world_row_pb(5001, 1, 777), world_row_pb(5002, 2, 555)],
            self.schedule, 2)
        self.lineups = {
            uin: lineup_response_pb(
                uin,
                lineup_round_pb(321, card_map_pb(30, 5), card_map_pb(7, 0)),
                lineup_round_pb(123, card_map_pb(9, 1)),
            )
            for uin in uins
        }
        self.boss_pages = None
        self.reject = reject or {}

    def __call__(self, command, body=b""):
        rejected = self.reject.get(command)
        if isinstance(rejected, list):
            outcome = rejected.pop(0) if rejected else None
        else:
            outcome = rejected
        if outcome is not None:
            raise outcome
        if command == 2301:
            return self.guild_meta
        if command == 2307:
            return self.guild_rank_page
        if command == 10033:
            return self.guild_ext_page
        if command == 2001:
            self._illusion_meta_calls += 1
            if self.lineup_alt_season is not None and self._illusion_meta_calls >= 3:
                # the ranking collector consumed calls 1-2; the lineup probe sees a
                # season change and must abort before any 2003 lineup request
                return illusion_meta_pb(self.lineup_alt_season)
            return self.illusion_meta
        if command == 2002:
            if self.boss_pages is not None:
                cursor = first_varint(body[1:]) if len(body) > 1 else 1
                return self.boss_pages[cursor]
            return self.boss_page
        if command == 3001:
            return b(1, self.schedule)
        if command == 3004:
            return self.world_page
        if command == 2003:
            return self.lineups[first_varint(body[1:])] if len(body) > 1 else self.lineups
        if command == 2306:
            return self.recommendations[first_varint(body[1:])]
        raise AssertionError(f"unexpected command {command!r}")


def world_with_stages(world, stages=None, *, share_first=2):
    if stages is None:
        stages = [51502011 + index for index in range(12)]
    recommendations = {}
    for index, stage_id in enumerate(stages):
        version = 1 if index == 1 else 2
        recommendations[stage_id] = recommendation_response_pb(
            formation_pb(stage_id, 8001 + index, f"斗士{index}", 101 + index,
                         9000000000000000 + index, version=version,
                         cards=[card_simple_pb(101 + index)],
                         skills=[skill_level_pb(4001, 3)]))
    world.recommendations = recommendations
    return world


def settle_world_after_end(world):
    """After the window the server keeps serving SETTLED_CANDIDATE reads."""
    settled = schedule_pb(27, 2, 4, T0 - 300, T0 + 3600)
    world.schedule = settled
    world.guild_meta = guild_meta_pb(27, 2, 4, T0 - 300, T0 + 3600)
    world.world_page = world_page_pb(
        [world_row_pb(5001, 1, 777), world_row_pb(5002, 2, 555)], settled, 2)
    world.illusion_meta = illusion_meta_pb(27, T0 + 100_000)


def multi_page_illusion_world():
    """12 illusion players across three ranking pages (5 + 5 + 2)."""
    world = world_with_stages(World(illusion_players=12))
    world.boss_pages = {}
    for cursor, count in ((1, 5), (6, 5), (11, 2)):
        rows = [boss_row_pb(100000 + n, n + 1, 5000 - n)
                for n in range(cursor - 1, cursor - 1 + count)]
        world.boss_pages[cursor] = boss_page_pb(rows, 12, cursor=cursor)
    return world


class FakeSession:
    def __init__(self, world, observer):
        self.world = world
        self.observer = observer
        self.calls = []

    def request(self, command, body=b""):
        self.calls.append((command, body))
        value = self.world(command, body)
        if isinstance(value, Exception):
            raise value
        if self.observer is not None:
            self.observer(command, value)
        return value

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeSessionFactory:
    def __init__(self, world, on_session=None):
        self.world = world
        self.sessions = []
        self.on_session = on_session

    def __call__(self, credentials, *, max_requests, max_seconds, response_observer):
        session = FakeSession(self.world, response_observer)
        self.sessions.append((session, max_requests, max_seconds))
        if self.on_session is not None:
            self.on_session(len(self.sessions), session, max_requests)
        return session


class FakeProvider:
    async def credentials(self):
        return object()


class FakePackager:
    def __init__(self):
        self.sealed = []

    def seal_season(self, scope, mode, season_id):
        self.sealed.append((scope, mode, season_id))
        return {"package": f"season__{scope}__{mode}__{season_id}.tar.gz"}


class Clock:
    def __init__(self, t0=T0):
        self.t = float(t0)

    def now(self):
        return self.t

    async def sleep(self, seconds):
        self.t += float(seconds)


def fixture_stage_map():
    stages = []
    for index in range(12):
        stages.append({
            "stage_id": 51502011 + index,
            "chapter_id": 50020201,
            "round": 1 + index // 5,
            "boss_index": 1 + index % 5,
            "boss_id": 52020001 + index,
            "share_hp_count": 2 if index == 0 else None,
        })
    return GuildWarStageMap({
        "format": "guild-war-stages/1",
        "phases": {
            "2": {"config_id": 51400002, "stage_count": 12, "stages": stages},
        },
    })


def job_config(tmp, config_kwargs=None):
    return LeaderboardConfig(
        enabled=True,
        credentials_file="unused",
        scope="test:scope",
        data_dir=str(tmp),
        metadata_probe_interval=600,
        **(config_kwargs or {}))


def make_job(tmp, world, clock, *, on_session=None, stage_map=None,
             packager=None, random_int=None, config_kwargs=None):
    config = job_config(tmp, config_kwargs)
    factory = FakeSessionFactory(world, on_session)
    history = SeasonHistory(Path(tmp) / "seasonal.sqlite3")
    job = LeaderboardArchiveJob(
        config, provider=FakeProvider(), history=history,
        stage_map=stage_map if stage_map is not None else fixture_stage_map(),
        session_factory=factory, packager=packager, now=clock.now,
        random_int=random_int or (lambda a, bmo: 45), sleep=clock.sleep)
    return job, history, factory


def restart_job(tmp, world, clock, *, on_session=None, packager=None):
    """A second process instance over the same data dir and history file."""
    factory = FakeSessionFactory(world, on_session)
    history = SeasonHistory(Path(tmp) / "seasonal.sqlite3")
    return LeaderboardArchiveJob(
        job_config(tmp), provider=FakeProvider(), history=history,
        stage_map=fixture_stage_map(), session_factory=factory,
        packager=packager, now=clock.now,
        random_int=lambda a, b: 45, sleep=clock.sleep), history, factory


async def drive(job, stop_event, predicate=None, real_timeout=15.0):
    import time as real_time

    task = asyncio.ensure_future(job.run(stop_event))
    started = real_time.monotonic()
    try:
        while not task.done():
            if predicate is not None and predicate():
                stop_event.set()
            if real_time.monotonic() - started > real_timeout:
                stop_event.set()
                await task
                raise AssertionError("job did not settle in time")
            await asyncio.sleep(0.002)
        return task.result() if not task.cancelled() else None
    finally:
        if not task.done():
            task.cancel()


class PlannerTests(unittest.TestCase):
    def test_offsets_are_drawn_once_and_persisted_before_use(self):
        persisted = []
        planner = SlotPlanner(random_int=lambda a, b: 45)
        planned = planner.observe_window(
            season_id="27", phase=2, start=T0 - 300, end=T0 + 3600, now=T0,
            persist=persisted.append)
        routine = [slot for slot in planned if slot.kind == "routine"]
        self.assertEqual(len(routine), 3)
        self.assertEqual([slot.fire_at for slot in routine],
                         [T0 - 45, T0 + 1755, T0 + 3555])
        cumulative = []
        current = {}
        for mark in (T0, T0 + 1800, T0 + 3600):
            current = dict(current)
            current[f"ROUTINE@{mark}"] = 45
            cumulative.append(current)
        self.assertEqual(persisted, cumulative)
        again = SlotPlanner(random_int=lambda a, b: 99, offsets=planner.offsets())
        replanned = again.observe_window(
            season_id="27", phase=2, start=T0 - 300, end=T0 + 3600, now=T0,
            persist=persisted.append)
        self.assertEqual(
            [slot.fire_at for slot in replanned if slot.kind == "routine"],
            [T0 - 45, T0 + 1755, T0 + 3555], "stored offsets must be reused")

    def test_pre_end_priority_stale_routine_conflict_and_clear(self):
        planner = SlotPlanner(random_int=lambda a, b: 45)
        planner.observe_window(season_id="27", phase=2, start=T0 - 300,
                               end=T0 + 200, now=T0, persist=lambda offsets: None)
        due = planner.next_due(T0)
        self.assertEqual(due.trigger, "PRE_END_300S",
                         "expired pre-end wins over the routine slot")
        self.assertTrue(planner.is_stale(due, T0))
        planner.settle(due)
        routine = planner.next_due(T0 + 60)
        self.assertEqual(routine.kind, "routine")
        self.assertTrue(planner.conflicts_with_pre_end(routine, T0 + 60))
        planner.settle(planner.next_due(T0))
        planner.clear_window()
        self.assertFalse(planner.has_pending())
        self.assertIsNone(planner.next_due(T0 + 10_000))


class JobTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stop = asyncio.Event()

    async def test_closed_period_only_probes_metadata(self):
        world = World()
        world.guild_meta = guild_meta_pb(27, 2, 5)  # CLOSED
        clock = Clock()
        job, history, factory = make_job(self.root, world, clock)
        await drive(job, self.stop, predicate=lambda: (
            clock.t >= T0 + 3 * 600 and len(factory.sessions) >= 4))
        self.assertGreaterEqual(len(factory.sessions), 4)
        for session, max_requests, max_seconds in factory.sessions:
            self.assertEqual((max_requests, max_seconds), (8, 60))
            self.assertEqual([command for command, _ in session.calls], [2301])
        self.assertEqual(history.snapshots(), [])

    async def test_open_window_persists_offsets_before_any_pipeline_session(self):
        world = world_with_stages(World())
        clock = Clock()
        seen = {"persisted_before_pipeline": False}

        def on_session(count, session, max_requests):
            if count >= 2 and not seen["persisted_before_pipeline"]:
                state_path = self.root / "schedule_state.json"
                if state_path.exists():
                    raw = json.loads(state_path.read_text(encoding="utf-8"))
                    offsets = raw.get("offsets", {})
                    seen["persisted_before_pipeline"] = (
                        len([key for key in offsets if key.startswith("ROUTINE@")]) == 3)

        job, _history, factory = make_job(self.root, world, clock, on_session=on_session)
        await drive(job, self.stop, predicate=lambda: len(factory.sessions) >= 6)
        self.assertTrue(seen["persisted_before_pipeline"])

    async def test_routine_pipeline_adapts_rankings_lineups_and_guild_index(self):
        world = world_with_stages(World(illusion_players=52))
        clock = Clock()
        job, history, _factory = make_job(self.root, world, clock)

        def pipeline_done():
            snaps = history.snapshots(mode="guild_war", season_id=SEASON)
            return (len(snaps) == 2 and snaps[1]["battles_count"] == 12
                    and history.last_run_state("test:scope", "illusion", SEASON)["pages_count"] == 2)

        await drive(job, self.stop, predicate=pipeline_done)

        guild_snaps = history.snapshots(mode="guild_war", season_id=SEASON)
        self.assertEqual(len(guild_snaps), 2)  # ranking + guild index run
        ranking_snap = history.export_snapshot(guild_snaps[0]["snapshot_id"])
        self.assertEqual([entry["entity_id"] for entry in ranking_snap["entries"]],
                         ["9001", "9002", "9003"])
        self.assertEqual(ranking_snap["entries"][0]["score"], 100)
        self.assertEqual(ranking_snap["entries"][0]["guild_name"], "公会9001")
        self.assertEqual([m["member_uid"] for m in ranking_snap["membership"]],
                         ["9001", "9002", "9003", "1001", "1002", "1003"],
                         "entries and member_entries form the full rank/member list")
        self.assertEqual(ranking_snap["page_evidence"][0]["source_rpc"], 2307)
        with sqlite_blobs(history, ranking_snap["snapshot_id"]) as blobs:
            # meta + rank + ext; the identical recheck responses dedup by SHA256
            self.assertEqual(blobs, 3)

        illusion_snaps = history.snapshots(mode="illusion", season_id=SEASON)
        self.assertEqual(len(illusion_snaps), 2)  # ranking + lineup run
        illusion_rank = history.export_snapshot(illusion_snaps[0]["snapshot_id"])
        self.assertEqual(illusion_rank["coverage"], "COMPLETE_REPORTED_RANGE")
        self.assertEqual(len(illusion_rank["entries"]), 52)
        self.assertEqual(illusion_rank["entries"][51]["entity_id"], "100051")
        self.assertEqual(len(illusion_rank["membership"]), 52,
                         "main ranking entries feed the full membership snapshot")

        lineup_run = history.last_run_state("test:scope", "illusion", SEASON)
        self.assertEqual(lineup_run["status"], "COMPLETE")
        self.assertEqual(lineup_run["pages_count"], 2, "52 players batched 50 + 2")
        lineup_snap = history.export_snapshot(lineup_run["snapshot_id"])
        self.assertEqual(lineup_snap["coverage"], "COMPLETE_REPORTED_RANGE")
        self.assertEqual(len(lineup_snap["membership"]), 52,
                         "lineup snapshots carry the ranking membership for presence")
        self.assertEqual(len(lineup_snap["battles"]), 52)
        first = lineup_snap["battles"][0]
        self.assertEqual(first["battle_uid"], "100000")
        self.assertEqual(first["round"], 2)
        slots = first["lineup"]
        self.assertEqual([slot["slot"] for slot in slots], [1, 2])
        self.assertEqual(slots[0]["raw_data"], {
            "round": 1,
            "score": "321",
            "cards": [{"cardId": "7", "skinId": "0"},
                      {"cardId": "30", "skinId": "5"}],
        })
        self.assertIsNotNone(first["raw_sha256"])

        nightmare_snap = history.export_snapshot(
            history.snapshots(mode="nightmare", season_id=SEASON)[0]["snapshot_id"])
        self.assertEqual([entry["entity_id"] for entry in nightmare_snap["entries"]],
                         ["5001", "5002"])
        self.assertEqual(nightmare_snap["entries"][0]["score"], 777)

        index_run = history.last_run_state("test:scope", "guild_war", SEASON)
        self.assertEqual(index_run["pages_count"], 3, "12 stages batched 5 + 5 + 2")
        index_snap = history.export_snapshot(index_run["snapshot_id"])
        self.assertEqual(index_snap["coverage"], "SERVER_VISIBLE_SLICE",
                         "the configured-pool index is a server-visible slice")
        self.assertEqual(len(index_snap["membership"]), 6)
        self.assertEqual(len(index_snap["battles"]), 12)
        by_uid = {battle["battle_uid"]: battle for battle in index_snap["battles"]}
        first_stage = by_uid["9000000000000000"]
        formation = first_stage["formation"]
        self.assertEqual(formation["stageId"], 51502011)
        self.assertEqual(formation["fighterUin"], "8001")
        self.assertEqual(formation["fighterName"], "斗士0")
        self.assertEqual(formation["damageHp"], "101")
        self.assertEqual(formation["displayDamageHp"], "51",
                         "ceil(101 / 2) for shared HP")
        self.assertEqual(formation["battleUid"], "9000000000000000")
        self.assertEqual(formation["battleTime"], 65)
        self.assertEqual(formation["version"], 2)
        self.assertEqual(formation["skillLevels"], [{"skillId": 4001, "level": 3}])
        self.assertEqual(formation["cardInfos"][0]["cardId"], 101)
        self.assertEqual(formation["chapter"], 50020201)
        # the lineup row keeps the same complete decoded formation
        slot = first_stage["lineup"][0]
        self.assertEqual(slot["member_uid"], "8001")
        self.assertEqual(slot["raw_data"]["stageId"], 51502011)
        self.assertEqual(slot["raw_data"]["displayDamageHp"], "51")
        self.assertEqual(slot["raw_data"]["battleUid"], "9000000000000000")
        self.assertEqual(slot["raw_data"]["fighterName"], "斗士0")
        self.assertEqual(slot["raw_data"]["cardInfos"][0]["cardId"], 101)
        # stage index 1 is version 1: shared HP stays raw
        self.assertEqual(by_uid["9000000000000001"]["formation"]["displayDamageHp"], "102")
        self.assertEqual(first_stage["round"], 1)

    async def test_full_window_lifecycle_slot_statuses_and_sealing(self):
        world = world_with_stages(World())
        clock = Clock()

        def settle_after_end(count, session, max_requests):
            if clock.t >= T0 + 3600:
                settle_world_after_end(world)

        packager = FakePackager()
        job, history, factory = make_job(
            self.root, world, clock, packager=packager, on_session=settle_after_end)
        await drive(job, self.stop,
                    predicate=lambda: len(packager.sealed) >= 3)
        budgets = [(max_requests, max_seconds)
                   for _, max_requests, max_seconds in factory.sessions]
        expected_business = {(80, 300): 24, (400, 900): 3, (200, 600): 3}
        for budget, count in expected_business.items():
            self.assertEqual(budgets.count(budget), count, budget)
        metadata_sessions = [budget for budget in budgets if budget not in expected_business]
        self.assertGreaterEqual(len(metadata_sessions), 2)
        self.assertLessEqual(len(metadata_sessions), 3,
                               "one optional closed-period metadata probe is allowed")
        slots = {row["slot"]: row["status"]
                 for row in history.schedule_slots("test:scope", "guild_war", SEASON)}
        self.assertEqual(slots["ROUTINE"], "SKIPPED",
                         "last routine slot overlaps the endgame and is skipped")
        for trigger in ("PRE_END_300S", "PRE_END_120S", "PRE_END_60S",
                        "PRE_END_30S", "PRE_END_15S", "POST_CLOSE_1H"):
            self.assertEqual(slots[trigger], "FIRED", trigger)
        ranking_runs = [s for s in history.snapshots(mode="guild_war", season_id=SEASON)
                        if s["entries_count"]]
        self.assertEqual(len(ranking_runs), 8)  # 2 routine + 5 pre-end + 1 post-close
        lineup_runs = [s for s in history.snapshots(mode="illusion", season_id=SEASON)
                       if s["battles_count"]]
        self.assertEqual(len(lineup_runs), 3)  # 2 routine + post-close only
        index_runs = [s for s in history.snapshots(mode="guild_war", season_id=SEASON)
                      if s["battles_count"]]
        self.assertEqual(len(index_runs), 3)
        self.assertEqual(packager.sealed,
                         [("test:scope", mode, SEASON) for mode in
                          ("guild_war", "illusion", "nightmare")])
        state = json.loads((self.root / "schedule_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["offsets"],
                         {f"ROUTINE@{mark}": 45
                          for mark in (T0, T0 + 1800, T0 + 3600)})

    async def test_season_extension_postpones_post_close_without_purging(self):
        world = world_with_stages(World())
        clock = Clock()
        packager = FakePackager()

        def extend_after_end(count, session, max_requests):
            if clock.t >= T0 + 3600:
                extended = schedule_pb(27, 2, 2, T0 - 300, T0 + 20000)
                world.schedule = extended
                world.guild_meta = guild_meta_pb(27, 2, 2, T0 - 300, T0 + 20000)
                world.world_page = world_page_pb(
                    [world_row_pb(5001, 1, 777), world_row_pb(5002, 2, 555)],
                    extended, 2)
                world.illusion_meta = illusion_meta_pb(27, T0 + 100_000)

        job, history, _factory = make_job(
            self.root, world, clock, packager=packager, on_session=extend_after_end)
        # Reach the old post-close time: the server reports an extension, so the
        # catch-up must be postponed and nothing may be sealed from the old end.
        await drive(job, self.stop, predicate=lambda: clock.t >= T0 + 7300)
        self.assertEqual(packager.sealed, [],
                         "the old-end post-close must never seal or purge data")
        self.assertTrue(history.snapshots(season_id=SEASON),
                        "runtime data must stay intact while the season is extended")
        slots = {row["slot"]: row["status"]
                 for row in history.schedule_slots("test:scope", "guild_war", SEASON)}
        self.assertEqual(slots["POST_CLOSE_1H"], "SCHEDULED",
                         "postponed to the new end, not marked FIRED")

        # The new post-close at end+3600 (T0 + 23600) finally catches up and seals.
        self.stop = asyncio.Event()
        await drive(job, self.stop, predicate=lambda: len(packager.sealed) >= 3)
        self.assertEqual(packager.sealed,
                         [("test:scope", mode, SEASON) for mode in
                          ("guild_war", "illusion", "nightmare")])
        slots = {row["slot"]: row["status"]
                 for row in history.schedule_slots("test:scope", "guild_war", SEASON)}
        self.assertEqual(slots["POST_CLOSE_1H"], "FIRED")

    async def test_restart_between_end_and_post_close_recovers_catch_up(self):
        world = world_with_stages(World())
        clock = Clock()
        packager = FakePackager()

        def settle_after_end(count, session, max_requests):
            if clock.t >= T0 + 3600:
                settle_world_after_end(world)

        job1, _history1, factory1 = make_job(
            self.root, world, clock, packager=packager, on_session=settle_after_end)
        await drive(job1, self.stop, predicate=lambda: len(factory1.sessions) >= 6)
        self.assertEqual(packager.sealed, [])

        # Simulate a process restart between end and end+3600s: the first
        # process dies right after the routine pipeline, the clock advances,
        # and a fresh job must recover the pending post-close.
        clock.t = T0 + 3700
        self.stop = asyncio.Event()
        job2, history2, _factory2 = restart_job(
            self.root, world, clock, on_session=settle_after_end, packager=packager)
        await drive(job2, self.stop, predicate=lambda: len(packager.sealed) >= 3)
        slots = {row["slot"]: row["status"]
                 for row in history2.schedule_slots("test:scope", "guild_war", SEASON)}
        self.assertEqual(slots["POST_CLOSE_1H"], "FIRED",
                         "the recovered post-close catch-up must fire and seal")
        self.assertEqual(packager.sealed,
                         [("test:scope", mode, SEASON) for mode in
                          ("guild_war", "illusion", "nightmare")])

    async def test_recovery_never_catches_up_expired_pre_end_slots(self):
        world = world_with_stages(World())
        clock = Clock()
        job1, _history1, factory1 = make_job(self.root, world, clock)
        await drive(job1, self.stop, predicate=lambda: len(factory1.sessions) >= 6)

        # Restart past the end: pending pre-end slots are expired and must be
        # skipped, never caught up, while the future post-close still fires.
        clock.t = T0 + 4000
        self.stop = asyncio.Event()
        job2, history2, factory2 = restart_job(self.root, world, clock)
        await drive(job2, self.stop, predicate=lambda: (
            {row["slot"]: row["status"] for row in
             history2.schedule_slots("test:scope", "guild_war", SEASON)}.get("POST_CLOSE_1H") == "FIRED"))

        slots = {row["slot"]: row["status"]
                 for row in history2.schedule_slots("test:scope", "guild_war", SEASON)}
        for trigger in ("PRE_END_300S", "PRE_END_120S", "PRE_END_60S",
                        "PRE_END_30S", "PRE_END_15S"):
            self.assertEqual(slots[trigger], "SKIPPED", trigger)
        fired = {row["trigger"] for row in history2.snapshots(season_id=SEASON)}
        self.assertNotIn("PRE_END_300S", fired)
        self.assertEqual(slots["POST_CLOSE_1H"], "FIRED")
        # One post-close verification probe + one full pipeline; zero sessions
        # were spent catching up the expired endgame slots.
        self.assertEqual(len(factory2.sessions), 1 + 5)

    async def test_expired_pre_end_slot_is_never_caught_up(self):
        world = world_with_stages(World())
        # window ends 200s after T0: PRE_END_300S fired 100s before wakeup
        world.schedule = schedule_pb(27, 2, 2, T0 - 300, T0 + 200)
        world.guild_meta = guild_meta_pb(27, 2, 2, T0 - 300, T0 + 200)
        world.world_page = world_page_pb(
            [world_row_pb(5001, 1, 777), world_row_pb(5002, 2, 555)],
            world.schedule, 2)
        clock = Clock()
        job, history, _factory = make_job(self.root, world, clock)
        # window ends 200s after T0: PRE_END_300S fired 100s before wakeup
        await drive(job, self.stop,
                    predicate=lambda: clock.t >= T0 + 3900)
        slots = {row["slot"]: row["status"]
                 for row in history.schedule_slots("test:scope", "guild_war", SEASON)}
        self.assertEqual(slots["PRE_END_300S"], "SKIPPED")
        self.assertEqual(slots["PRE_END_120S"], "FIRED")
        triggers = [row["trigger"] for row in history.snapshots(mode="guild_war", season_id=SEASON)]
        self.assertNotIn("PRE_END_300S", triggers)

    async def test_pre_end_takes_priority_and_suppresses_overlapping_routine(self):
        world = world_with_stages(World())
        # end = T0 + 255 makes PRE_END_300S (T0 - 45) coincide with the routine
        # slot (T0 - 45); probing at T0 - 100 leaves both fresh, not stale
        world.schedule = schedule_pb(27, 2, 2, T0 - 300, T0 + 255)
        world.guild_meta = guild_meta_pb(27, 2, 2, T0 - 300, T0 + 255)
        world.world_page = world_page_pb(
            [world_row_pb(5001, 1, 777), world_row_pb(5002, 2, 555)],
            world.schedule, 2)
        order = []

        def on_session(count, session, max_requests):
            order.append(max_requests)
            if clock.t >= T0 + 255:
                settle_world_after_end(world)

        clock = Clock(T0 - 100)
        job, history, _factory = make_job(self.root, world, clock, on_session=on_session)
        await drive(job, self.stop, predicate=lambda: clock.t >= T0 + 4000)
        # probe(8) then the first pipeline sessions are ranking-only (80)
        self.assertEqual(order[0], 8)
        self.assertEqual(order[1:4], [80, 80, 80], "pre-end ranking-only fires first")
        self.assertNotIn(400, order[1:4])
        slots = {row["slot"]: row["status"]
                 for row in history.schedule_slots("test:scope", "guild_war", SEASON)}
        self.assertEqual(slots["ROUTINE"], "SKIPPED")
        self.assertEqual(slots["PRE_END_300S"], "FIRED")
        lineup_runs = [s for s in history.snapshots(mode="illusion", season_id=SEASON)
                       if s["battles_count"]]
        self.assertEqual(len(lineup_runs), 1, "only the post-close catch-up collects lineups")

    async def test_post_close_rejection_records_unavailable_and_still_seals(self):
        world = world_with_stages(World())
        clock = Clock()

        def settle_and_reject(count, session, max_requests):
            if clock.t >= T0 + 3600:
                # after the window the server still serves reads but rejects
                # the guild ranking RPC
                settle_world_after_end(world)
                world.reject = {2307: GachaError("RPC_REJECTED")}

        packager = FakePackager()
        job, history, _factory = make_job(
            self.root, world, clock, packager=packager, on_session=settle_and_reject)
        await drive(job, self.stop, predicate=lambda: len(packager.sealed) >= 3)
        slots = {row["slot"]: row["status"]
                 for row in history.schedule_slots("test:scope", "guild_war", SEASON)}
        self.assertEqual(slots["POST_CLOSE_1H"], "UNAVAILABLE")
        self.assertEqual(slots["POST_CLOSE_UNAVAILABLE"], "UNAVAILABLE")
        self.assertEqual(packager.sealed,
                         [("test:scope", mode, SEASON) for mode in
                          ("guild_war", "illusion", "nightmare")])
        state = history.last_run_state("test:scope", "guild_war", SEASON)
        self.assertEqual(state["status"], "INTERRUPTED")
        self.assertEqual(state["interrupt_reason"], "RPC_REJECTED")

    async def test_unknown_phase_skips_guild_index_with_explicit_state(self):
        world = world_with_stages(World())
        world.schedule = schedule_pb(27, 99, 2, T0 - 300, T0 + 3600)
        world.guild_meta = guild_meta_pb(27, 99, 2, T0 - 300, T0 + 3600)
        world.world_page = world_page_pb(
            [world_row_pb(5001, 1, 777), world_row_pb(5002, 2, 555)],
            world.schedule, 2)
        clock = Clock()
        job, history, factory = make_job(self.root, world, clock)
        await drive(job, self.stop, predicate=lambda: (
            history.last_run_state("test:scope", "guild_war", SEASON) is not None
            and history.last_run_state("test:scope", "guild_war", SEASON)["interrupt_reason"] == "UNKNOWN_PHASE"))
        self.assertFalse(
            any(command == 2306 for session, _, _ in factory.sessions
                for command, _ in session.calls),
            "unknown phase never requests 2306 stage indexes")
        state = history.last_run_state("test:scope", "guild_war", SEASON)
        self.assertEqual(state["status"], "INTERRUPTED")
        self.assertEqual(state["interrupt_reason"], "UNKNOWN_PHASE")

    async def test_circuit_breaker_stops_after_first_error_without_retry(self):
        world = World()
        world.reject = {2002: GachaError("RPC_REJECTED")}
        clock = Clock()
        job, history, factory = make_job(self.root, world, clock)
        await drive(job, self.stop, predicate=lambda: len(factory.sessions) >= 3)
        self.assertEqual(len(factory.sessions), 3)  # probe + guild + illusion rankings
        illusion_calls = [c for s, _, _ in factory.sessions for c in s.calls
                          if c[0] == 2002]
        self.assertEqual(len(illusion_calls), 1, "no automatic retry")
        state = history.last_run_state("test:scope", "illusion", SEASON)
        self.assertEqual(state["interrupt_reason"], "RPC_REJECTED")
        slots = {row["slot"]: row["status"]
                 for row in history.schedule_slots("test:scope", "guild_war", SEASON)}
        self.assertEqual(slots["ROUTINE"], "UNAVAILABLE")

    async def test_lineup_season_mismatch_interrupts_without_lineup_requests(self):
        world = World(illusion_players=2, lineup_alt_season=28)
        clock = Clock()
        job, history, factory = make_job(self.root, world, clock)
        await drive(job, self.stop, predicate=lambda: (
            history.last_run_state("test:scope", "illusion", SEASON) is not None
            and history.last_run_state("test:scope", "illusion", SEASON)["interrupt_reason"] == "SEASON_OR_PHASE_CHANGED"))
        self.assertFalse(
            any(command == 2003 for session, _, _ in factory.sessions
                for command, _ in session.calls),
            "season mismatch aborts before any 2003 lineup request")
        state = history.last_run_state("test:scope", "illusion", SEASON)
        self.assertEqual(state["interrupt_reason"], "SEASON_OR_PHASE_CHANGED")

    async def test_graceful_stop_interrupts_the_running_component(self):
        world = world_with_stages(World())
        clock = Clock()

        def on_session(count, session, max_requests):
            if count == 5:  # probe + 3 rankings + lineup session
                self.stop.set()

        job, history, _factory = make_job(self.root, world, clock, on_session=on_session)
        await drive(job, self.stop)
        state = history.last_run_state("test:scope", "illusion", SEASON)
        self.assertEqual(state["status"], "INTERRUPTED")
        self.assertEqual(state["interrupt_reason"], "STOP_REQUESTED")

    async def test_ranking_saves_every_page_evidence_and_full_membership(self):
        world = multi_page_illusion_world()
        clock = Clock()
        job, history, _factory = make_job(self.root, world, clock)
        await drive(job, self.stop, predicate=lambda: (
            len(history.snapshots(mode="illusion", season_id=SEASON)) >= 1))

        snap = history.export_snapshot(
            history.snapshots(mode="illusion", season_id=SEASON)[0]["snapshot_id"])
        self.assertEqual(len(snap["entries"]), 12)
        self.assertEqual(len(snap["membership"]), 12)
        self.assertEqual(snap["coverage"], "COMPLETE_REPORTED_RANGE")
        self.assertEqual(len(snap["page_evidence"]), 4, "3 pages + 1 consistency recheck")
        self.assertEqual([item["echoed_index"] for item in snap["page_evidence"]],
                         [1, 6, 11, 1])
        self.assertTrue(snap["page_evidence"][3]["is_recheck"])
        with sqlite_blobs(history, snap["snapshot_id"]) as blobs:
            # metadata + 3 distinct pages; identical recheck responses dedup by SHA
            self.assertEqual(blobs, 4)

    async def test_post_close_catch_up_reads_closed_leaderboard(self):
        world = world_with_stages(World())
        clock = Clock()

        def close_after_end(count, session, max_requests):
            if clock.t >= T0 + 3600:
                closed = schedule_pb(27, 2, 5, T0 - 300, T0 + 3600)  # state 5 = CLOSED
                world.schedule = closed
                world.guild_meta = guild_meta_pb(27, 2, 5, T0 - 300, T0 + 3600)
                world.world_page = world_page_pb(
                    [world_row_pb(5001, 1, 777), world_row_pb(5002, 2, 555)],
                    closed, 2)
                world.illusion_meta = illusion_meta_pb(27, T0 + 3600)

        job, history, _factory = make_job(self.root, world, clock,
                                         on_session=close_after_end)
        await drive(job, self.stop, predicate=lambda: any(
            row["trigger"] == "POST_CLOSE_1H" and row["mode"] == "illusion"
            and row["entries_count"] == 2
            for row in history.snapshots(season_id=SEASON)))
        post_snaps = {row["mode"]: row for row in history.snapshots(season_id=SEASON)
                      if row["trigger"] == "POST_CLOSE_1H"}
        self.assertEqual(post_snaps["guild_war"]["entries_count"], 3)
        self.assertEqual(post_snaps["illusion"]["entries_count"], 2)
        self.assertEqual(post_snaps["illusion"]["availability"], "CLOSED",
                         "allow_closed reads the ranking but never upgrades CLOSED")

    async def test_failed_ranking_preserves_returned_raw_observations(self):
        world = multi_page_illusion_world()
        world.reject = {2002: [None, GachaError("RPC_REJECTED")]}
        clock = Clock()
        job, history, _factory = make_job(self.root, world, clock)
        await drive(job, self.stop, predicate=lambda: (
            history.last_run_state("test:scope", "illusion", SEASON) is not None
            and history.last_run_state("test:scope", "illusion", SEASON)["status"] == "INTERRUPTED"))

        state = history.last_run_state("test:scope", "illusion", SEASON)
        self.assertEqual(state["interrupt_reason"], "RPC_REJECTED")
        snap = history.export_snapshot(state["snapshot_id"])
        self.assertEqual(len(snap["entries"]), 5,
                         "the first ranking page that did return is preserved")
        self.assertEqual(len(snap["membership"]), 5)
        self.assertEqual(len(snap["page_evidence"]), 1)
        self.assertEqual(snap["coverage"], "PARTIAL",
                         "a failed run never fabricates a complete snapshot")
        with sqlite_blobs(history, snap["snapshot_id"]) as blobs:
            self.assertEqual(blobs, 2)  # metadata + first page raw PBs


class sqlite_blobs:
    def __init__(self, history, snapshot_id):
        import sqlite3

        self.db = sqlite3.connect(history.path)
        self.snapshot_id = snapshot_id

    def __enter__(self):
        return self.db.execute(
            "SELECT count(*) FROM snapshot_blobs WHERE snapshot_id=?",
            (self.snapshot_id,)).fetchone()[0]

    def __exit__(self, *exc_info):
        self.db.close()
