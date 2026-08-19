"""角色数据版本仓库与增量状态，不依赖 Qt。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .file_utils import atomic_write_bytes
from .logger import logger


SCHEMA_VERSION = 1
REPOSITORY_FILENAME = "characters_repository.json"
SNAPSHOT_DIRNAME = "versions"


def repository_path(data_dir: str | os.PathLike[str]) -> str:
    return str(Path(data_dir) / REPOSITORY_FILENAME)


def _default_repository() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "current_version": None,
        "current_characters": {},
        "unread": {},
        "history": {},
    }


def load_repository(path: str | os.PathLike[str]) -> dict:
    """读取角色仓库；文件不存在或损坏时返回空仓库。"""
    file_path = Path(path)
    if not file_path.is_file():
        return _default_repository()
    try:
        with file_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return _default_repository()
        result = _default_repository()
        result.update(payload)
        if not isinstance(result.get("current_characters"), dict):
            result["current_characters"] = {}
        if not isinstance(result.get("unread"), dict):
            result["unread"] = {}
        if not isinstance(result.get("history"), dict):
            result["history"] = {}
        return result
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("读取角色数据仓库失败: %s", exc)
        return _default_repository()


def save_repository(path: str | os.PathLike[str], repository: Mapping) -> None:
    """原子保存角色仓库，避免解析中断留下半个 JSON。"""
    payload = json.dumps(repository, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    atomic_write_bytes(path, payload)


def _json_key(value) -> str:
    return str(value)


def _character_mapping(payload: Mapping | None) -> dict:
    if not isinstance(payload, Mapping):
        return {}
    return {_json_key(key): value for key, value in payload.items()}


def current_characters(repository: Mapping) -> dict:
    """返回当前角色数据，保留数字角色 ID 的兼容性。"""
    result = {}
    for key, value in _character_mapping(repository.get("current_characters")).items():
        try:
            result[int(key)] = value
        except ValueError:
            result[key] = value
    return result


def unread_status(repository: Mapping) -> dict[str, str]:
    return {
        _json_key(key): str(value)
        for key, value in _character_mapping(repository.get("unread")).items()
        if value in {"new", "changed"}
    }


def _fingerprint(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def compare_characters(previous: Mapping, current: Mapping) -> dict[str, str]:
    """比较两个完整角色快照，返回角色 ID 到 new/changed 的状态。"""
    old = _character_mapping(previous)
    new = _character_mapping(current)
    changes = {}
    for key, value in new.items():
        if key not in old:
            changes[key] = "new"
        elif _fingerprint(old[key]) != _fingerprint(value):
            changes[key] = "changed"
    return changes


def _snapshot_path(data_dir: str | os.PathLike[str], version_timestamp: int | str) -> Path:
    return Path(data_dir) / SNAPSHOT_DIRNAME / f"{int(version_timestamp)}.json"


def merge_snapshot(
    data_dir: str | os.PathLike[str],
    version_timestamp: int | str,
    characters_full: Mapping,
    source_dir: str | os.PathLike[str] | None = None,
    baseline_characters: Mapping | None = None,
) -> dict:
    """保存版本快照，并把新/变更角色合并到当前仓库。

    历史快照永不覆盖其他版本；当前数据只替换为本次解析结果，未读状态则累积保留。
    返回 ``repository``、``changes`` 和可供 UI 使用的 ``characters_full``。
    """
    timestamp = int(version_timestamp)
    path = repository_path(data_dir)
    repository = load_repository(path)
    previous = current_characters(repository)
    if not previous and baseline_characters:
        # 兼容升级前的 characters_full.json：第一次建立版本仓库时，
        # 先拿旧缓存作为基线，避免同一份数据首次迁移就整批显示“新”。
        previous = _character_mapping(baseline_characters)
    current = {key: value for key, value in characters_full.items()}
    changes = compare_characters(previous, current)
    unread = unread_status(repository)
    unread.update(changes)

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "version": timestamp,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": os.fspath(source_dir) if source_dir else "",
        "characters_full": current,
    }
    snapshot_file = _snapshot_path(data_dir, timestamp)
    save_repository(snapshot_file, snapshot)

    history = dict(repository.get("history") or {})
    history[str(timestamp)] = {
        "version": timestamp,
        "parsed_at": snapshot["parsed_at"],
        "source_dir": snapshot["source_dir"],
        "character_count": len(current),
        "new": sorted(key for key, status in changes.items() if status == "new"),
        "changed": sorted(key for key, status in changes.items() if status == "changed"),
        "snapshot": str(snapshot_file),
    }
    repository.update({
        "schema_version": SCHEMA_VERSION,
        "current_version": timestamp,
        "current_characters": current,
        "unread": unread,
        "history": history,
    })
    save_repository(path, repository)
    return {
        "repository": repository,
        "changes": changes,
        "unread": unread,
        "characters_full": current_characters(repository),
    }


def clear_unread(data_dir: str | os.PathLike[str], char_id) -> bool:
    """清除指定角色未读标记，返回是否确实清除了状态。"""
    path = repository_path(data_dir)
    repository = load_repository(path)
    unread = unread_status(repository)
    key = _json_key(char_id)
    if key not in unread:
        return False
    unread.pop(key, None)
    repository["unread"] = unread
    save_repository(path, repository)
    return True


def clear_all_unread(data_dir: str | os.PathLike[str]) -> int:
    """清除全部角色未读状态，返回清除的数量。"""
    path = repository_path(data_dir)
    repository = load_repository(path)
    unread = unread_status(repository)
    if not unread:
        return 0
    count = len(unread)
    repository["unread"] = {}
    save_repository(path, repository)
    return count


def unread_count(repository: Mapping) -> int:
    """返回仓库中当前未读角色数量。"""
    return len(unread_status(repository))
