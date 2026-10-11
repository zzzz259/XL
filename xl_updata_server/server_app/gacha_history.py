"""Deterministically classify gacha history and rebuild the first-rerun FIFO."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pool_order(pool: Mapping[str, Any]) -> tuple[int, int, str]:
    """Use configured sort when numeric, then numeric Gacha ID as stable tie-break."""
    gacha_id = _int(pool.get("id"))
    configured_sort = _int(pool.get("sort"))
    return (
        configured_sort if configured_sort is not None else (gacha_id if gacha_id is not None else 2**63 - 1),
        gacha_id if gacha_id is not None else 2**63 - 1,
        str(pool.get("id", "")),
    )


def _bottom_type(bottomups: Mapping[Any, Any], key: Any) -> int | None:
    entry = bottomups.get(key)
    if entry is None:
        entry = bottomups.get(str(key))
    if entry is None and _int(key) is not None:
        entry = bottomups.get(_int(key))
    if not isinstance(entry, Mapping):
        return None
    return _int(entry.get("bottom_type"))


def _classify(pool: Mapping[str, Any], bottomups: Mapping[Any, Any]) -> tuple[str, str | None]:
    if _int(pool.get("type")) != 2:
        return "ignored", None
    bottom_main_type = _int(pool.get("bottom_main_type"))
    bottom_type = _bottom_type(bottomups, pool.get("bottom_up"))
    if bottom_main_type == 1 and bottom_type == 201:
        return "normal", None
    if bottom_main_type == 2 and bottom_type == 301:
        return "special", None
    if bottom_type is None:
        return "unknown", "missing_bottom_up_reference"
    return "unknown", "unrecognized_pool_type_combination"


def rebuild_gacha_history(
    pools: Sequence[Mapping[str, Any]],
    bottomups: Mapping[Any, Any],
    names: Mapping[Any, str] | None = None,
    overrides: Sequence[Mapping[str, Any]] | None = None,
    through_gacha_id: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build pool events, lifecycle events, queue, and deterministic anomalies.

    The queue contains normal single-headliner characters with a debut but no
    first normal rerun. Ordering is the configured numeric ``sort`` field when
    present, then numeric Gacha ID, with original string ID as a stable tie-break.
    """
    display_names = names or {}
    override_by_id: dict[str, Mapping[str, Any]] = {}
    for override in overrides or ():
        gacha_key = str(_int(override.get("gacha_id")) or "")
        source = str(override.get("source", "")).strip()
        pool_kind_override = override.get("pool_kind")
        character_id_override = override.get("character_id")
        if not gacha_key or not source:
            raise ValueError("每条卡池覆盖项必须有有效 gacha_id 和 source")
        if pool_kind_override is not None and pool_kind_override not in {"normal", "special", "unknown"}:
            raise ValueError(f"覆盖项 pool_kind 无效: {pool_kind_override!r}")
        if character_id_override is not None and _int(character_id_override) is None:
            raise ValueError("覆盖项 character_id 必须为数字")
        if gacha_key in override_by_id:
            raise ValueError(f"重复的卡池覆盖项: {gacha_key}")
        override_by_id[gacha_key] = override
    if through_gacha_id is not None and _int(through_gacha_id) is None:
        raise ValueError("through_gacha_id must be an integer")
    sorted_pools = sorted(pools, key=_pool_order)
    if through_gacha_id is not None:
        cutoff = int(through_gacha_id)
        sorted_pools = [
            pool for pool in sorted_pools
            if _int(pool.get("id")) is not None and int(pool["id"]) <= cutoff
        ]
    first_by_id: dict[str, Mapping[str, Any]] = {}
    ordered: list[Mapping[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    for pool in sorted_pools:
        if _int(pool.get("type")) != 2:
            continue
        pool_key = str(pool.get("id", ""))
        if pool_key and pool_key in first_by_id:
            previous = first_by_id[pool_key]
            if dict(previous) != dict(pool):
                anomalies.append({
                    "gacha_id": _int(pool.get("id")) or pool_key,
                    "reason": "duplicate_gacha_id_conflict",
                    "severity": "error",
                    "correction_action": "retained_first_entry",
                })
            continue
        if pool_key:
            first_by_id[pool_key] = pool
        ordered.append(pool)

    events: list[dict[str, Any]] = []
    lifecycle: dict[int, str] = {}
    queue: list[dict[str, Any]] = []

    def name_for(character_id: int) -> str:
        return str(display_names.get(character_id, display_names.get(str(character_id), str(character_id))))

    for pool in ordered:
        pool_kind, classification_reason = _classify(pool, bottomups)
        gacha_id = _int(pool.get("id"))
        if gacha_id is None:
            gacha_id = str(pool.get("id", ""))
        override = override_by_id.get(str(gacha_id))
        excluded = bool(override.get("exclude_from_prediction", False)) if override else False
        if override:
            pool_kind_override = override.get("pool_kind")
            if pool_kind_override:
                pool_kind = str(pool_kind_override)
                classification_reason = None
        entry: dict[str, Any] = {
            "gacha_id": gacha_id,
            "pool_kind": pool_kind,
            "event_kind": "ignored" if pool_kind == "ignored" else pool_kind,
        }
        if override:
            entry["manual_override_source"] = str(override["source"])
            if excluded:
                entry["prediction_excluded"] = True
            anomalies.append({
                "gacha_id": gacha_id,
                "reason": "manual_override_applied",
                "source": str(override["source"]),
                "severity": "info",
                "correction_action": "applied_id_scoped_override",
            })
            if excluded:
                anomalies.append({
                    "gacha_id": gacha_id,
                    "reason": "excluded_from_prediction",
                    "source": str(override["source"]),
                    "severity": "info",
                    "correction_action": "kept_out_of_forecast_queue",
                })
        if classification_reason:
            entry["reason"] = classification_reason
            anomalies.append({
                "gacha_id": gacha_id,
                "reason": classification_reason,
                "severity": "warning",
                "correction_action": "excluded_from_prediction",
            })
        events.append(entry)
        if pool_kind in {"ignored", "unknown"}:
            continue

        raw_main_ids = pool.get("main_card_ids")
        if override and override.get("character_id") is not None:
            raw_main_ids = [_int(override["character_id"])]
        if not isinstance(raw_main_ids, (list, tuple)) or not raw_main_ids:
            entry.update(event_kind="unknown", pool_kind="unknown", reason="missing_main_card_ids")
            anomalies.append({
                "gacha_id": gacha_id,
                "reason": "missing_main_card_ids",
                "severity": "warning",
                "correction_action": "excluded_from_prediction",
            })
            continue
        main_ids = [_int(value) for value in raw_main_ids]
        if any(value is None for value in main_ids):
            entry.update(event_kind="unknown", pool_kind="unknown", reason="invalid_main_character_id")
            anomalies.append({
                "gacha_id": gacha_id,
                "reason": "invalid_main_character_id",
                "severity": "warning",
                "correction_action": "excluded_from_prediction",
            })
            continue
        distinct_ids = list(dict.fromkeys(main_ids))
        if pool_kind == "special":
            entry["character_ids"] = distinct_ids
            entry["names"] = [name_for(value) for value in distinct_ids]
            entry["event_kind"] = "special"
            continue
        if len(distinct_ids) != 1:
            entry.update(event_kind="unknown", pool_kind="unknown", character_ids=distinct_ids)
            anomalies.append({
                "gacha_id": gacha_id,
                "reason": "multiple_normal_headliners",
                "severity": "warning",
                "correction_action": "excluded_from_prediction",
            })
            continue

        character_id = distinct_ids[0]
        entry.update(character_id=character_id, name=name_for(character_id))
        previous_state = lifecycle.get(character_id)
        if previous_state is None:
            entry["event_kind"] = "debut"
            lifecycle[character_id] = "debut"
            if not excluded:
                queue.append({
                    "character_id": character_id,
                    "name": name_for(character_id),
                    "debut_gacha_id": gacha_id,
                })
        elif previous_state == "debut":
            entry["event_kind"] = "first_rerun"
            lifecycle[character_id] = "first_rerun"
            expected = queue[0]["character_id"] if queue else None
            queued = next((index for index, item in enumerate(queue) if item["character_id"] == character_id), None)
            if queued is None:
                anomalies.append({
                    "gacha_id": gacha_id,
                    "expected_character_id": expected,
                    "actual_character_id": character_id,
                    "reason": "first_rerun_not_in_queue",
                    "severity": "error",
                    "correction_action": "kept_queue_unchanged",
                })
            else:
                if queued != 0:
                    anomalies.append({
                        "gacha_id": gacha_id,
                        "expected_character_id": expected,
                        "actual_character_id": character_id,
                        "reason": "out_of_order_first_rerun",
                        "severity": "warning",
                        "correction_action": "removed_actual_character",
                    })
                queue.pop(queued)
        else:
            entry["event_kind"] = "later_rerun"

    return {"events": events, "queue": queue, "anomalies": anomalies}
