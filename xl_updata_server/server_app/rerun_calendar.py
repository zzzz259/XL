"""Persist version observations against a fixed Beijing resource-cycle calendar."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .gacha_history import _classify, _int, rebuild_gacha_history

SHANGHAI = ZoneInfo("Asia/Shanghai")
CYCLE_DAYS = 21
DEFAULT_CYCLE_ANCHOR = date(2026, 11, 20)


def cycle_for_date(day: date, anchor: date) -> tuple[date, date]:
    """Return the fixed half-open resource interval containing a natural date."""
    if isinstance(day, datetime) or isinstance(anchor, datetime):
        raise TypeError("cycle dates must be natural dates, not datetimes")
    if anchor.weekday() != 4:
        raise ValueError("cycle anchor must be a Friday")
    start = anchor + timedelta(days=((day - anchor).days // CYCLE_DAYS) * CYCLE_DAYS)
    return start, start + timedelta(days=CYCLE_DAYS)


def pool_window(cycle_start: date) -> tuple[datetime, datetime]:
    """A Friday resource cycle supplies the following Tuesday's pool window."""
    if isinstance(cycle_start, datetime):
        raise TypeError("cycle_start must be a natural date, not a datetime")
    if cycle_start.weekday() != 4:
        raise ValueError("cycle_start must be a Friday natural date")
    tuesday = cycle_start + timedelta(days=4)
    return (datetime.combine(tuesday, time(10), SHANGHAI),
            datetime.combine(tuesday + timedelta(days=CYCLE_DAYS), time(5), SHANGHAI))


def _valid_cycle(value: str, anchor: date) -> date:
    cycle = date.fromisoformat(value)
    if cycle_for_date(cycle, anchor)[0] != cycle:
        raise ValueError("cycle_start does not belong to the anchored resource calendar")
    return cycle


def _positive_id(value: Any) -> int | None:
    identifier = _int(value)
    return identifier if not isinstance(value, bool) and identifier is not None and identifier > 0 else None


def _pool_reason(pool: dict, bottomups: dict, override: dict | None = None) -> str | None:
    if _positive_id(pool.get("id")) is None:
        return "invalid_gacha_id"
    kind, reason = _classify(pool, bottomups)
    if override and override.get("pool_kind"):
        kind, reason = override["pool_kind"], None
        # An explicit unknown classification remains lifecycle audit evidence.
        # The lifecycle excludes it from both the normal queue and known pools.
        if kind == "unknown":
            return None
    if kind not in {"normal", "special"}:
        return reason or "unsupported_pool_type"
    ids = [override["character_id"]] if override and override.get("character_id") is not None else pool.get("main_card_ids")
    if not isinstance(ids, (list, tuple)) or not ids:
        return "missing_main_card_ids"
    if any(_positive_id(item) is None for item in ids):
        return "invalid_main_character_id"
    if kind == "normal" and len(set(map(int, ids))) != 1:
        return "multiple_normal_headliners"
    return None


