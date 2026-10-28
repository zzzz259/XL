"""Opt-in scheduled leaderboard archive job for one game account.

Scheduling anchors on the guild war metadata window (season/phase/start/end):

- Open period: routine slots at every half-hour mark, fired 30-60s early.
  The random advance is persisted before the slot may fire. Each routine slot
  runs the full pipeline (three rankings, illusion lineups, guild 2306
  index) strictly serially through one session at a time.
- Endgame: PRE_END_300S/120S/60S/30S/15S fire ranking-only collections at
  end - offset. They take priority over routine slots, are never caught up
  once expired, and an imminent pre-end slot suppresses a routine slot.
- Closed period: only low-frequency metadata probes leave; all other RPCs
  are silent.
- POST_CLOSE_1H: one controlled catch-up at end + 3600s. An RPC rejection is
  recorded as POST_CLOSE_UNAVAILABLE; the SeasonPackager then seals every
  mode that has runs for the season.

Per-mode sessions enforce bounded request/duration budgets; any error trips
the circuit for the whole slot (no automatic retries). All network I/O runs
in asyncio.to_thread; stopping is cooperative via the stop event.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gacha_core.gacha import GachaError
from gacha_core.guild_stages import GuildWarStageMap, display_damage_hp, load_stage_map
from gacha_core.leaderboard_records import (
    request_guild_recommendations,
    request_illusion_lineup,
)
from gacha_core.leaderboards import collect_mode
from gacha_core.session import GameGatewaySession
from leaderboard_season_package import SeasonPackager
from leaderboard_seasons import SeasonHistory

from .config import Config, LeaderboardConfig
from .leaderboard_credentials import (
    CredentialError,
    LeaderboardCredentialProvider,
    claim_renewal_owner,
    same_credentials_path,
    shared_account_lock,
)

_logger = logging.getLogger(__name__)

CODE_VERSION = "leaderboard-job/1"
MODES = ("guild_war", "illusion", "nightmare")
FULL_COMPONENTS = ("ranking", "lineup", "guild_index")

PRE_END_OFFSETS = (300, 120, 60, 30, 15)
PRE_END_TRIGGER = {seconds: f"PRE_END_{seconds}S" for seconds in PRE_END_OFFSETS}
ROUTINE_ADVANCE_RANGE = (30, 60)
ROUTINE_SLOT_SPACING = 1800
ROUTINE_TOLERANCE_SECONDS = 300
PRE_END_TOLERANCE_SECONDS = 15
POST_CLOSE_TOLERANCE_SECONDS = 300
PRE_END_CONFLICT_SECONDS = 300
POST_CLOSE_DELAY_SECONDS = 3600

METADATA_MAX_REQUESTS = 8
METADATA_MAX_SECONDS = 60
LINEUP_BATCH_PLAYERS = 50
GUILD_STAGE_BATCH = 5

INTERRUPT_STOPPED = "STOP_REQUESTED"
INTERRUPT_SEASON_CHANGED = "SEASON_OR_PHASE_CHANGED"
INTERRUPT_UNKNOWN_PHASE = "UNKNOWN_PHASE"
FAILURE_DECODE = "DECODE_FAILED"
FAILURE_SAVE = "ARCHIVE_SAVE_FAILED"
FAILURE_NO_SEASON = "NO_SEASON"

_STATE_VERSION = 2

_SAFE_ERROR_CODES = frozenset({
    "REQUEST_LIMIT", "SESSION_TIMEOUT", "RESPONSE_TIMEOUT", "RPC_REJECTED",
    "SESSION_CLOSED", "CONNECTION_CLOSED", "GATEWAY_NETWORK_ERROR",
    "RESPONSE_SIZE_LIMIT", "RESPONSE_LIMIT", "RESPONSE_MISMATCH",
    "INVALID_FRAME", "RPC_NOT_ALLOWED", "AUTH_REJECTED", "AUTH_ERROR",
    "AUTH_NETWORK_ERROR", "SERVER_MAINTENANCE", "NO_ALLOWED_GATEWAY",
    "ENTER_REJECTED", "ACTOR_NOT_FOUND",
})


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _error_code(error: Exception) -> str:
    if isinstance(error, GachaError):
        return error.code if error.code in _SAFE_ERROR_CODES else "REQUEST_FAILED"
    if isinstance(error, ValueError):
        return FAILURE_DECODE
    return "REQUEST_FAILED"


@dataclass(frozen=True)
class Slot:
    key: str
    trigger: str
    kind: str  # "routine" | "pre_end" | "post_close"
    fire_at: float


def _slot_priority(slot: Slot) -> int:
    return {"pre_end": 0, "post_close": 1, "routine": 2}[slot.kind]


class SlotPlanner:
    """Pure scheduling logic over one observed guild war window."""

    def __init__(self, *, random_int=None, offsets=None):
        self._random_int = random_int or (lambda a, b: random.randint(a, b))
        self._offsets = dict(offsets or {})
        self._slots: list[Slot] = []
        self._settled: set[str] = set()
        self.window: tuple[str, int | None, float, float] | None = None

    def offsets(self) -> dict[str, int]:
        return dict(self._offsets)

    def observe_window(self, *, season_id, phase, start, end, now, persist):
        season_id = str(season_id)
        new_window = (season_id, phase, float(start), float(end))
        if self.window == new_window:
            return []
        # The window changed (new season, or the same season/phase with moved
        # boundaries, e.g. an extension): every pending slot of the old window
        # is dropped and nothing may fire or seal by the old end. The settled
        # set resets because the new plan reuses trigger keys at new times.
        self._settled.clear()
        self._slots = []
        self.window = new_window
        planned = []
        first = int(start) - int(start) % ROUTINE_SLOT_SPACING + ROUTINE_SLOT_SPACING
        for mark in range(first, int(end) + 1, ROUTINE_SLOT_SPACING):
            key = f"ROUTINE@{mark}"
            advance = self._offsets.get(key)
            if advance is None:
                advance = self._random_int(*ROUTINE_ADVANCE_RANGE)
                self._offsets[key] = advance
                persist(dict(self._offsets))
            planned.append(Slot(key, "ROUTINE", "routine", mark - advance))
        for seconds in PRE_END_OFFSETS:
            trigger = PRE_END_TRIGGER[seconds]
            planned.append(Slot(trigger, trigger, "pre_end", float(end) - seconds))
        planned.append(Slot("POST_CLOSE_1H", "POST_CLOSE_1H", "post_close",
                            float(end) + POST_CLOSE_DELAY_SECONDS))
        self._slots.extend(planned)
        return planned

    def observe_post_close_recovery(self, *, season_id, phase, start, end):
        """An already-ended window we only just learned about (e.g. after a
        restart between end and end+3600s): schedule the single catch-up slot
        at end + 3600s; expired endgame slots are never recreated."""
        self._settled.clear()
        self._slots = []
        self.window = (str(season_id), phase, float(start), float(end))
        slot = Slot("POST_CLOSE_1H", "POST_CLOSE_1H", "post_close",
                    float(end) + POST_CLOSE_DELAY_SECONDS)
        self._slots.append(slot)
        return [slot]

    def restore(self, window, slots) -> None:
        """Rebuild planner state persisted by a previous process."""
        self._settled.clear()
        self.window = tuple(window)
        self._slots = list(slots)

    def pending_slots(self) -> list[Slot]:
        return [slot for slot in self._slots if slot.key not in self._settled]

    def clear_window(self) -> None:
        self.window = None
        self._slots = []
        self._settled.clear()

    def settle(self, slot: Slot) -> None:
        self._settled.add(slot.key)

    def has_pending(self) -> bool:
        return any(slot.key not in self._settled for slot in self._slots)

    def next_due(self, now: float) -> Slot | None:
        due = [slot for slot in self._slots
               if slot.key not in self._settled and slot.fire_at <= now]
        if not due:
            return None
        return min(due, key=lambda slot: (_slot_priority(slot), slot.fire_at))

    def next_fire_delay(self, now: float) -> float | None:
        pending = [slot.fire_at for slot in self._slots if slot.key not in self._settled]
        if not pending:
            return None
        return max(0.0, min(pending) - now)

    def is_stale(self, slot: Slot, now: float) -> bool:
        if slot.kind == "pre_end":
            return now - slot.fire_at > PRE_END_TOLERANCE_SECONDS
        if slot.kind == "post_close":
            return now - slot.fire_at > POST_CLOSE_TOLERANCE_SECONDS
        return now - slot.fire_at > ROUTINE_TOLERANCE_SECONDS

    def conflicts_with_pre_end(self, slot: Slot, now: float) -> bool:
        if slot.kind != "routine":
            return False
        return any(
            other.kind == "pre_end" and other.key not in self._settled
            and other.fire_at <= now + PRE_END_CONFLICT_SECONDS
            for other in self._slots
        )


class LeaderboardArchiveJob:
    """Assemble scheduling, bounded collection, and SeasonHistory adaptation."""

    def __init__(
        self,
        config: LeaderboardConfig,
        *,
        provider,
        history: SeasonHistory,
        stage_map: GuildWarStageMap,
        session_factory,
        packager=None,
        now=time.time,
        random_int=None,
        sleep=None,
    ):
        self._config = config
        self._provider = provider
        self._history = history
        self._stage_map = stage_map
        self._session_factory = session_factory
        self._packager = packager
        self._now = now
        self._sleep = sleep or asyncio.sleep
        state = self._load_state()
        self._planner = SlotPlanner(random_int=random_int, offsets=state["offsets"])
        self._last_probe = state["last_probe"]
        self._closed_out = state["closed_out"]
        self._open_triggers: set[str] = set()
        if state["window"] is not None and state["pending_slots"]:
            self._planner.restore(state["window"], state["pending_slots"])
            self._open_triggers = {slot.trigger for slot in state["pending_slots"]}

    # -- persisted schedule state ------------------------------------------

    def _state_path(self) -> Path:
        return Path(self._config.data_dir) / "schedule_state.json"

    def _load_state(self):
        empty = {"offsets": {}, "last_probe": None, "window": None,
                 "pending_slots": [], "closed_out": []}
        try:
            raw = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty
        if not isinstance(raw, dict) or raw.get("version") != _STATE_VERSION:
            return empty
        offsets = raw.get("offsets")
        if not isinstance(offsets, dict):
            offsets = {}
        offsets = {str(key): int(value) for key, value in offsets.items()
                   if str(key).startswith("ROUTINE@")}
        last_probe = raw.get("last_probe")
        probe = float(last_probe) if isinstance(last_probe, (int, float)) else None
        window = self._load_window(raw.get("window"))
        pending = self._load_pending_slots(raw.get("pending_slots"))
        if window is None:
            pending = []
        closed_out = raw.get("closed_out")
        if not isinstance(closed_out, list):
            closed_out = []
        closed_out = [list(item) for item in closed_out
                      if isinstance(item, (list, tuple)) and len(item) == 3]
        return {"offsets": offsets, "last_probe": probe, "window": window,
                "pending_slots": pending, "closed_out": closed_out}

    @staticmethod
    def _load_window(raw):
        if not isinstance(raw, dict):
            return None
        season = raw.get("season_id")
        phase = raw.get("phase")
        start, end = raw.get("start"), raw.get("end")
        if (not isinstance(season, str) or not season
                or (phase is not None and type(phase) is not int)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float)) or not start < end):
            return None
        return (season, phase, float(start), float(end))

    @staticmethod
    def _load_pending_slots(raw):
        slots = []
        if not isinstance(raw, list):
            return slots
        for item in raw:
            if not isinstance(item, dict):
                continue
            key, trigger, kind = item.get("key"), item.get("trigger"), item.get("kind")
            fire_at = item.get("fire_at")
            if (not isinstance(key, str) or not isinstance(trigger, str)
                    or kind not in ("routine", "pre_end", "post_close")
                    or not isinstance(fire_at, (int, float))):
                continue
            slots.append(Slot(key, trigger, kind, float(fire_at)))
        return slots

    def _persist_offsets(self, offsets) -> None:
        self._write_state(offsets=offsets)

    def _persist_last_probe(self) -> None:
        self._write_state(offsets=self._planner.offsets())

    def _write_state(self, *, offsets) -> None:
        path = self._state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            window = self._planner.window
            payload = json.dumps({
                "version": _STATE_VERSION,
                "offsets": {key: int(value) for key, value in offsets.items()},
                "last_probe": self._last_probe,
                "window": None if window is None else {
                    "season_id": window[0], "phase": window[1],
                    "start": window[2], "end": window[3]},
                "pending_slots": [{"key": slot.key, "trigger": slot.trigger,
                                   "kind": slot.kind, "fire_at": slot.fire_at}
                                  for slot in self._planner.pending_slots()],
                "closed_out": self._closed_out,
            }, sort_keys=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        except OSError:
            _logger.warning("leaderboard schedule state persist failed")

    # -- main loop -----------------------------------------------------------

    async def run(self, stop_event) -> None:
        while not stop_event.is_set():
            now = self._now()
            slot = self._planner.next_due(now)
            if slot is None:
                delay = self._planner.next_fire_delay(now)
                if self._planner.window is None:
                    probe_in = 0.0 if self._last_probe is None else max(
                        0.0, self._last_probe + self._config.metadata_probe_interval - now)
                    delay = probe_in if delay is None else min(delay, probe_in)
                if delay is None:
                    delay = 1.0
                stopped = await self._wait(delay, stop_event)
                if stopped:
                    break
                if self._planner.window is None and not stop_event.is_set():
                    woke = self._now()
                    if (self._last_probe is None
                            or woke - self._last_probe >= self._config.metadata_probe_interval):
                        await self._probe(woke)
                continue
            await self._fire(slot, stop_event)
            if self._planner.window is not None and not self._planner.has_pending():
                self._planner.clear_window()

    async def _wait(self, delay: float, stop_event) -> bool:
        if delay <= 0:
            return stop_event.is_set()
        sleep_task = asyncio.ensure_future(self._sleep(delay))
        stop_task = asyncio.ensure_future(stop_event.wait())
        done, pending = await asyncio.wait(
            {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        return stop_task in done

    # -- metadata probing -----------------------------------------------------

    async def _probe(self, now: float) -> None:
        self._last_probe = now
        self._persist_last_probe()
        try:
            web = await self._provider.credentials()
        except CredentialError:
            return
        try:
            result = await asyncio.to_thread(self._probe_metadata, web)
        except GachaError as error:
            _logger.warning("leaderboard metadata probe failed: %s", error.code)
            return
        except Exception:  # noqa: BLE001 - never persist raw transport text
            _logger.warning("leaderboard metadata probe failed: PROBE_FAILED")
            return
        availability = result.get("availability")
        season = result.get("season_id")
        metadata = result.get("metadata", {})
        start, end = metadata.get("startStamp"), metadata.get("endStamp")
        ended = isinstance(start, int) and isinstance(end, int) and 0 < start < end
        if season and availability in ("ACTIVE", "SETTLING", "SETTLED_CANDIDATE") \
                and ended and end > now:
            planned = self._planner.observe_window(
                season_id=season, phase=metadata.get("phase"),
                start=float(start), end=float(end), now=now,
                persist=self._persist_offsets)
            for slot in planned:
                self._plan_history_slot(slot)
            self._write_state(offsets=self._planner.offsets())
        elif (season and availability in ("SETTLED_CANDIDATE", "CLOSED") and ended
                and float(end) <= now
                and float(end) + POST_CLOSE_DELAY_SECONDS
                > now - POST_CLOSE_TOLERANCE_SECONDS
                and [str(season), metadata.get("phase"), float(end)]
                not in self._closed_out):
            # The window already ended (e.g. a restart between end and the
            # catch-up): schedule only the post-close slot at end + 3600s.
            planned = self._planner.observe_post_close_recovery(
                season_id=season, phase=metadata.get("phase"),
                start=float(start), end=float(end))
            for slot in planned:
                self._plan_history_slot(slot)
            self._write_state(offsets=self._planner.offsets())
        elif availability not in ("CLOSED", "NOT_OPEN", "SETTLED_CANDIDATE"):
            _logger.info("leaderboard metadata probe state=%s", availability or "UNKNOWN")

    def _probe_metadata(self, web):
        bodies: list[bytes] = []
        session = self._open_session(web, METADATA_MAX_REQUESTS, METADATA_MAX_SECONDS, bodies)
        with session as active:
            return collect_mode(active, "guild_war", scope=self._config.scope,
                                metadata_only=True, now=self._now)

    def _plan_history_slot(self, slot: Slot) -> None:
        window = self._planner.window
        if window is None:
            return
        self._open_triggers.add(slot.trigger)
        try:
            self._history.plan_schedule_slot(
                self._config.scope, "guild_war", window[0], slot.trigger, _iso(slot.fire_at))
        except (ValueError, KeyError):
            _logger.warning("leaderboard schedule slot plan failed: %s", slot.trigger)

    def _settle_history_slot(self, trigger: str, status: str, run_id=None) -> None:
        window = self._planner.window
        if window is None:
            return
        try:
            self._history.fire_schedule_slot(
                self._config.scope, "guild_war", window[0], trigger,
                run_id=run_id, status=status)
        except (ValueError, KeyError):
            _logger.warning("leaderboard schedule slot settle failed: %s", trigger)

    def _settle_slot(self, slot: Slot, status: str, run_id=None) -> None:
        self._planner.settle(slot)
        self._open_triggers.discard(slot.trigger)
        self._settle_history_slot(slot.trigger, status, run_id=run_id)
        self._write_state(offsets=self._planner.offsets())

    # -- slot firing -----------------------------------------------------------

    async def _fire(self, slot: Slot, stop_event) -> None:
        now = self._now()
        if self._planner.is_stale(slot, now):
            self._settle_slot(slot, "SKIPPED")
            return
        if self._planner.conflicts_with_pre_end(slot, now):
            self._settle_slot(slot, "SKIPPED")
            return
        if slot.kind == "post_close":
            await self._fire_post_close(slot, stop_event)
            return
        components = ("ranking",) if slot.kind == "pre_end" else FULL_COMPONENTS
        try:
            run_ids, failure = await self._run_pipeline(slot.trigger, components, stop_event)
        except CredentialError:
            self._settle_slot(slot, "UNAVAILABLE")
            return
        status = "FIRED" if failure is None else "UNAVAILABLE"
        self._settle_slot(slot, status, run_id=run_ids.get("guild_war"))

    async def _post_close_was_extended(self) -> bool:
        """Verify the season really ended before the catch-up purges anything.

        Returns True when the metadata shows the same season with an end still
        in the future (extension or still-active): the window is replanned by
        the new end and the catch-up is postponed instead of fired or sealed.
        """
        window = self._planner.window
        if window is None:
            return False
        try:
            web = await self._provider.credentials()
            result = await asyncio.to_thread(self._probe_metadata, web)
        except CredentialError:
            return False
        except GachaError as error:
            _logger.warning("leaderboard post-close verification failed: %s", error.code)
            return False
        except Exception:  # noqa: BLE001 - fixed code only
            _logger.warning("leaderboard post-close verification failed: PROBE_FAILED")
            return False
        season = result.get("season_id")
        metadata = result.get("metadata", {})
        start, end = metadata.get("startStamp"), metadata.get("endStamp")
        now = self._now()
        if (season and str(season) == window[0]
                and isinstance(start, int) and isinstance(end, int)
                and 0 < start < end and float(end) > now):
            planned = self._planner.observe_window(
                season_id=season, phase=metadata.get("phase"),
                start=float(start), end=float(end), now=now,
                persist=self._persist_offsets)
            for slot in planned:
                self._plan_history_slot(slot)
            replanned = {slot.trigger for slot in planned}
            for trigger in sorted(self._open_triggers - replanned):
                self._settle_history_slot(trigger, "SKIPPED")
            self._open_triggers &= replanned
            self._write_state(offsets=self._planner.offsets())
            _logger.info("leaderboard season extended: post-close postponed")
            return True
        return False

    async def _fire_post_close(self, slot: Slot, stop_event) -> None:
        if await self._post_close_was_extended():
            return
        window = self._planner.window
        rejected = False
        try:
            _run_ids, failure = await self._run_pipeline(
                slot.trigger, FULL_COMPONENTS, stop_event)
            rejected = failure == "RPC_REJECTED"
        except CredentialError:
            rejected = False
        if rejected:
            self._settle_history_slot("POST_CLOSE_UNAVAILABLE", "UNAVAILABLE")
        self._settle_slot(slot, "UNAVAILABLE" if rejected else "FIRED")
        await self._seal_seasons(stop_event)
        self._planner.clear_window()
        if window is not None:
            self._closed_out.append([window[0], window[1], window[3]])
        # The catch-up pipeline just validated the season metadata; treat it as
        # a fresh probe so the closed loop does not immediately re-login.
        self._last_probe = self._now()
        self._persist_last_probe()

    async def _seal_seasons(self, stop_event) -> None:
        if self._packager is None:
            return
        window = self._planner.window
        if window is None:
            return
        season = window[0]
        for mode in MODES:
            if stop_event.is_set():
                return
            try:
                state = await asyncio.to_thread(
                    self._history.last_run_state, self._config.scope, mode, season)
            except (ValueError, KeyError):
                state = None
            if state is None:
                continue
            try:
                entry = await asyncio.to_thread(
                    self._packager.seal_season, self._config.scope, mode, season)
            except Exception as exc:  # noqa: BLE001 - fixed code only
                _logger.warning("leaderboard season seal failed: %s", type(exc).__name__)
            else:
                _logger.info("leaderboard season sealed: %s %s %s",
                             mode, season, entry["package"])

    # -- collection pipeline ----------------------------------------------------

    async def _run_pipeline(self, trigger: str, components, stop_event):
        web = await self._provider.credentials()
        run_ids: dict[str, str] = {}
        ranking_results: dict[str, dict] = {}
        failure = None
        for mode in MODES:
            if stop_event.is_set():
                failure = failure or INTERRUPT_STOPPED
                break
            run_id, result, error = await self._collect_ranking(mode, web, trigger)
            if run_id is not None:
                run_ids[mode] = run_id
            if error is not None:
                failure = error
                break
            ranking_results[mode] = result
        if (failure is None and not stop_event.is_set()
                and "lineup" in components and "illusion" in ranking_results):
            failure = await self._collect_lineups(
                web, trigger, ranking_results["illusion"], stop_event)
        if (failure is None and not stop_event.is_set()
                and "guild_index" in components and "guild_war" in ranking_results):
            failure = await self._collect_guild_index(
                web, trigger, ranking_results["guild_war"], stop_event)
        if stop_event.is_set() and failure is None:
            failure = INTERRUPT_STOPPED
        return run_ids, failure

    def _open_session(self, web, max_requests: int, max_seconds: int, bodies) -> object:
        def observer(command, body):
            bodies.append(bytes(body))

        return self._session_factory(
            web, max_requests=max_requests, max_seconds=max_seconds,
            response_observer=observer)

    async def _collect_ranking(self, mode, web, trigger):
        bodies: list[bytes] = []
        # The post-close catch-up opts into closed-window reads; every other
        # trigger keeps the default closed-period silence.
        allow_closed = trigger == "POST_CLOSE_1H"

        def work():
            session = self._open_session(
                web, self._config.ranking_max_requests,
                self._config.ranking_max_seconds, bodies)
            with session as active:
                return collect_mode(
                    active, mode, scope=self._config.scope,
                    max_pages=self._config.ranking_max_pages,
                    max_entries=self._config.ranking_max_entries,
                    allow_closed=allow_closed, now=self._now)

        try:
            result = await asyncio.to_thread(work)
        except GachaError as error:
            _logger.warning("leaderboard ranking session failed: %s", error.code)
            return None, None, _error_code(error)
        except Exception:  # noqa: BLE001 - fixed code only
            _logger.warning("leaderboard ranking session failed: REQUEST_FAILED")
            return None, None, "REQUEST_FAILED"
        season = result.get("season_id")
        if not season:
            _logger.warning("leaderboard ranking run skipped: %s", FAILURE_NO_SEASON)
            return None, result, FAILURE_NO_SEASON
        if result.get("collection_error"):
            run_id = await asyncio.to_thread(
                self._history.begin_run, self._config.scope, mode, season,
                trigger=trigger, code_version=CODE_VERSION,
                metadata=result.get("metadata"))
            await asyncio.to_thread(
                self._preserve_failed_ranking, run_id, result, bodies)
            await asyncio.to_thread(
                self._history.interrupt_run, run_id, result["collection_error"])
            return run_id, result, result["collection_error"]
        try:
            run_id = await asyncio.to_thread(
                self._save_ranking_run, mode, season, trigger, result, bodies)
        except (ValueError, KeyError):
            _logger.warning("leaderboard ranking archive failed: %s", FAILURE_SAVE)
            return None, result, FAILURE_SAVE
        return run_id, result, None

    def _save_ranking_run(self, mode, season, trigger, result, bodies):
        evidence_pages = result.get("page_evidence") or [{}]
        pages = [{
            "entries": result.get("entries", []),
            "membership": _full_membership(result),
            "evidence": evidence_pages[0],
            "raw_payloads": bodies,
        }]
        # Every remaining page evidence is kept on its own page so nothing is
        # dropped, without repeating entries/membership or raw payload links.
        pages.extend({"evidence": item} for item in evidence_pages[1:])
        return self._history.save_run(
            scope=self._config.scope, mode=mode, season_id=season, trigger=trigger,
            code_version=CODE_VERSION, coverage=result["coverage"],
            settle_evidence=result["settle_evidence"],
            availability=result["availability"],
            metadata=result.get("metadata"), pages=pages)["run_id"]

    def _preserve_failed_ranking(self, run_id, result, bodies):
        """Keep whatever the failed attempt already returned, without ever
        letting the interrupted run claim a complete snapshot."""
        evidence_pages = result.get("page_evidence") or []
        if not (evidence_pages or bodies or result.get("entries")
                or result.get("member_entries")):
            return
        pages = [{
            "entries": result.get("entries", []),
            "membership": _full_membership(result),
            "evidence": evidence_pages[0] if evidence_pages else {},
            "raw_payloads": bodies,
        }]
        pages.extend({"evidence": item} for item in evidence_pages[1:])
        try:
            for page in pages:
                self._history.append_page(run_id, **page)
        except ValueError:
            _logger.warning("leaderboard failed-run preservation incomplete: %s",
                            FAILURE_SAVE)

    async def _collect_lineups(self, web, trigger, ranking_result, stop_event):
        season = ranking_result["season_id"]
        players = [entry["entity_id"] for entry in ranking_result.get("entries", [])]
        if not players:
            return None
        batches = [players[i:i + LINEUP_BATCH_PLAYERS]
                   for i in range(0, len(players), LINEUP_BATCH_PLAYERS)]
        run_id = await asyncio.to_thread(
            self._history.begin_run, self._config.scope, "illusion", season,
            trigger=trigger, code_version=CODE_VERSION)
        state = await asyncio.to_thread(self._history.run_state, run_id)
        return await asyncio.to_thread(
            self._lineup_worker, web, run_id, season, batches, state["next_page"],
            _full_membership(ranking_result), stop_event)

    def _lineup_worker(self, web, run_id, season, batches, start_index, membership,
                       stop_event):
        bodies: list[bytes] = []
        session = self._open_session(
            web, self._config.lineup_max_requests,
            self._config.lineup_max_seconds, bodies)
        try:
            with session as active:
                before = collect_mode(active, "illusion", scope=self._config.scope,
                                      metadata_only=True, now=self._now)
                if before.get("season_id") != season:
                    self._history.interrupt_run(run_id, INTERRUPT_SEASON_CHANGED)
                    return INTERRUPT_SEASON_CHANGED
                for index in range(start_index, len(batches)):
                    if stop_event.is_set():
                        self._history.interrupt_run(run_id, INTERRUPT_STOPPED)
                        return None
                    mark = len(bodies)
                    battles = []
                    for uin in batches[index]:
                        raws: list[bytes] = []
                        try:
                            rounds = request_illusion_lineup(
                                active, uin,
                                on_response=lambda command, body, decoded, sink=raws: sink.append(body))
                        except (GachaError, ValueError) as error:
                            code = _error_code(error)
                            self._history.interrupt_run(run_id, code)
                            return code
                        battles.append({
                            "battle_uid": str(uin),
                            "round": len(rounds),
                            "formation": {},
                            "lineup": [{
                                "member_uid": str(uin),
                                "slot": row["round"],
                                "raw_data": {
                                    "round": row["round"],
                                    "score": row["score"],
                                    "cards": row["cards"],
                                },
                            } for row in rounds],
                            "presence": [],
                            "raw_payload": raws[0] if raws else None,
                        })
                    self._history.append_page(
                        run_id, entries=[], membership=membership if index == start_index else [],
                        battles=battles,
                        evidence={"request_index": index, "entry_count": len(battles),
                                  "source_rpc": 2003},
                        raw_payloads=bodies[mark:])
                after = collect_mode(active, "illusion", scope=self._config.scope,
                                     metadata_only=True, now=self._now)
                if after.get("season_id") != season:
                    self._history.interrupt_run(run_id, INTERRUPT_SEASON_CHANGED)
                    return INTERRUPT_SEASON_CHANGED
                availability = after.get("availability", "UNKNOWN")
                self._history.finish_run(
                    run_id, coverage="COMPLETE_REPORTED_RANGE",
                    settle_evidence=_settle_evidence(availability),
                    availability=availability)
                return None
        except GachaError as error:
            code = _error_code(error)
            _logger.warning("leaderboard lineup session failed: %s", code)
            self._interrupt_quietly(run_id, code)
            return code
        except ValueError:
            self._interrupt_quietly(run_id, FAILURE_SAVE)
            return FAILURE_SAVE
        except Exception:  # noqa: BLE001 - fixed code only, never kill the loop
            _logger.warning("leaderboard lineup session failed: REQUEST_FAILED")
            self._interrupt_quietly(run_id, "REQUEST_FAILED")
            return "REQUEST_FAILED"

    async def _collect_guild_index(self, web, trigger, ranking_result, stop_event):
        season = ranking_result["season_id"]
        metadata = ranking_result.get("metadata", {})
        phase = metadata.get("phase")
        run_id = await asyncio.to_thread(
            self._history.begin_run, self._config.scope, "guild_war", season,
            trigger=trigger, code_version=CODE_VERSION)
        state = await asyncio.to_thread(self._history.run_state, run_id)
        return await asyncio.to_thread(
            self._guild_index_worker, web, run_id, season, phase,
            state["next_page"], _full_membership(ranking_result), stop_event)

    def _guild_index_worker(self, web, run_id, season, phase, start_index,
                            membership, stop_event):
        bodies: list[bytes] = []
        session = self._open_session(
            web, self._config.guild_index_max_requests,
            self._config.guild_index_max_seconds, bodies)
        try:
            with session as active:
                before = collect_mode(active, "guild_war", scope=self._config.scope,
                                      metadata_only=True, now=self._now)
                before_meta = before.get("metadata", {})
                if (before.get("season_id") != season
                        or before_meta.get("phase") != phase):
                    self._history.interrupt_run(run_id, INTERRUPT_SEASON_CHANGED)
                    return INTERRUPT_SEASON_CHANGED
                stage_ids = self._stage_map.stage_ids(phase)
                if stage_ids is None:
                    self._history.interrupt_run(run_id, INTERRUPT_UNKNOWN_PHASE)
                    return INTERRUPT_UNKNOWN_PHASE
                batches = [stage_ids[i:i + GUILD_STAGE_BATCH]
                           for i in range(0, len(stage_ids), GUILD_STAGE_BATCH)]
                for index in range(start_index, len(batches)):
                    if stop_event.is_set():
                        self._history.interrupt_run(run_id, INTERRUPT_STOPPED)
                        return None
                    mark = len(bodies)
                    battles = []
                    for stage_id in batches[index]:
                        raws: list[bytes] = []
                        try:
                            rows = request_guild_recommendations(
                                active, stage_id,
                                on_response=lambda command, body, decoded, sink=raws: sink.append(body))
                        except (GachaError, ValueError) as error:
                            code = _error_code(error)
                            self._history.interrupt_run(run_id, code)
                            return code
                        battles.extend(self._formation_battles(rows))
                    self._history.append_page(
                        run_id, entries=[], membership=membership if index == start_index else [],
                        battles=battles,
                        evidence={"request_index": index, "entry_count": len(battles),
                                  "source_rpc": 2306},
                        raw_payloads=bodies[mark:])
                after = collect_mode(active, "guild_war", scope=self._config.scope,
                                     metadata_only=True, now=self._now)
                after_meta = after.get("metadata", {})
                if (after.get("season_id") != season
                        or after_meta.get("phase") != phase):
                    self._history.interrupt_run(run_id, INTERRUPT_SEASON_CHANGED)
                    return INTERRUPT_SEASON_CHANGED
                availability = after.get("availability", "UNKNOWN")
                self._history.finish_run(
                    run_id, coverage="SERVER_VISIBLE_SLICE",
                    settle_evidence=_settle_evidence(availability),
                    availability=availability)
                return None
        except GachaError as error:
            code = _error_code(error)
            _logger.warning("leaderboard guild index session failed: %s", code)
            self._interrupt_quietly(run_id, code)
            return code
        except ValueError:
            self._interrupt_quietly(run_id, FAILURE_SAVE)
            return FAILURE_SAVE
        except Exception:  # noqa: BLE001 - fixed code only, never kill the loop
            _logger.warning("leaderboard guild index session failed: REQUEST_FAILED")
            self._interrupt_quietly(run_id, "REQUEST_FAILED")
            return "REQUEST_FAILED"

    def _formation_battles(self, rows):
        battles = []
        for row in rows:
            stage_id = int(row["stageId"])
            stage = self._stage_map.stage(stage_id) or {}
            version = row.get("version", 0)
            share_hp = self._stage_map.share_hp_count(stage_id)
            display = display_damage_hp(int(row["damageHp"]), version, share_hp)
            decoded = {
                "stageId": stage_id,
                "fighterUin": row["fighterUin"],
                "fighterName": row.get("fighterName"),
                "cardInfos": row.get("cardInfos", []),
                "damageHp": row["damageHp"],
                "displayDamageHp": str(display),
                "battleUid": row["battleUid"],
                "battleTime": row.get("battleTime"),
                "version": row.get("version"),
                "skillLevels": row.get("skillLevels", []),
            }
            battles.append({
                "battle_uid": str(row["battleUid"]),
                "round": stage.get("round"),
                "formation": dict(decoded, chapter=stage.get("chapter_id")),
                "lineup": [{"member_uid": str(row["fighterUin"]),
                            "raw_data": dict(decoded)}],
                "presence": [],
                "raw_payload": None,
            })
        return battles

    def _interrupt_quietly(self, run_id, reason) -> None:
        try:
            self._history.interrupt_run(run_id, reason)
        except ValueError:
            _logger.warning("leaderboard run interrupt failed: %s", reason)


def _membership_row(entry: dict) -> dict:
    return {
        "member_uid": entry["entity_id"],
        "display_name": entry.get("display_name"),
        "score": entry.get("score"),
        "level": entry.get("level"),
        "raw_data": entry.get("raw_data"),
    }


def _full_membership(result: dict) -> list:
    """Entries plus member_entries as one deduplicated identity-ordered list.

    Ranking entries feed the membership snapshot as well, so presence history
    sees the full visible list; member_entries keep their own raw rows unless
    the identity was already covered by an entry.
    """
    rows = []
    seen = set()
    for entry in result.get("entries", []):
        uid = str(entry["entity_id"])
        if uid in seen:
            continue
        seen.add(uid)
        rows.append(_membership_row(entry))
    for entry in result.get("member_entries", []):
        uid = str(entry["entity_id"])
        if uid in seen:
            continue
        seen.add(uid)
        rows.append(_membership_row(entry))
    return rows


def _settle_evidence(availability) -> str:
    if availability == "ACTIVE":
        return "LIVE"
    if availability == "SETTLED_CANDIDATE":
        return "SETTLED_CANDIDATE"
    return "LAST_OBSERVED"


def build_leaderboard_job(
    config: Config,
    *,
    provider=None,
    history=None,
    stage_map=None,
    session_factory=None,
    packager=None,
    now=time.time,
    random_int=None,
    sleep=None,
):
    """Assemble the archive job; ``None`` unless explicitly enabled.

    Renewal ownership is resolved here: when the status monitor already
    renews the same state path, the job binds read-only; an independent path
    may be renewed by the job, claiming single ownership of that state.
    """
    lb = config.leaderboard
    if not lb.enabled:
        return None
    data_dir = Path(lb.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    if history is None:
        history = SeasonHistory(data_dir / "seasonal_leaderboards.sqlite3")
    if stage_map is None:
        stage_map = load_stage_map()
    if provider is None:
        read_only = True
        if lb.refresh_enabled:
            status = config.server_status
            status_renews_same_state = (
                status.enabled and status.refresh_enabled and status.credentials_file
                and same_credentials_path(status.credentials_file, lb.credentials_file)
            )
            if status_renews_same_state:
                _logger.info("leaderboard credential renewal owner=status-monitor")
            else:
                if claim_renewal_owner(lb.credentials_file, "leaderboard-job"):
                    read_only = False
                else:
                    _logger.warning(
                        "leaderboard credential renewal owner conflict: READ_ONLY")
        provider = LeaderboardCredentialProvider(
            lb, read_only=read_only,
            account_lock=shared_account_lock(lb.credentials_file))
    if session_factory is None:
        def session_factory(credentials, *, max_requests, max_seconds, response_observer):
            return GameGatewaySession(
                credentials, max_requests=max_requests, max_duration=max_seconds,
                request_delay=lb.request_delay, response_observer=response_observer)
    if packager is None:
        packager = SeasonPackager(history, data_dir / "packages")
    return LeaderboardArchiveJob(
        lb, provider=provider, history=history, stage_map=stage_map,
        session_factory=session_factory, packager=packager, now=now,
        random_int=random_int, sleep=sleep)
