"""@机器人+角色名 的图鉴查询：匹配角色并定位已渲染长图。

find() 保留当前版本快速路径；find_history()/find_all_versions() 扫描版本目录做
只读内容历史聚合（连续相同折叠、缺失间断、A→B→A 成新节点，新到旧）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from . import history

_logger = logging.getLogger(__name__)

# card manifest（schema 1）中带有效图片条目的状态
CARD_STATES_WITH_IMAGE = ("published", "stale")


@dataclass(frozen=True)
class QueryResult:
    status: str  # "found" | "not_found" | "no_image"
    character_id: str = ""
    name: str = ""
    image_path: Path | None = None


class CharacterQuerier:
    def __init__(self, character_data: str | Path, versions_dir: str | Path):
        self._character_data = Path(character_data)
        self._versions_dir = Path(versions_dir)
        self._cache_mtime: float | None = None
        self._characters: dict = {}

    def _load(self) -> dict:
        try:
            mtime = self._character_data.stat().st_mtime
        except OSError:
            return {}
        if mtime != self._cache_mtime:
            try:
                payload = json.loads(self._character_data.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            self._characters = payload.get("characters", {})
            self._cache_mtime = mtime
        return self._characters

    def _match(self, query: str) -> tuple[str, dict] | None:
        characters = self._load()
        lowered = query.lower()
        for character_id, data in characters.items():
            name = str(data.get("name", ""))
            cn_name, _, en_name = name.partition("/")
            if query == cn_name or query == str(character_id):
                return str(character_id), data
            if en_name and lowered == en_name.lower():
                return str(character_id), data
        return None

    def _image_for(self, character_id: str) -> Path | None:
        """当前版本图鉴定位：card manifest 存在时优先其明确的角色→图片映射（带统一校验，
        修复改名后 glob 取旧图）；无 manifest 才回退唯一旧文件，多候选不猜第一张。"""
        pointer = self._character_data.parent.parent / "current_version.json"
        try:
            version = json.loads(pointer.read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError):
            return None
        version_dir = self._versions_dir / str(version)
        cards_dir = version_dir / "character_cards"
        manifest_path = cards_dir / "manifest.json"
        if manifest_path.is_file():
            return self._manifest_image(character_id, version_dir, manifest_path)
        return self._legacy_image(character_id, cards_dir)

    def _manifest_image(self, character_id: str, version_dir: Path, manifest_path: Path) -> Path | None:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if int(payload.get("schema_version")) != 1:
                return None
            entries = payload.get("characters")
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("character_id")) != character_id:
                continue
            if str(entry.get("state") or "") not in CARD_STATES_WITH_IMAGE:
                return None
            artifact_sha = str(entry.get("artifact_sha256") or "")
            if not artifact_sha:
                return None
            image, _ = history.validate_image(
                version_dir, str(entry.get("artifact_path") or ""), artifact_sha
            )
            return image

    def _legacy_image(self, character_id: str, cards_dir: Path) -> Path | None:
        if not cards_dir.is_dir():
            return None
        try:
            matches = sorted(p for p in cards_dir.glob(f"{character_id}_*") if p.is_file())
        except OSError:
            return None
        if len(matches) != 1:
            return None
        return matches[0]

    def find(self, query: str) -> QueryResult:
        query = query.strip().strip("\"'“”‘’").strip()
        if not query:
            return QueryResult(status="not_found")
        matched = self._match(query)
        if matched is None:
            return QueryResult(status="not_found")
        character_id, data = matched
        cn_name = str(data.get("name", "")).split("/", 1)[0]
        image = self._image_for(character_id)
        if image is None:
            return QueryResult(status="no_image", character_id=character_id, name=cn_name)
        return QueryResult(status="found", character_id=character_id, name=cn_name, image_path=image)

    # ---------- 内容历史（只读聚合，不触发下载/渲染） ----------

    def _history_pointer(self) -> tuple[str, str]:
        """返回 (当前版本, 错误)；current_version.json 缺失/损坏时错误非空（与立绘一致）。"""
        current = self._current_version()
        if not current:
            return "", "current_version.json 缺失或损坏"
        return current, ""

    def find_history(self, character_id: str, offset: int = 0) -> history.ContentHistoryResult:
        """第 offset 个不同内容（0=当前有效内容，1/2=前 1/2 个），始终新到旧口径。"""
        try:
            offset = max(int(offset), 0)
        except (TypeError, ValueError):
            offset = 0
        character_id = str(character_id)
        name = self._cn_name(character_id)
        current, pointer_error = self._history_pointer()
        if pointer_error:
            return history.ContentHistoryResult(
                status="unavailable", character_id=character_id, name=name,
                offset=offset, error=pointer_error,
            )
        cells = self._card_cells(character_id)
        if cells is None:
            return history.ContentHistoryResult(
                status="unavailable", character_id=character_id, name=name,
                offset=offset, error="versions 目录不可用",
            )
        runs = history.fold_revision_runs(cells)
        total = len(runs)
        if total == 0:
            return history.ContentHistoryResult(
                status="not_found", character_id=character_id, name=name,
                offset=offset, total=0,
            )
        if offset >= total:
            return history.ContentHistoryResult(
                status="out_of_range", character_id=character_id, name=name,
                offset=offset, total=total,
            )
        return self._history_result(
            character_id, name, runs[offset], offset, total, current
        )

    def find_all_versions(self, character_id: str) -> list[history.ContentHistoryResult]:
        """当前及全部历史，新到旧；数据不可用时返回单元素 unavailable 列表。"""
        character_id = str(character_id)
        name = self._cn_name(character_id)
        current, pointer_error = self._history_pointer()
        if pointer_error:
            return [history.ContentHistoryResult(
                status="unavailable", character_id=character_id, name=name,
                error=pointer_error,
            )]
        cells = self._card_cells(character_id)
        if cells is None:
            return [history.ContentHistoryResult(
                status="unavailable", character_id=character_id, name=name,
                error="versions 目录不可用",
            )]
        runs = history.fold_revision_runs(cells)
        if not runs:
            return [history.ContentHistoryResult(
                status="not_found", character_id=character_id, name=name, total=0,
            )]
        return [
            self._history_result(character_id, name, run, index, len(runs), current)
            for index, run in enumerate(runs)
        ]

    def _cn_name(self, character_id: str) -> str:
        data = self._load().get(character_id) or {}
        return str(data.get("name", "")).split("/", 1)[0]

    def _current_version(self) -> str:
        pointer = self._character_data.parent.parent / "current_version.json"
        try:
            return str(json.loads(pointer.read_text(encoding="utf-8"))["version"])
        except (OSError, ValueError, KeyError):
            return ""

    def _card_cells(self, character_id: str):
        """逐版本格子 [(version, key, payload)]（旧到新）；目录不可用返回 None。"""
        try:
            if not self._versions_dir.is_dir():
                return None
        except OSError as error:
            _logger.warning("stage=card_history status=unreadable_versions reason=%s", error)
            return None
        return [
            self._card_cell(character_id, version)
            for version in history.numeric_versions(self._versions_dir)
        ]

    @staticmethod
    def _invalid_cell(version: str, error: str):
        """占住该版本位的不可用修订（唯一 key 不折叠），payload 携带错误。"""
        return (version, ("invalid", version), {"error": error})

    def _card_cell(self, character_id: str, version: str):
        version_dir = self._versions_dir / version
        manifest_path = version_dir / "character_cards" / "manifest.json"
        if manifest_path.is_file():
            return self._manifest_card_cell(character_id, version, version_dir, manifest_path)
        return self._legacy_card_cell(character_id, version, version_dir)

    def _manifest_card_cell(self, character_id, version, version_dir, manifest_path):
        """损坏/不可读/不支持 schema 的 manifest 是 unavailable 修订，不是普通间断。"""
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("manifest 不是对象")
            if int(payload.get("schema_version")) != 1:
                raise ValueError(f"不支持的 schema_version: {payload.get('schema_version')!r}")
            entries = payload.get("characters")
            if not isinstance(entries, list):
                raise TypeError("characters 不是列表")
        except (OSError, ValueError, TypeError) as error:
            _logger.warning(
                "stage=card_history status=invalid_manifest version=%s reason=%s", version, error
            )
            return self._invalid_cell(version, f"card manifest 损坏或不可读: {error}")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("character_id")) != character_id:
                continue
            if str(entry.get("state") or "") not in CARD_STATES_WITH_IMAGE:
                return (version, None, None)
            artifact_sha = str(entry.get("artifact_sha256") or "")
            if not artifact_sha:
                return self._invalid_cell(version, "artifact_sha256 为空，无法校验历史图片")
            data_sha = str(entry.get("data_sha256") or "")
            if not data_sha:
                return self._invalid_cell(version, "data_sha256 为空，无法确定内容修订")
            artifact_path = str(entry.get("artifact_path") or "")
            if not artifact_path:
                return self._invalid_cell(version, "artifact_path 为空")
            return (version, (data_sha, artifact_sha), {"entry": entry, "version_dir": version_dir})
        return (version, None, None)

    def _legacy_card_cell(self, character_id, version, version_dir):
        """旧格式（无 card manifest）只在候选唯一时回退；多个改名文件不猜，占住该版本位报不可用。"""
        cards_dir = version_dir / "character_cards"
        try:
            matches = sorted(p for p in cards_dir.glob(f"{character_id}_*") if p.is_file())
        except OSError:
            matches = []
        if len(matches) == 1:
            try:
                digest = hashlib.sha256(matches[0].read_bytes()).hexdigest()
            except OSError:
                return (version, None, None)
            return (version, ("legacy", digest), {
                "legacy": matches[0],
                "legacy_sha256": digest,
                "version_dir": version_dir,
            })
        if len(matches) > 1:
            return (version, ("ambiguous", version), {
                "error": f"旧版目录有 {len(matches)} 个候选图鉴文件，无法确定唯一历史图片",
                "version_dir": version_dir,
            })
        return (version, None, None)

    def _history_result(self, character_id, name, run, offset, total, current_version):
        payload = run["payload"]
        base = {
            "character_id": character_id,
            "name": name,
            "first_seen_version": run["first_seen_version"],
            "last_seen_version": run["last_seen_version"],
            "offset": offset,
            "total": total,
            "stale": run["last_seen_version"] != current_version,
        }
        if payload.get("error"):
            return history.ContentHistoryResult(
                status="unavailable", error=str(payload["error"]), **base
            )
        if "legacy" in payload:
            image, error = history.validate_image(
                payload["version_dir"],
                f"character_cards/{payload['legacy'].name}",
                payload["legacy_sha256"],
            )
            artifact_sha = payload["legacy_sha256"]
            data_sha = ""
            source_version = run["first_seen_version"]
        else:
            entry = payload["entry"]
            artifact_sha = str(entry.get("artifact_sha256") or "")
            data_sha = str(entry.get("data_sha256") or "")
            source_version = str(entry.get("source_version") or run["first_seen_version"])
            image, error = history.validate_image(
                payload["version_dir"],
                str(entry.get("artifact_path") or ""),
                artifact_sha,
            )
        if image is None:
            return history.ContentHistoryResult(
                status="unavailable", error=error, data_sha256=data_sha,
                artifact_sha256=artifact_sha, source_version=source_version, **base,
            )
        return history.ContentHistoryResult(
            status="found", image_path=image, data_sha256=data_sha,
            artifact_sha256=artifact_sha, source_version=source_version, **base,
        )
