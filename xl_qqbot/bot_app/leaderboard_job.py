"""Opt-in scheduled leaderboard archive job for one game account.

Every mode (guild war / illusion / nightmare) is scheduled independently from
its own metadata window (season/phase/start/end); a closed mode never blocks
another mode:

- Probing: one bounded session reads all three mode metadata RPCs at
  metadata_probe_interval; each result updates only its own mode's window.
- Open period: each mode gets routine slots at every half-hour mark, fired
  30-60s early (the random advance is persisted before the slot may fire). A
  routine slot runs only its own mode's pipeline (ranking plus, for guild war,
  the 2306 index, and for illusion, the lineups) strictly serially.
- Endgame: PRE_END_300S/120S/60S/30S/15S fire ranking-only collections per
  mode at that mode's end - offset. They take priority over routine slots,
  are never caught up once expired, and an imminent pre-end slot suppresses
  only its own mode's routine slot.
- Closed period: closed modes issue nothing beyond the low-frequency
  metadata probes.
- POST_CLOSE_1H: each mode catches up independently at its own end + 3600s
  (ranking reads opt into closed windows); an RPC rejection is recorded as
  POST_CLOSE_UNAVAILABLE, and the SeasonPackager seals only that mode.

Per-mode sessions enforce bounded request/duration budgets; any error trips
the circuit for that slot only (no automatic retries). All network I/O runs
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
# Each routine slot runs only its own mode's pipeline; closed modes have no
# slots at all and stay silent beyond the low-frequency metadata probes.
ROUTINE_COMPONENTS = {
    "guild_war": ("ranking", "guild_index"),
    "illusion": ("ranking", "lineup"),
    "nightmare": ("ranking",),
}

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

_STATE_VERSION = 3

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
    mode: str
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

    def observe_window(self, *, mode, season_id, phase, start, end, now, persist):
        season_id = str(season_id)
        start_value = None if start is None else float(start)
        new_window = (season_id, phase, start_value, float(end))
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
        # Modes without a startStamp (illusion) schedule from the next
        # half-hour mark after the observation.
        base = int(now) if start_value is None else int(start_value)
        first = base - base % ROUTINE_SLOT_SPACING + ROUTINE_SLOT_SPACING
        for mark in range(first, int(end) + 1, ROUTINE_SLOT_SPACING):
            key = f"{mode}:ROUTINE@{mark}"
            advance = self._offsets.get(key)
            if advance is None:
                advance = self._random_int(*ROUTINE_ADVANCE_RANGE)
                self._offsets[key] = advance
                persist(dict(self._offsets))
            planned.append(Slot(mode, key, "ROUTINE", "routine", mark - advance))
        for seconds in PRE_END_OFFSETS:
            trigger = PRE_END_TRIGGER[seconds]
            planned.append(Slot(mode, f"{mode}:{trigger}", trigger, "pre_end",
                                float(end) - seconds))
        planned.append(Slot(mode, f"{mode}:POST_CLOSE_1H", "POST_CLOSE_1H",
                            "post_close", float(end) + POST_CLOSE_DELAY_SECONDS))
        self._slots.extend(planned)
        return planned

    def observe_post_close_recovery(self, *, mode, season_id, phase, start, end):
        """An already-ended window we only just learned about (e.g. after a
        restart between end and end+3600s): schedule the single catch-up slot
        at end + 3600s; expired endgame slots are never recreated."""
        self._settled.clear()
        self._slots = []
        self.window = (str(season_id), phase,
                       None if start is None else float(start), float(end))
        slot = Slot(mode, f"{mode}:POST_CLOSE_1H", "POST_CLOSE_1H", "post_close",
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
        self._planners = {
            mode: SlotPlanner(random_int=random_int, offsets=state["offsets"])
            for mode in MODES
        }
        self._last_probe = state["last_probe"]
        self._closed_out = {mode: state["modes"][mode]["closed_out"] for mode in MODES}
        self._open_triggers: set[tuple[str, str]] = set()
        for mode in MODES:
            mode_state = state["modes"][mode]
            if mode_state["window"] is not None and mode_state["pending_slots"]:
                self._planners[mode].restore(mode_state["window"],
                                             mode_state["pending_slots"])
                self._open_triggers |= {
                    (mode, slot.trigger) for slot in mode_state["pending_slots"]}

    # -- persisted schedule state ------------------------------------------

    def _state_path(self) -> Path:
        return Path(self._config.data_dir) / "schedule_state.json"

    def _empty_state(self):
        return {
            "offsets": {},
            "last_probe": None,
            "modes": {mode: {"window": None, "pending_slots": [], "closed_out": []}
                      for mode in MODES},
        }

    def _load_state(self):
        empty = self._empty_state()
        try:
            raw = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty
        if not isinstance(raw, dict):
            return empty
        version = raw.get("version")
        if version == 3:
            return self._load_state_v3(raw, empty)
        if version in (1, 2):
            # Safe migration: the pre-v3 state anchored everything on the
            # guild war window; move it under guild_war and prefix the shared
            # offset keys so no existing plan is lost.
            migrated = self._empty_state()
            offsets = raw.get("offsets")
            if isinstance(offsets, dict):
                for key, value in offsets.items():
                    key = str(key)
                    head, _, tail = key.partition(":")
                    if head not in MODES:
                        key = f"guild_war:{key}"
                        tail = key.partition(":")[2]
                    if tail.startswith("ROUTINE@"):
                        try:
                            migrated["offsets"][key] = int(value)
                        except (TypeError, ValueError):
                            continue
            last_probe = raw.get("last_probe")
            if isinstance(last_probe, (int, float)):
                migrated["last_probe"] = float(last_probe)
            if version == 2:
                guild = migrated["modes"]["guild_war"]
                guild["window"] = self._load_window(raw.get("window"))
                raw_slots = raw.get("pending_slots")
                if isinstance(raw_slots, list):
                    # v2 slots predate the per-mode scheduler: they all belong
                    # to the guild war planner.
                    migrated_slots = [
                        dict(item, mode="guild_war")
                        if isinstance(item, dict) and "mode" not in item else item
                        for item in raw_slots]
                    guild["pending_slots"] = self._load_pending_slots(migrated_slots)
                if guild["window"] is None:
                    guild["pending_slots"] = []
                closed_out = raw.get("closed_out")
                if isinstance(closed_out, list):
                    guild["closed_out"] = [
                        list(item) for item in closed_out
                        if isinstance(item, (list, tuple)) and len(item) == 3]
            return migrated
        return empty

    def _load_state_v3(self, raw, empty):
        state = self._empty_state()
        offsets = raw.get("offsets")
        if isinstance(offsets, dict):
            for key, value in offsets.items():
                key = str(key)
                head, _, tail = key.partition(":")
                if head in MODES and tail.startswith("ROUTINE@"):
                    try:
                        state["offsets"][key] = int(value)
                    except (TypeError, ValueError):
                        continue
        last_probe = raw.get("last_probe")
        if isinstance(last_probe, (int, float)):
            state["last_probe"] = float(last_probe)
        modes = raw.get("modes")
        if isinstance(modes, dict):
            for mode in MODES:
                entry = modes.get(mode)
                if not isinstance(entry, dict):
                    continue
                mode_state = state["modes"][mode]
                mode_state["window"] = self._load_window(entry.get("window"))
                mode_state["pending_slots"] = [
                    slot for slot in self._load_pending_slots(entry.get("pending_slots"))
                    if slot.mode == mode]
                if mode_state["window"] is None:
                    mode_state["pending_slots"] = []
                closed_out = entry.get("closed_out")
                if isinstance(closed_out, list):
                    mode_state["closed_out"] = [
                        list(item) for item in closed_out
                        if isinstance(item, (list, tuple)) and len(item) == 3]
        return state

    @staticmethod
    def _load_window(raw):
        if not isinstance(raw, dict):
            return None
        season = raw.get("season_id")
        phase = raw.get("phase")
        start, end = raw.get("start"), raw.get("end")
        if (not isinstance(season, str) or not season
                or (phase is not None and type(phase) is not int)
                or (start is not None and not isinstance(start, (int, float)))
                or not isinstance(end, (int, float)) or end <= 0):
            return None
        return (season, phase,
                None if start is None else float(start), float(end))

    @staticmethod
    def _load_pending_slots(raw):
        slots = []
        if not isinstance(raw, list):
            return slots
        for item in raw:
            if not isinstance(item, dict):
                continue
            mode = item.get("mode")
            key, trigger, kind = item.get("key"), item.get("trigger"), item.get("kind")
            fire_at = item.get("fire_at")
            if (mode not in MODES or not isinstance(key, str)
                    or not isinstance(trigger, str)
                    or kind not in ("routine", "pre_end", "post_close")
                    or not isinstance(fire_at, (int, float))):
                continue
            slots.append(Slot(mode, key, trigger, kind, float(fire_at)))
        return slots

    def _merged_offsets(self) -> dict:
        merged = {}
        for mode in MODES:
            merged.update(self._planners[mode].offsets())
        return merged

    def _persist_offsets(self, offsets) -> None:
        merged = self._merged_offsets()
        merged.update(offsets)
        self._write_state(offsets=merged)

    def _persist_last_probe(self) -> None:
        self._write_state(offsets=self._merged_offsets())

    def _write_state(self, *, offsets) -> None:
        path = self._state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            modes = {}
            for mode in MODES:
                window = self._planners[mode].window
                modes[mode] = {
                    "window": None if window is None else {
                        "season_id": window[0], "phase": window[1],
                        "start": window[2], "end": window[3]},
                    "pending_slots": [
                        {"mode": slot.mode, "key": slot.key, "trigger": slot.trigger,
                         "kind": slot.kind, "fire_at": slot.fire_at}
                        for slot in self._planners[mode].pending_slots()],
                    "closed_out": self._closed_out[mode],
                }
            payload = json.dumps({
                "version": _STATE_VERSION,
                "offsets": {key: int(value) for key, value in offsets.items()},
                "last_probe": self._last_probe,
                "modes": modes,
            }, sort_keys=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        except OSError:
            _logger.warning("leaderboard schedule state persist failed")

    # -- main loop -----------------------------------------------------------

    def _next_due(self, now: float) -> Slot | None:
        due = []
        for mode in MODES:
            slot = self._planners[mode].next_due(now)
            if slot is not None:
                due.append(slot)
        if not due:
            return None
        return min(due, key=lambda slot: (_slot_priority(slot), slot.fire_at))

    def _next_fire_delay(self, now: float) -> float | None:
        delays = [delay for mode in MODES
                  if (delay := self._planners[mode].next_fire_delay(now)) is not None]
        return min(delays) if delays else None

    async def run(self, stop_event) -> None:
        while not stop_event.is_set():
            now = self._now()
            slot = self._next_due(now)
            if slot is None:
                delay = self._next_fire_delay(now)
                probe_in = 0.0 if self._last_probe is None else max(
                    0.0, self._last_probe + self._config.metadata_probe_interval - now)
                delay = probe_in if delay is None else min(delay, probe_in)
                if delay is None:
                    delay = 1.0
                stopped = await self._wait(delay, stop_event)
                if stopped:
                    break
                woke = self._now()
                if (self._last_probe is None
                        or woke - self._last_probe >= self._config.metadata_probe_interval):
                    await self._probe(woke)
                continue
            await self._fire(slot, stop_event)
            planner = self._planners[slot.mode]
            if planner.window is not None and not planner.has_pending():
                planner.clear_window()
                self._write_state(offsets=self._merged_offsets())

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
            results = await asyncio.to_thread(self._probe_all_metadata, web)
        except GachaError as error:
            _logger.warning("leaderboard metadata probe failed: %s", error.code)
            return
        except Exception:  # noqa: BLE001 - never persist raw transport text
            _logger.warning("leaderboard metadata probe failed: PROBE_FAILED")
            return
        for mode in MODES:
            self._handle_probe_result(mode, results.get(mode) or {}, now)
        self._write_state(offsets=self._merged_offsets())

    def _handle_probe_result(self, mode: str, result: dict, now: float) -> None:
        planner = self._planners[mode]
        availability = result.get("availability")
        season = result.get("season_id")
        metadata = result.get("metadata", {})
        start, end = metadata.get("startStamp"), metadata.get("endStamp")
        start_value = float(start) if isinstance(start, int) and start > 0 else None
        ended = isinstance(end, int) and end > 0
        if season and availability in ("ACTIVE", "SETTLING", "SETTLED_CANDIDATE") \
                and ended and end > now:
            planned = planner.observe_window(
                mode=mode, season_id=season, phase=metadata.get("phase"),
                start=start_value, end=float(end), now=now,
                persist=self._persist_offsets)
            for slot in planned:
                self._plan_history_slot(slot)
        elif (season and availability in ("SETTLED_CANDIDATE", "CLOSED") and ended
                and float(end) <= now
                and float(end) + POST_CLOSE_DELAY_SECONDS
                > now - POST_CLOSE_TOLERANCE_SECONDS
                and [str(season), metadata.get("phase"), float(end)]
                not in self._closed_out[mode]):
            # The window already ended (e.g. a restart between end and the
            # catch-up): schedule only the post-close slot at end + 3600s.
            planned = planner.observe_post_close_recovery(
                mode=mode, season_id=season, phase=metadata.get("phase"),
                start=start_value, end=float(end))
            for slot in planned:
                self._plan_history_slot(slot)
        elif availability not in ("CLOSED", "NOT_OPEN", "SETTLED_CANDIDATE"):
            _logger.info("leaderboard %s metadata probe state=%s",
                         mode, availability or "UNKNOWN")

    def _probe_all_metadata(self, web):
        bodies: list[bytes] = []
        session = self._open_session(web, METADATA_MAX_REQUESTS, METADATA_MAX_SECONDS, bodies)
        with session as active:
            return {
                mode: collect_mode(active, mode, scope=self._config.scope,
                                   metadata_only=True, now=self._now)
                for mode in MODES
            }

    def _probe_mode_metadata(self, mode: str, web):
        bodies: list[bytes] = []
        session = self._open_session(web, METADATA_MAX_REQUESTS, METADATA_MAX_SECONDS, bodies)
        with session as active:
            return collect_mode(active, mode, scope=self._config.scope,
                                metadata_only=True, now=self._now)

    def _plan_history_slot(self, slot: Slot) -> None:
        window = self._planners[slot.mode].window
        if window is None:
            return
        self._open_triggers.add((slot.mode, slot.trigger))
        try:
            self._history.plan_schedule_slot(
                self._config.scope, slot.mode, window[0], slot.trigger, _iso(slot.fire_at))
        except (ValueError, KeyError):
            _logger.warning("leaderboard schedule slot plan failed: %s", slot.trigger)

    def _settle_history_slot(self, mode: str, trigger: str, status: str, run_id=None) -> None:
        window = self._planners[mode].window
        if window is None:
            return
        try:
            self._history.fire_schedule_slot(
                self._config.scope, mode, window[0], trigger,
                run_id=run_id, status=status)
        except (ValueError, KeyError):
            _logger.warning("leaderboard schedule slot settle failed: %s", trigger)

    def _settle_slot(self, slot: Slot, status: str, run_id=None) -> None:
        self._planners[slot.mode].settle(slot)
        self._open_triggers.discard((slot.mode, slot.trigger))
        self._settle_history_slot(slot.mode, slot.trigger, status, run_id=run_id)
        self._write_state(offsets=self._merged_offsets())

    # -- slot firing -----------------------------------------------------------

    async def _fire(self, slot: Slot, stop_event) -> None:
        planner = self._planners[slot.mode]
        now = self._now()
        if planner.is_stale(slot, now):
            self._settle_slot(slot, "SKIPPED")
            return
        if planner.conflicts_with_pre_end(slot, now):
            self._settle_slot(slot, "SKIPPED")
            return
        if slot.kind == "post_close":
            await self._fire_post_close(slot, stop_event)
            return
        components = ("ranking",) if slot.kind == "pre_end" \
            else ROUTINE_COMPONENTS[slot.mode]
        try:
            run_ids, failure = await self._run_pipeline(slot, components, stop_event)
        except CredentialError:
            self._settle_slot(slot, "UNAVAILABLE")
            return
        status = "FIRED" if failure is None else "UNAVAILABLE"
        self._settle_slot(slot, status, run_id=run_ids.get(slot.mode))

    async def _post_close_was_extended(self, slot: Slot) -> bool:
        """Verify the season really ended before the catch-up purges anything.

        Returns True when the metadata shows the same season with an end still
        in the future (extension or still-active): the window is replanned by
        the new end and the catch-up is postponed instead of fired or sealed.
        """
        planner = self._planners[slot.mode]
        window = planner.window
        if window is None:
            return False
        try:
            web = await self._provider.credentials()
            result = await asyncio.to_thread(self._probe_mode_metadata, slot.mode, web)
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
        start_value = float(start) if isinstance(start, int) and start > 0 else None
        now = self._now()
        if (season and str(season) == window[0]
                and isinstance(end, int) and end > 0 and float(end) > now):
            planned = planner.observe_window(
                mode=slot.mode, season_id=season, phase=metadata.get("phase"),
                start=start_value, end=float(end), now=now,
                persist=self._persist_offsets)
            for new_slot in planned:
                self._plan_history_slot(new_slot)
            replanned = {(slot.mode, new_slot.trigger) for new_slot in planned}
            stale = {item for item in self._open_triggers
                     if item[0] == slot.mode} - replanned
            for mode, trigger in sorted(stale):
                self._settle_history_slot(mode, trigger, "SKIPPED")
            self._open_triggers -= stale
            self._write_state(offsets=self._merged_offsets())
            _logger.info("leaderboard season extended: %s post-close postponed", slot.mode)
            return True
        return False

    async def _fire_post_close(self, slot: Slot, stop_event) -> None:
        if await self._post_close_was_extended(slot):
            return
        planner = self._planners[slot.mode]
        window = planner.window
        rejected = False
        try:
            _run_ids, failure = await self._run_pipeline(
                slot, ROUTINE_COMPONENTS[slot.mode], stop_event)
            rejected = failure == "RPC_REJECTED"
        except CredentialError:
            rejected = False
        if rejected:
            self._settle_history_slot(slot.mode, "POST_CLOSE_UNAVAILABLE", "UNAVAILABLE")
        self._settle_slot(slot, "UNAVAILABLE" if rejected else "FIRED")
        await self._seal_season(slot, stop_event)
        planner.clear_window()
        if window is not None:
            self._closed_out[slot.mode].append([window[0], window[1], window[3]])
        # The catch-up pipeline just validated the season metadata; treat it as
        # a fresh probe so the closed loop does not immediately re-login.
        self._last_probe = self._now()
        self._persist_last_probe()

    async def _seal_season(self, slot: Slot, stop_event) -> None:
        if self._packager is None:
            return
        window = self._planners[slot.mode].window
        if window is None:
            return
        mode, season = slot.mode, window[0]
        try:
            state = await asyncio.to_thread(
                self._history.last_run_state, self._config.scope, mode, season)
        except (ValueError, KeyError):
            state = None
        if state is None or stop_event.is_set():
            return
        try:
            entry = await asyncio.to_thread(
                self._packager.seal_season, self._config.scope, mode, season)
        except Exception as exc:  # noqa: BLE001 - fixed code only
            _logger.warning("leaderboard season seal failed: %s", type(exc).__name__)
        else:
            _logger.info("leaderboard season sealed: %s %s %s",
                         mode, season, entry["package"])

    # -- collection pipeline ----------------------------------------------------

    async def _run_pipeline(self, slot: Slot, components, stop_event):
        web = await self._provider.credentials()
        mode = slot.mode
        run_id, result, error = await self._collect_ranking(mode, web, slot.trigger)
        failure = error
        if run_id is None:
            result = None
        if (failure is None and not stop_event.is_set()
                and "lineup" in components):
            failure = await self._collect_lineups(web, slot.trigger, result, stop_event)
        if (failure is None and not stop_event.is_set()
                and "guild_index" in components):
            failure = await self._collect_guild_index(
                web, slot.trigger, result, stop_event)
        if stop_event.is_set() and failure is None:
            failure = INTERRUPT_STOPPED
        return ({mode: run_id} if run_id is not None else {}), failure

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
