"""Version-anchored projection and durable publication for the rerun schedule."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)
TIMEZONE = ZoneInfo("Asia/Shanghai")
CYCLE_DAYS = 21
DEFAULT_ANCHOR = {
    "new_character_id": 10000224,
    "new_character_name": "雾铃",
    "new_gacha_id": 24000088,
    "rerun_character_id": 10000214,
    "rerun_character_name": "朝雾",
    "rerun_gacha_id": 24000089,
    "start_at": "2026-10-13T10:00:00+08:00",
    "end_at": "2026-11-03T05:00:00+08:00",
    "source": "user_confirmed",
}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _parse_aware(value: str | datetime, name: str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(TIMEZONE)


def _name(names: dict[Any, str] | None, character_id: Any, fallback: str = "") -> str:
    if names:
        value = names.get(character_id, names.get(str(character_id)))
        if value:
            return str(value).split("/", 1)[0]
    return fallback or str(character_id)


def project_schedule(
    history: dict[str, list[dict[str, Any]]],
    *,
    source_version: str | int,
    anchor: dict[str, Any] | None = None,
    names: dict[Any, str] | None = None,
    classic_pool_dates: list[dict[str, str]] | None = None,
    forecast_limit: int = 20,
) -> dict[str, Any]:
    """Project one version snapshot from its confirmed anchor and first-rerun queue."""
    selected_anchor = dict(anchor or DEFAULT_ANCHOR)
    start = _parse_aware(selected_anchor["start_at"], "anchor.start_at")
    end = _parse_aware(selected_anchor["end_at"], "anchor.end_at")
    if end <= start:
        raise ValueError("anchor.end_at must be after anchor.start_at")
    if not str(source_version).strip():
        raise ValueError("source_version must be non-empty")
    if forecast_limit <= 0:
        raise ValueError("forecast_limit must be positive")

    queue = [dict(item) for item in history.get("queue", [])]
    for item in queue:
        item["character_id"] = int(item["character_id"])
        item["name"] = _name(names, item["character_id"], str(item.get("name", "")))
    current_new_id = int(selected_anchor["new_character_id"])
    current_rerun_id = int(selected_anchor["rerun_character_id"])
    queue = [
        item for item in queue
        if item["character_id"] not in {current_new_id, current_rerun_id}
    ]
    queue.append({
        "character_id": current_new_id,
        "name": _name(names, current_new_id, str(selected_anchor["new_character_name"])),
        "debut_gacha_id": int(selected_anchor["new_gacha_id"]),
    })
    period_state = "version_snapshot"
    current_mode = "confirmed"
    current_badge = "当前已确认"
    current_name_new = _name(names, selected_anchor["new_character_id"], selected_anchor["new_character_name"])
    current_name_rerun = _name(names, selected_anchor["rerun_character_id"], selected_anchor["rerun_character_name"])
    current_start, current_end = start, end

    def forecast_for(period_index: int, item: dict[str, Any]) -> dict[str, Any]:
        forecast_start = start + timedelta(days=CYCLE_DAYS * period_index)
        forecast_end = end + timedelta(days=CYCLE_DAYS * period_index)
        return {
            "period_index": period_index,
            "character_id": item["character_id"],
            "name": item["name"],
            "start_at": forecast_start.isoformat(),
            "end_at": forecast_end.isoformat(),
            "status": "predicted",
        }

    forecasts = [forecast_for(index + 1, item) for index, item in enumerate(queue)]
    next_forecast = forecasts[0] if forecasts else None
    if next_forecast is None:
        next_display = {"name": "暂无待预测角色", "startsAt": "待定", "endsAt": "待定"}
    else:
        next_start = datetime.fromisoformat(next_forecast["start_at"])
        next_end = datetime.fromisoformat(next_forecast["end_at"])
        next_display = {
            "name": next_forecast["name"],
            "startsAt": next_start.strftime("%m.%d %H:%M"),
            "endsAt": next_end.strftime("%m.%d %H:%M"),
        }

    render_rows = []
    for forecast in forecasts:
        when = datetime.fromisoformat(forecast["start_at"])
        render_rows.append({"name": forecast["name"], "date": when.strftime("%Y.%m.%d")})
    render = {
        "anchorDate": start.strftime("%Y.%m.%d"),
        "periodDays": CYCLE_DAYS,
        "current": {
            "mode": current_mode,
            "badge": current_badge,
            "newName": current_name_new,
            "rerunName": current_name_rerun,
            "startsAt": current_start.strftime("%Y.%m.%d %H:%M"),
            "endsAt": current_end.strftime("%Y.%m.%d %H:%M"),
        },
        "next": next_display,
        "firstReruns": render_rows[:forecast_limit],
    }
    if classic_pool_dates is not None:
        render["classicPoolDates"] = list(classic_pool_dates)
    return {
        "schema_version": 1,
        "source_version": str(source_version),
        "timezone": "Asia/Shanghai",
        "cycle_days": CYCLE_DAYS,
        "anchor": selected_anchor,
        "queue": queue,
        "forecasts": forecasts,
        "events": list(history.get("events", [])),
        "anomalies": list(history.get("anomalies", [])),
        "period_state": period_state,
        "generated_at": datetime.now(TIMEZONE).isoformat(),
        "render": render,
    }


def project_calendar(
    calendar: dict[str, Any],
    *,
    now: datetime,
    forecast_limit: int = 20,
) -> dict[str, Any]:
    """Project known periods and first normal reruns from one complete lifecycle.

    Wall time filters display entries only. It never fulfills a queued rerun or
    moves its predicted dates; only observed normal pool identities do that.
    """
    from .gacha_history import rebuild_gacha_history
    from .rerun_calendar import (
        DEFAULT_CYCLE_ANCHOR,
        _pool_reason,
        _valid_cycle,
        pool_window,
    )

    moment = _parse_aware(now, "now")
    if forecast_limit <= 0:
        raise ValueError("forecast_limit must be positive")
    version = str(calendar.get("source_version", "")).strip()
    if not version:
        raise ValueError("calendar.source_version must be non-empty")
    anchor = date.fromisoformat(calendar.get("cycle_anchor_date", DEFAULT_CYCLE_ANCHOR.isoformat()))
    bottomups = calendar.get("bottomups", {})
    names = calendar.get("names", {})
    archived = {str(identifier) for identifier in calendar.get("archived_pool_ids", [])}
    overrides = calendar.get("overrides", [])
    override_by_id = {str(item.get("gacha_id")): item for item in overrides}
    history_pools: list[dict[str, Any]] = []
    dated: dict[str, dict[str, Any]] = {}
    diagnostics = [dict(item) for item in calendar.get("diagnostics", [])]
    for pool in calendar.get("pools", {}).values():
        key = str(pool.get("id"))
        if key in archived:
            reason = _pool_reason(pool, bottomups, override_by_id.get(key))
            if reason:
                diagnostics.append({"gacha_id": pool.get("id"), "reason": "invalid_calendar_pool",
                                    "detail": reason, "correction_action": "excluded_from_projection"})
                continue
            history_pools.append(pool)
            continue
        try:
            cycle = _valid_cycle(pool["cycle_start"], anchor)
            start, end = pool_window(cycle)
            if not all(isinstance(pool.get(field), (str, datetime)) for field in ("start_at", "end_at")):
                raise ValueError("pool dates must be timezone-aware datetimes")
            if (_parse_aware(pool["start_at"], "pool.start_at") != start
                    or _parse_aware(pool["end_at"], "pool.end_at") != end):
                raise ValueError("pool dates disagree with its resource cycle")
            reason = _pool_reason(pool, bottomups, override_by_id.get(key))
            if reason:
                raise ValueError(reason)
        except (KeyError, ValueError, TypeError) as exc:
            diagnostics.append({"gacha_id": pool.get("id"), "reason": "invalid_calendar_pool",
                                "detail": str(exc), "correction_action": "excluded_from_projection"})
            continue
        history_pools.append(pool)
        dated[key] = {**pool, "start_at": start.isoformat(), "end_at": end.isoformat()}

    history = rebuild_gacha_history(history_pools, bottomups, names, overrides=overrides)
    events = history["events"]
    queue = [{**item, "name": _name(names, item["character_id"], item["name"])} for item in history["queue"]]
    groups: dict[str, dict[str, Any]] = {}
    normal_cycles: list[date] = []
    known: list[dict[str, Any]] = []
    labels = {"debut": "新角色", "first_rerun": "首复刻", "later_rerun": "再次复刻", "special": "特殊池"}
    for event in events:
        pool = dated.get(str(event["gacha_id"]))
        if pool is None or event["pool_kind"] not in {"normal", "special"}:
            continue
        cycle = date.fromisoformat(pool["cycle_start"])
        if event["pool_kind"] == "normal":
            normal_cycles.append(cycle)
        character_ids = event.get("character_ids", [event["character_id"]] if "character_id" in event else [])
        entry = {**event, "character_ids": character_ids,
                 "names": [_name(names, identifier) for identifier in character_ids],
                 "cycle_start": pool["cycle_start"], "start_at": pool["start_at"], "end_at": pool["end_at"],
                 "status": "known", "label": labels[event["event_kind"]]}
        entry["name"] = " / ".join(entry["names"])
        known.append(entry)
        if _parse_aware(pool["end_at"], "pool.end_at") <= moment:
            continue
        group = groups.setdefault(pool["cycle_start"], {
            "badge": "已知", "cycle_start": pool["cycle_start"],
            "startsAt": _parse_aware(pool["start_at"], "pool.start_at").strftime("%Y.%m.%d %H:%M"),
            "endsAt": _parse_aware(pool["end_at"], "pool.end_at").strftime("%Y.%m.%d %H:%M"), "pools": [],
        })
        group["pools"].append(entry)

    base = max(normal_cycles) if normal_cycles else None
    if base is None:
        reliable_cycles = []
        if calendar.get("last_reliable_cycle_start"):
            reliable_cycles.append(_valid_cycle(calendar["last_reliable_cycle_start"], anchor))
        pools_by_id = {str(pool.get("id")): pool for pool in history_pools}
        for event in events:
            key = str(event["gacha_id"])
            if key not in archived or event["pool_kind"] != "normal":
                continue
            pool = pools_by_id[key]
            mapping = calendar.get("versions", {}).get(str(pool.get("first_seen_version")), {})
            cycle_value = pool.get("cycle_start") or mapping.get("cycle_start")
            if cycle_value:
                try:
                    reliable_cycles.append(_valid_cycle(cycle_value, anchor))
                except (TypeError, ValueError) as exc:
                    diagnostics.append({"gacha_id": event["gacha_id"], "reason": "invalid_historical_cycle",
                                        "detail": str(exc)})
        base = max(reliable_cycles) if reliable_cycles else None
    forecasts = []
    if base is not None:
        for index, item in enumerate(queue, 1):
            cycle = base + timedelta(days=CYCLE_DAYS * index)
            start, end = pool_window(cycle)
            forecasts.append({**item, "period_index": index, "cycle_start": cycle.isoformat(),
                              "start_at": start.isoformat(), "end_at": end.isoformat(), "status": "predicted"})
    visible_forecasts = [item for item in forecasts if _parse_aware(item["end_at"], "forecast.end_at") > moment]
    next_item = visible_forecasts[0] if visible_forecasts else None
    render = {
        "known": [groups[key] for key in sorted(groups)],
        "anchorDate": pool_window(base)[0].strftime("%Y.%m.%d") if base else "待定",
        "periodDays": CYCLE_DAYS,
        "next": {"name": next_item["name"],
                 "startsAt": _parse_aware(next_item["start_at"], "forecast.start_at").strftime("%m.%d %H:%M"),
                 "endsAt": _parse_aware(next_item["end_at"], "forecast.end_at").strftime("%m.%d %H:%M")}
                if next_item else {"name": "暂无待预测角色", "startsAt": "待定", "endsAt": "待定"},
        "firstReruns": [{"name": item["name"], "date": _parse_aware(item["start_at"], "forecast.start_at").strftime("%Y.%m.%d")}
                        for item in visible_forecasts[:forecast_limit]],
    }
    return {
        "schema_version": 2, "source_version": version, "timezone": "Asia/Shanghai", "cycle_days": CYCLE_DAYS,
        "cycle_anchor_date": anchor.isoformat(), "forecast_base_cycle_start": base.isoformat() if base else None,
        "queue": queue, "forecasts": forecasts, "known": known, "events": events,
        "history": history, "anomalies": history["anomalies"], "diagnostics": diagnostics,
        "period_state": "calendar_snapshot", "generated_at": moment.isoformat(), "render": render,
    }


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(content)
    temporary.replace(path)


def publish_schedule_snapshot(
    schedule_dir: str | Path,
    payload: dict[str, Any],
    png_bytes: bytes,
) -> dict[str, Any]:
    """Publish immutable matching JSON/PNG first, then atomically move the current pointer."""
    if not png_bytes.startswith(PNG_SIGNATURE):
        raise ValueError("排期渲染结果不是有效 PNG")
    source_version = str(payload.get("source_version", "")).strip()
    if not source_version:
        raise ValueError("排期 payload 缺少 source_version")
    safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_version)
    png_hash = hashlib.sha256(png_bytes).hexdigest()
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    snapshot_id = f"{safe_version}-{hashlib.sha256(canonical + png_bytes).hexdigest()[:16]}"
    published = {
        **payload,
        "snapshot_id": snapshot_id,
        "render_sha256": png_hash,
        "artifact_path": f"snapshots/{snapshot_id}/current.png",
    }
    root = Path(schedule_dir)
    snapshot_dir = root / "snapshots" / snapshot_id
    snapshot_dir.parent.mkdir(parents=True, exist_ok=True)
    json_bytes = json.dumps(published, ensure_ascii=False, indent=2).encode("utf-8")
    image_path = snapshot_dir / "current.png"
    snapshot_json = snapshot_dir / "current.json"
    if snapshot_dir.exists():
        if not snapshot_json.is_file() or not image_path.is_file():
            raise FileExistsError(f"不完整的排期快照已存在: {snapshot_id}")
        if snapshot_json.read_bytes() != json_bytes or image_path.read_bytes() != png_bytes:
            raise FileExistsError(f"不可变排期快照内容冲突: {snapshot_id}")
    else:
        temporary_dir = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}.", dir=snapshot_dir.parent))
        try:
            (temporary_dir / "current.png").write_bytes(png_bytes)
            (temporary_dir / "current.json").write_bytes(json_bytes)
            temporary_dir.replace(snapshot_dir)
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise

    # The versioned artifact is authoritative. Root current.png is a convenience
    # mirror; current.json is replaced last and points to the immutable image.
    _write_atomic(root / "current.png", png_bytes)
    _write_atomic(root / "current.json", json_bytes)
    LOGGER.info(
        "stage=rerun_publish game_version=%s snapshot=%s status=success queue_length=%d anomaly_count=%d",
        source_version,
        snapshot_id,
        len(published.get("queue", [])),
        len(published.get("anomalies", [])),
    )
    return published


def read_current_schedule(schedule_dir: str | Path) -> tuple[dict[str, Any], Path]:
    """Read the current manifest and its immutable PNG, verifying containment/hash."""
    root = Path(schedule_dir).resolve()
    payload = json.loads((root / "current.json").read_text(encoding="utf-8"))
    relative = Path(str(payload["artifact_path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("排期图片路径不安全")
    image = (root / relative).resolve()
    if not image.is_relative_to(root) or not image.is_file():
        raise ValueError("排期图片不存在或越界")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    if digest != payload.get("render_sha256"):
        raise ValueError("排期 JSON 与 PNG 摘要不匹配")
    return payload, image


def render_schedule_png(
    payload: dict[str, Any],
    *,
    node_bin: str = "node",
    renderer_script: str | Path | None = None,
) -> bytes:
    """Render an already-projected schedule via the existing Node canvas renderer."""
    script = Path(renderer_script) if renderer_script else Path(__file__).resolve().parent.parent / "renderer" / "rerun_schedule_renderer.js"
    if not script.is_file():
        raise FileNotFoundError(f"排期渲染脚本不存在: {script}")
    with tempfile.TemporaryDirectory(prefix="rerun-render-") as directory:
        input_path = Path(directory) / "schedule.json"
        output_path = Path(directory) / "schedule.png"
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        completed = subprocess.run(
            [node_bin, str(script), str(input_path), str(output_path)],
            cwd=script.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if completed.returncode != 0 or not output_path.is_file():
            raise RuntimeError(
                "排期 PNG 渲染失败: "
                f"exit={completed.returncode} stderr={(completed.stderr or '').strip()[:1000]}"
            )
        return output_path.read_bytes()