def merge_calendar(previous: dict, pools: list[dict], bottomups: dict, names: dict, *,
                   source_version: str, observed_at: datetime, historical_import: bool = False) -> dict:
    """Merge observations while preserving established identities and provenance.

    Historical imports can only use explicit version mappings or archived IDs.
    Invalid records are diagnosed independently so healthy records still advance.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    version = str(source_version).strip()
    if not version:
        raise ValueError("source_version must be non-empty")
    observed = observed_at.astimezone(SHANGHAI)
    result = deepcopy(previous)
    anchor = date.fromisoformat(result.get("cycle_anchor_date", DEFAULT_CYCLE_ANCHOR.isoformat()))
    cycle_for_date(observed.date(), anchor)
    result.update(schema_version=1, cycle_anchor_date=anchor.isoformat(), source_version=version)
    result.setdefault("versions", {})
    result.setdefault("pools", {})
    result.setdefault("diagnostics", [])
    result["archived_pool_ids"] = list(dict.fromkeys(str(item) for item in result.get("archived_pool_ids", [])))
    result["names"] = {str(key): value for key, value in result.get("names", {}).items()}
    result["names"].update({str(key): value for key, value in names.items()})
    result["bottomups"] = {str(key): value for key, value in result.get("bottomups", {}).items()}

    def diagnose(reason: str, gacha_id: Any = None, **details: Any) -> None:
        entry = {"reason": reason, "source_version": version, **details}
        if gacha_id is not None:
            entry["gacha_id"] = gacha_id
        if entry not in result["diagnostics"]:
            result["diagnostics"].append(entry)

    # Keep a previously reliable classification reference from silently changing
    # the meaning of every pool that refers to it.
    for key, value in bottomups.items():
        key = str(key)
        old = result["bottomups"].get(key)
        if (isinstance(old, dict) and old.get("bottom_type") in (201, 301, "201", "301")
                and (not isinstance(value, dict)
                     or _int(old.get("bottom_type")) != _int(value.get("bottom_type")))):
            diagnose("bottom_up_semantic_conflict", bottom_up=key)
        else:
            result["bottomups"][key] = deepcopy(value)

    versions = result["versions"]
    if version not in versions and not historical_import:
        versions[version] = {"first_seen_at": observed.isoformat(),
                             "cycle_start": cycle_for_date(observed.date(), anchor)[0].isoformat()}
    mapped_cycle = versions.get(version, {}).get("cycle_start")
    if mapped_cycle:
        _valid_cycle(mapped_cycle, anchor)
    overrides = {str(item.get("gacha_id")): item for item in result.get("overrides", [])}
    for raw in pools:
        identifier = _positive_id(raw.get("id"))
        if identifier is None:
            diagnose("invalid_gacha_id", str(raw.get("id", "")))
            continue
        key = str(identifier)
        old = result["pools"].get(key)
        candidate = deepcopy(raw)
        if old:
            candidate = {**old, **candidate}
            semantic_keys = ("type", "bottom_main_type", "bottom_up", "main_card_ids")
            if any(raw.get(field, old.get(field)) != old.get(field) for field in semantic_keys):
                diagnose("pool_semantic_conflict", identifier, correction_action="retained_original_semantics")
                for field in semantic_keys:
                    if field in old:
                        candidate[field] = deepcopy(old[field])
                    else:
                        candidate.pop(field, None)
        reason = _pool_reason(candidate, result["bottomups"], overrides.get(key))
        if reason:
            # Non-character pools are outside this calendar, not malformed data.
            if _int(candidate.get("type")) == 2:
                diagnose(reason, identifier)
            continue
        if old:
            for field in ("first_seen_version", "cycle_start", "start_at", "end_at", "date_source", "source_reference"):
                if field in old:
                    candidate[field] = deepcopy(old[field])
                else:
                    candidate.pop(field, None)
        else:
            candidate["first_seen_version"] = version
            if key not in result["archived_pool_ids"]:
                if not mapped_cycle:
                    diagnose("historical_cycle_unknown", identifier, correction_action="skipped_unmapped_import")
                    continue
                start, end = pool_window(_valid_cycle(mapped_cycle, anchor))
                candidate.update(cycle_start=mapped_cycle, start_at=start.isoformat(), end_at=end.isoformat(),
                                 date_source="version_cycle", source_reference=version)
        candidate.update(id=identifier, gacha_id=identifier)
        result["pools"][key] = candidate
    return apply_two_track_schedule(result)


def apply_two_track_schedule(calendar: dict) -> dict:
    """Assign first and later reruns to independent 21-day sequence tracks."""
    pools = calendar.get("pools", {})
    if not isinstance(pools, dict) or not pools:
        return calendar
    anchor = date.fromisoformat(calendar.get("cycle_anchor_date", DEFAULT_CYCLE_ANCHOR.isoformat()))
    archived = {str(item) for item in calendar.get("archived_pool_ids", [])}
    bottomups = calendar.get("bottomups", {})
    names = calendar.get("names", {})
    overrides = list(calendar.get("overrides", []))
    override_by_id = {str(item.get("gacha_id")): item for item in overrides}
    history = rebuild_gacha_history(list(pools.values()), bottomups, names, overrides=overrides)
    events = history["events"]
    event_by_id = {str(event.get("gacha_id")): event for event in events}

    def dated_cycle(pool: dict) -> date | None:
        key = str(pool.get("id"))
        if key in archived or _pool_reason(pool, bottomups, override_by_id.get(key)):
            return None
        try:
            return _valid_cycle(pool["cycle_start"], anchor)
        except (KeyError, TypeError, ValueError):
            return None

    candidates: dict[date, dict[str, list[int]]] = {}
    for event in events:
        kind = event.get("event_kind")
        if kind not in {"first_rerun", "later_rerun"}:
            continue
        identifier = _positive_id(event.get("gacha_id"))
        pool = pools.get(str(identifier))
        if identifier is None or not isinstance(pool, dict):
            continue
        cycle = dated_cycle(pool)
        if cycle is not None:
            candidates.setdefault(cycle, {"first_rerun": [], "later_rerun": []})[kind].append(identifier)

    def diagnose(reason: str, **details) -> dict:
        result = deepcopy(calendar)
        result.setdefault("diagnostics", [])
        entry = {"reason": reason, "source_version": calendar.get("source_version", ""),
                 "correction_action": "retained_previous_calendar", **details}
        for index, existing in enumerate(result["diagnostics"]):
            if existing.get("reason") == reason:
                result["diagnostics"][index] = entry
                break
        else:
            result["diagnostics"].append(entry)
        return result

    model = calendar.get("track_schedule")
    if model is None:
        anchor_candidates = [(cycle, entries) for cycle, entries in candidates.items()
                             if entries["first_rerun"] and entries["later_rerun"]]
        source_cycle = calendar.get("versions", {}).get(
            str(calendar.get("source_version", "")), {}).get("cycle_start")
        pending_reason = next((item.get("reason") for item in calendar.get("diagnostics", [])
                               if item.get("reason") in {"two_track_anchor_ambiguous", "two_track_anchor_not_found"}), None)
        if pending_reason:
            return diagnose(pending_reason, source_cycle=source_cycle,
                            candidate_cycles=[cycle.isoformat() for cycle, _ in anchor_candidates])
        current_candidates = [item for item in anchor_candidates if item[0].isoformat() == source_cycle]
        if current_candidates:
            anchor_cycle, entries = current_candidates[0]
        elif len(anchor_candidates) == 1:
            anchor_cycle, entries = anchor_candidates[0]
        elif anchor_candidates:
            return diagnose("two_track_anchor_ambiguous", source_cycle=source_cycle,
                            candidate_cycles=[cycle.isoformat() for cycle, _ in anchor_candidates])
        elif (any(entries["first_rerun"] for entries in candidates.values())
                and any(entries["later_rerun"] for entries in candidates.values())):
            return diagnose("two_track_anchor_not_found", source_cycle=source_cycle, candidate_cycles=[])
        else:
            return calendar
        first_anchor = min(entries["first_rerun"])
        later_anchor = min(entries["later_rerun"])
        model = {
            "mode": "first_and_later_rerun_tracks",
            "anchor_cycle_start": anchor_cycle.isoformat(),
            "first_anchor_gacha_id": first_anchor,
            "later_anchor_gacha_id": later_anchor,
        }
    else:
        try:
            if not isinstance(model, dict) or model.get("mode") != "first_and_later_rerun_tracks":
                return diagnose("two_track_model_invalid")
            anchor_cycle = _valid_cycle(model["anchor_cycle_start"], anchor)
            first_anchor = _positive_id(model["first_anchor_gacha_id"])
            later_anchor = _positive_id(model["later_anchor_gacha_id"])
        except (KeyError, TypeError, ValueError):
            return diagnose("two_track_model_invalid")
        if (first_anchor is None or later_anchor is None
                or event_by_id.get(str(first_anchor), {}).get("event_kind") != "first_rerun"
                or event_by_id.get(str(later_anchor), {}).get("event_kind") != "later_rerun"):
            return diagnose("two_track_anchor_unavailable",
                            first_anchor_gacha_id=first_anchor, later_anchor_gacha_id=later_anchor)

    tracks = {"first_rerun": [], "later_rerun": []}
    for event in events:
        kind = event.get("event_kind")
        if kind not in tracks:
            continue
        identifier = _positive_id(event.get("gacha_id"))
        anchor_id = first_anchor if kind == "first_rerun" else later_anchor
        pool = pools.get(str(identifier))
        if (identifier is None or identifier < anchor_id or not isinstance(pool, dict)
                or dated_cycle(pool) is None):
            continue
        tracks[kind].append((identifier, pool))
    for values in tracks.values():
        values.sort(key=lambda item: item[0])
    if (not tracks["first_rerun"] or not tracks["later_rerun"]
            or tracks["first_rerun"][0][0] != first_anchor
            or tracks["later_rerun"][0][0] != later_anchor):
        return diagnose("two_track_anchor_unavailable",
                        first_anchor_gacha_id=first_anchor, later_anchor_gacha_id=later_anchor)

    result = deepcopy(calendar)
    result["track_schedule"] = {
        "mode": "first_and_later_rerun_tracks",
        "anchor_cycle_start": anchor_cycle.isoformat(),
        "first_anchor_gacha_id": first_anchor,
        "later_anchor_gacha_id": later_anchor,
    }
    source_reference = f"two_track_anchor:{first_anchor}+{later_anchor}@{anchor_cycle.isoformat()}"
    for kind, values in tracks.items():
        for index, (identifier, _pool) in enumerate(values):
            cycle = anchor_cycle + timedelta(days=CYCLE_DAYS * index)
            start, end = pool_window(cycle)
            result["pools"][str(identifier)].update(
                cycle_start=cycle.isoformat(), start_at=start.isoformat(), end_at=end.isoformat(),
                date_source="two_track_sequence", source_reference=source_reference,
                schedule_track=kind, track_index=index + 1,
            )
    return result if result != calendar else calendar
