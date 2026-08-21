"""音频最终产物的增量快照与未读状态。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


STATE_FILENAME = ".audio_state.json"


def state_path(output_dir: str | os.PathLike[str]) -> Path:
    return Path(output_dir) / STATE_FILENAME


def _load(output_dir: str | os.PathLike[str]) -> dict:
    path = state_path(output_dir)
    if not path.is_file():
        return {"files": {}}
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {"files": {}}
    except (OSError, ValueError, TypeError):
        return {"files": {}}


def _save(output_dir: str | os.PathLike[str], data: dict) -> None:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".audio-state-", suffix=".json", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, state_path(root))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def sync_audio_snapshot(output_dir: str | os.PathLike[str], audio_files: list[dict]) -> dict:
    """同步当前 output/audio 文件，并返回各文件的未读状态。"""
    previous = _load(output_dir).get("files", {})
    current = {}
    for info in audio_files:
        name = str(info.get("name", "")).replace("\\", "/")
        path = Path(info.get("path", ""))
        if not name or not path.is_file():
            continue
        stat = path.stat()
        signature = f"{stat.st_size}:{stat.st_mtime_ns}"
        old = previous.get(name, {}) if isinstance(previous, dict) else {}
        if not isinstance(old, dict):
            old = {}
        current[name] = {
            "signature": signature,
            "unread": bool(old.get("unread", True)) if old.get("signature") == signature else True,
        }
        info["unread"] = current[name]["unread"]

    data = {"files": current}
    _save(output_dir, data)
    return data


def unread_files(output_dir: str | os.PathLike[str]) -> set[str]:
    files = _load(output_dir).get("files", {})
    return {name for name, value in files.items() if isinstance(value, dict) and value.get("unread")}


def mark_read(output_dir: str | os.PathLike[str], relative_name: str) -> bool:
    data = _load(output_dir)
    key = str(relative_name).replace("\\", "/")
    value = data.get("files", {}).get(key)
    if not isinstance(value, dict) or not value.get("unread"):
        return False
    value["unread"] = False
    _save(output_dir, data)
    return True


def mark_all_read(output_dir: str | os.PathLike[str]) -> bool:
    data = _load(output_dir)
    changed = False
    for value in data.get("files", {}).values():
        if isinstance(value, dict) and value.get("unread"):
            value["unread"] = False
            changed = True
    if changed:
        _save(output_dir, data)
    return changed
