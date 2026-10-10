"""Time-aware projection and durable publication for the rerun schedule."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)
TIMEZONE = ZoneInfo("Asia/Shanghai")
CYCLE_DAYS = 21
DEFAULT_ANCHOR = {
    "new_character_id": 10000223,
    "new_character_name": "罗蕾娜",
    "new_gacha_id": 24000086,
    "rerun_character_id": 10000212,
    "rerun_character_name": "雪莉",
    "rerun_gacha_id": 24000087,
    "start_at": "2026-09-22T10:00:00+08:00",
    "end_at": "2026-10-13T05:00:00+08:00",
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
    as_of: datetime | None = None,
    anchor: dict[str, Any] | None = None,
    names: dict[Any, str] | None = None,
    classic_pool_dates: list[dict[str, str]] | None = None,
    forecast_limit: int = 20,
) -> dict[str, Any]:
    """Project predicted periods without confusing a configured pool with confirmation."""
    selected_anchor = dict(anchor or DEFAULT_ANCHOR)
    start = _parse_aware(selected_anchor["start_at"], "anchor.start_at")
    end = _parse_aware(selected_anchor["end_at"], "anchor.end_at")
    now = _parse_aware(as_of or datetime.now(TIMEZONE), "as_of")
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
    delta = now - start
    cycle_index = max(0, int(delta.total_seconds() // (CYCLE_DAYS * 24 * 60 * 60)))
    candidate_start = start + timedelta(days=CYCLE_DAYS * cycle_index)
    candidate_end = end + timedelta(days=CYCLE_DAYS * cycle_index)
    next_cycle_start = candidate_start + timedelta(days=CYCLE_DAYS)

    if now < start:
        period_state = "before_anchor"
        current_mode = "confirmed"
        current_badge = "待开始"
        current_name_new = _name(names, selected_anchor["new_character_id"], selected_anchor["new_character_name"])
        current_name_rerun = _name(names, selected_anchor["rerun_character_id"], selected_anchor["rerun_character_name"])
        current_start, current_end = start, end
        next_index = 1
    elif cycle_index == 0 and start <= now < end:
        period_state = "confirmed_active"
        current_mode = "confirmed"
        current_badge = "当前已确认"
        current_name_new = _name(names, selected_anchor["new_character_id"], selected_anchor["new_character_name"])
        current_name_rerun = _name(names, selected_anchor["rerun_character_id"], selected_anchor["rerun_character_name"])
        current_start, current_end = start, end
        next_index = 1
    elif candidate_end <= now < next_cycle_start:
        period_state = "between_periods"
        current_mode = "confirmed" if cycle_index == 0 else "predicted"
        current_badge = "上期已结束" if cycle_index == 0 else "上期预计已结束"
        if cycle_index == 0:
            current_name_new = _name(names, selected_anchor["new_character_id"], selected_anchor["new_character_name"])
            current_name_rerun = _name(names, selected_anchor["rerun_character_id"], selected_anchor["rerun_character_name"])
        elif cycle_index - 1 < len(queue):
            current_name_new = queue[cycle_index - 1]["name"]
            current_name_rerun = "普通复刻档期"
        else:
            current_name_new = "待确认"
            current_name_rerun = "待确认"
        current_start, current_end = candidate_start, candidate_end
        next_index = cycle_index + 1
    else:
        period_state = "predicted_period"
        current_mode = "predicted"
        current_queue_index = cycle_index - 1
        current_badge = "本期预计（非官方）"
        if current_queue_index < len(queue):
            current_item = queue[current_queue_index]
            current_name_new = current_item["name"]
            current_name_rerun = "普通复刻档期"
            current_start, current_end = candidate_start, candidate_end
        else:
            current_name_new = "待确认"
            current_name_rerun = "待确认"
            current_start, current_end = candidate_start, candidate_end
        next_index = cycle_index + 1

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
    next_forecast = next((item for item in forecasts if item["period_index"] == next_index), None)
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
        "generated_at": now.isoformat(),
        "render": render,
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


def refresh_schedule_if_due(
    schedule_dir: str | Path,
    *,
    as_of: datetime,
    refresh_seconds: int,
    node_bin: str = "node",
    renderer_script: str | Path | None = None,
    forecast_limit: int = 20,
) -> bool:
    """Refresh cached dates/status at time boundaries, reusing parsed game history."""
    if refresh_seconds <= 0:
        raise ValueError("refresh_seconds must be positive")
    existing, _image = read_current_schedule(schedule_dir)
    history = {
        "queue": existing.get("queue", []),
        "events": existing.get("events", []),
        "anomalies": existing.get("anomalies", []),
    }
    names = {str(item.get("character_id")): str(item.get("name", "")) for item in history["queue"]}
    anchor = existing.get("anchor") or DEFAULT_ANCHOR
    projection = project_schedule(
        history,
        source_version=existing["source_version"],
        as_of=as_of,
        anchor=anchor,
        names=names,
        forecast_limit=forecast_limit,
    )
    generated_at = _parse_aware(existing["generated_at"], "generated_at")
    now = _parse_aware(as_of, "as_of")
    time_due = (now - generated_at).total_seconds() >= refresh_seconds
    if (
        projection["period_state"] == existing.get("period_state")
        and projection["render"] == existing.get("render")
        and not time_due
    ):
        return False
    png = render_schedule_png(projection, node_bin=node_bin, renderer_script=renderer_script)
    publish_schedule_snapshot(schedule_dir, projection, png)
    LOGGER.info(
        "stage=rerun_project game_version=%s period_state=%s queue_length=%d status=success",
        projection["source_version"], projection["period_state"], len(projection["queue"]),
    )
    return True
