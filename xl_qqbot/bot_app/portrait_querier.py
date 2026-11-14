"""角色立绘查询：读当前版本 portraits/manifest.json，按角色 ordinal 定位 PNG。

只依赖 stdlib：版本指针与 manifest 解析、SHA-256、PNG 头（IHDR）尺寸校验。
超过 QQ 20MiB 硬限（MAX_PORTRAIT_IMAGE_BYTES）的图片按 unavailable 处理，
绝不交给 Gateway；尺寸 stat 先行，超限即拒，不把大文件读进内存。
状态语义（与 server portrait manifest schema 1 对应）：
- published：正常发图；source_version 与当前版本不一致时按旧图提示；
- stale：随版本发布的旧图，可发但必须提示来源旧版本/本轮未更新；
- not_generated / failed / not_applicable：只提示，不触发渲染，不做历史回填；
- 越界 ordinal：返回该角色可用序号范围。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from . import history

_logger = logging.getLogger(__name__)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MANIFEST_SCHEMA_VERSION = 1
# QQ 图片硬限（20MiB）：超限立绘绝不交给 Gateway，直接按 unavailable 处理。
MAX_PORTRAIT_IMAGE_BYTES = 20 * 1024 * 1024
STATES_WITH_IMAGE = ("published", "stale")
NOTICE_STATES = ("not_generated", "failed", "not_applicable")


@dataclass(frozen=True)
class PortraitQueryResult:
    status: str  # "found" | "not_generated" | "failed" | "not_applicable" | "out_of_range" | "not_found" | "unavailable"
    character_id: str = ""
    skin_id: str = ""
    name: str = ""  # 皮肤名（manifest entry 的 name）
    ordinal: int | None = None
    image_path: Path | None = None
    source_version: str = ""
    stale: bool = False
    width: int = 0
    height: int = 0
    available_range: tuple[int, int] | None = None
    error: str = ""
    render_scope: str = "full"
    render_notice: str = ""


class PortraitQuerier:
    """复用 [watch] character_data/versions_dir 指向的 updater 数据根，不新增映射。"""

    def __init__(self, character_data: str | Path, versions_dir: str | Path):
        self._character_data = Path(character_data)
        self._versions_dir = Path(versions_dir)
        self._data_dir = self._character_data.parent.parent
        self._cache_key: tuple | None = None
        self._skins_by_character: dict[str, list[dict]] = {}

    # ---------- 数据加载 ----------

    def _load(self) -> dict[str, list[dict]] | None:
        """当前版本 manifest 按角色分组的皮肤表；任何数据问题返回 None（unavailable）。"""
        pointer_path = self._data_dir / "current_version.json"
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            active_version = str(pointer["version"])
            manifest_path = (
                self._versions_dir / active_version / "portraits" / "manifest.json"
            )
            key = (
                active_version,
                pointer_path.stat().st_mtime,
                manifest_path.stat().st_mtime,
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            _logger.warning("stage=portrait_query status=unavailable reason=pointer:%s", error)
            self._cache_key = None
            self._skins_by_character = {}
            return None
        if key == self._cache_key:
            return self._skins_by_character
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if int(payload.get("schema_version")) != MANIFEST_SCHEMA_VERSION:
                raise ValueError(f"unsupported schema_version: {payload.get('schema_version')!r}")
            skins = payload.get("skins")
            if not isinstance(skins, list):
                raise ValueError("skins 不是列表")
            grouped: dict[str, list[dict]] = {}
            for skin in skins:
                if not isinstance(skin, dict):
                    continue
                character_id = str(skin.get("character_id") or "")
                if character_id:
                    grouped.setdefault(character_id, []).append(skin)
        except (OSError, ValueError, KeyError, TypeError) as error:
            _logger.warning("stage=portrait_query status=unavailable reason=manifest:%s", error)
            self._cache_key = None
            self._skins_by_character = {}
            return None
        self._cache_key = key
        self._skins_by_character = grouped
        return grouped

    # ---------- 查询 ----------

    def find(self, character_id: str, ordinal: int | None = None) -> PortraitQueryResult:
        grouped = self._load()
        if grouped is None:
            return PortraitQueryResult(status="unavailable")
        skins = grouped.get(str(character_id))
        if not skins:
            return PortraitQueryResult(status="not_found", character_id=str(character_id))
        ordinals = sorted(self._safe_ordinal(skin) for skin in skins)
        available = (ordinals[0], ordinals[-1])
        wanted = self._safe_ordinal({"ordinal": ordinal}) if ordinal is not None else 1
        if wanted < available[0] or wanted > available[1]:
            return PortraitQueryResult(
                status="out_of_range",
                character_id=str(character_id),
                ordinal=wanted,
                available_range=available,
            )
        entry = next(
            (skin for skin in skins if self._safe_ordinal(skin) == wanted), None
        )
        if entry is None:
            return PortraitQueryResult(
                status="not_found",
                character_id=str(character_id),
                ordinal=wanted,
                available_range=available,
            )
        return self._entry_result(str(character_id), entry, available)

    def _entry_result(self, character_id: str, entry: dict,
                      available: tuple[int, int]) -> PortraitQueryResult:
        state = str(entry.get("state") or "")
        base = dict(
            character_id=character_id,
            skin_id=str(entry.get("skin_id") or ""),
            name=str(entry.get("name") or ""),
            ordinal=self._safe_ordinal(entry),
            available_range=available,
            error=str(entry.get("error") or ""),
            render_scope=str(entry.get("render_scope") or "full"),
            render_notice=str(entry.get("render_notice") or ""),
        )
        if state in NOTICE_STATES:
            return PortraitQueryResult(status=state, **base)
        if state not in STATES_WITH_IMAGE:
            _logger.warning("stage=portrait_query status=unknown_state state=%s skin=%s",
                            state, base["skin_id"])
            return PortraitQueryResult(status="unavailable", **base)
        image, image_error = self._validated_image(entry)
        if image is None:
            if image_error:
                base["error"] = image_error
            return PortraitQueryResult(status="unavailable", **base)
        source_version = str(entry.get("source_version") or "")
        active_version = str(self._cache_key[0]) if self._cache_key else ""
        return PortraitQueryResult(
            status="found",
            image_path=image,
            source_version=source_version,
            stale=bool(state == "stale" or (source_version and source_version != active_version)),
            width=self._safe_int(entry.get("width")),
            height=self._safe_int(entry.get("height")),
            **base,
        )

    # ---------- 内容历史（先把当前 ordinal 固定到 skin_id，再跨版本追踪同一 skin_id） ----------

    def find_history(self, character_id: str, ordinal: int | None = None,
                     offset: int = 0) -> history.ContentHistoryResult:
        """第 offset 个不同立绘内容（0=当前有效内容），按固定 skin_id 跨版本聚合。"""
        try:
            offset = max(int(offset), 0)
        except (TypeError, ValueError):
            offset = 0
        target, error_result = self._history_target(character_id, ordinal)
        if error_result is not None:
            return error_result
        cells = self._skin_cells(target["skin_id"])
        runs = history.fold_revision_runs(cells)
        base = self._history_base(target)
        total = len(runs)
        if total == 0:
            return history.ContentHistoryResult(status="not_found", offset=offset, total=0, **base)
        if offset >= total:
            return history.ContentHistoryResult(status="out_of_range", offset=offset, total=total, **base)
        current_version = str(self._cache_key[0]) if self._cache_key else ""
        return self._history_result(runs[offset], offset, total, current_version, base)

    def find_all_versions(self, character_id: str,
                          ordinal: int | None = None) -> list[history.ContentHistoryResult]:
        """该序号立绘的当前及全部内容版本，新到旧。"""
        target, error_result = self._history_target(character_id, ordinal)
        if error_result is not None:
            return [error_result]
        cells = self._skin_cells(target["skin_id"])
        runs = history.fold_revision_runs(cells)
        base = self._history_base(target)
        if not runs:
            return [history.ContentHistoryResult(status="not_found", total=0, **base)]
        current_version = str(self._cache_key[0]) if self._cache_key else ""
        return [
            self._history_result(run, index, len(runs), current_version, base)
            for index, run in enumerate(runs)
        ]

    def _history_target(self, character_id, ordinal):
        """解析当前版本 manifest：ordinal → skin_id。返回 (target, 错误结果)。"""
        grouped = self._load()
        if grouped is None:
            return None, history.ContentHistoryResult(status="unavailable", ordinal=ordinal)
        cid = str(character_id)
        skins = grouped.get(cid)
        if not skins:
            return None, history.ContentHistoryResult(
                status="not_found", character_id=cid, ordinal=ordinal)
        ordinals = sorted(self._safe_ordinal(skin) for skin in skins)
        available = (ordinals[0], ordinals[-1])
        wanted = self._safe_ordinal({"ordinal": ordinal}) if ordinal is not None else 1
        if wanted < available[0] or wanted > available[1]:
            return None, history.ContentHistoryResult(
                status="out_of_range", character_id=cid, ordinal=wanted,
                available_range=available,
            )
        entry = next((skin for skin in skins if self._safe_ordinal(skin) == wanted), None)
        if entry is None:
            return None, history.ContentHistoryResult(
                status="not_found", character_id=cid, ordinal=wanted,
                available_range=available,
            )
        return {
            "character_id": cid,
            "wanted": wanted,
            "available": available,
            "entry": entry,
            "skin_id": str(entry.get("skin_id") or ""),
        }, None

    @staticmethod
    def _history_base(target) -> dict:
        entry = target["entry"]
        return {
            "character_id": target["character_id"],
            "name": str(entry.get("name") or ""),
            "skin_id": target["skin_id"],
            "ordinal": target["wanted"],
            "available_range": target["available"],
        }

    def _skin_cells(self, skin_id: str):
        """逐版本格子 [(version, key, payload)]；无索引的未回填版本不参与聚合。"""
        cells = []
        for version in history.numeric_versions(self._versions_dir):
            cell = self._skin_cell(skin_id, version)
            if cell is not None:
                cells.append(cell)
        return cells

    @staticmethod
    def _invalid_skin_cell(version: str, error: str):
        """占住该版本位的不可用修订（唯一 key 不折叠），payload 携带错误。"""
        return (version, ("invalid", version), {"error": error})

    def _skin_cell(self, skin_id: str, version: str):
        """schema 必须 1；损坏 manifest 是 unavailable，完全未回填的版本跳过。"""
        manifest_path = self._versions_dir / version / "portraits" / "manifest.json"
        if not manifest_path.is_file():
            _logger.info("stage=portrait_history status=not_backfilled version=%s", version)
            return None
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("manifest 不是对象")
            if int(payload.get("schema_version")) != 1:
                raise ValueError(f"不支持的 schema_version: {payload.get('schema_version')!r}")
            skins = payload.get("skins")
            if not isinstance(skins, list):
                raise TypeError("skins 不是列表")
        except (OSError, ValueError, TypeError) as error:
            _logger.warning(
                "stage=portrait_history status=invalid_manifest version=%s reason=%s",
                version, error,
            )
            return self._invalid_skin_cell(version, f"portraits manifest 损坏或不可读: {error}")
        for entry in skins:
            if not isinstance(entry, dict) or str(entry.get("skin_id")) != skin_id:
                continue
            if str(entry.get("state") or "") not in STATES_WITH_IMAGE:
                return (version, None, None)
            artifact_sha = str(entry.get("artifact_sha256") or "")
            if not artifact_sha:
                return self._invalid_skin_cell(version, "artifact_sha256 为空，无法校验历史图片")
            artifact_path = str(entry.get("artifact_path") or "")
            if not artifact_path:
                return self._invalid_skin_cell(version, "artifact_path 为空")
            pixel_sha, pixel_error = self._pixel_sha256(version, artifact_path)
            if pixel_error:
                return self._invalid_skin_cell(version, pixel_error)
            return (version, pixel_sha, {"entry": entry, "version": version})
        return (version, None, None)

    def _pixel_sha256(self, version: str, artifact_path: str) -> tuple[str, str]:
        """containment 预检后读取并解码历史立绘，返回 (像素 SHA, 错误)。"""
        rel = Path(str(artifact_path or ""))
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            return "", "unsafe artifact path"
        base = (self._versions_dir / version / "portraits").resolve()
        target = (base / rel).resolve()
        if not target.is_relative_to(base):
            return "", "artifact path escapes portraits directory"
        try:
            size = target.stat().st_size
            if size > history.MAX_HISTORY_IMAGE_BYTES:
                return "", f"history image {size} bytes exceeds QQ image limit {history.MAX_HISTORY_IMAGE_BYTES}"
            data = target.read_bytes()
        except OSError as error:
            return "", f"历史图片不可读: {error}"
        try:
            return history.png_pixel_sha256(data), ""
        except ValueError as error:
            return "", f"像素哈希失败: {error}"

    def _history_result(self, run, offset, total, current_version, base):
        payload = run["payload"]
        common = dict(
            **base,
            first_seen_version=run["first_seen_version"],
            last_seen_version=run["last_seen_version"],
            offset=offset,
            total=total,
            stale=run["last_seen_version"] != current_version,
        )
        if payload.get("error"):
            return history.ContentHistoryResult(
                status="unavailable", error=str(payload["error"]), **common
            )
        pixel_sha256 = run["key"] if isinstance(run["key"], str) else ""
        entry = payload["entry"]
        artifact_sha = str(entry.get("artifact_sha256") or "")
        width = self._safe_int(entry.get("width"))
        height = self._safe_int(entry.get("height"))
        image, error = history.validate_image(
            self._versions_dir / payload["version"] / "portraits",
            str(entry.get("artifact_path") or ""),
            artifact_sha,
            width,
            height,
        )
        common.update(
            artifact_sha256=artifact_sha,
            source_version=str(entry.get("source_version") or run["first_seen_version"]),
            render_scope=str(entry.get("render_scope") or "full"),
            render_notice=str(entry.get("render_notice") or ""),
            pixel_sha256=pixel_sha256,
            width=width,
            height=height,
        )
        if image is None:
            return history.ContentHistoryResult(status="unavailable", error=error, **common)
        return history.ContentHistoryResult(status="found", image_path=image, **common)

    # ---------- 校验 ----------

    def _validated_image(self, entry: dict) -> tuple[Path | None, str]:
        """路径防逃逸 + 文件存在 + 20MiB 上限 + SHA-256 + PNG 头 + IHDR 尺寸交叉校验。

        返回 (image, "")；任何校验失败返回 (None, 可行动错误原因)。
        尺寸用 stat 先行，超限即拒，不把大文件读进内存。
        """
        try:
            relative = Path(str(entry.get("artifact_path") or ""))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError("unsafe artifact path")
            root = (self._versions_dir / str(self._cache_key[0]) / "portraits").resolve()
            image = (root / relative).resolve()
            if not image.is_relative_to(root) or not image.is_file():
                raise ValueError("artifact missing or outside portraits directory")
            size = image.stat().st_size
            if size > MAX_PORTRAIT_IMAGE_BYTES:
                raise ValueError(
                    f"portrait image {size} bytes exceeds QQ image limit"
                    f" {MAX_PORTRAIT_IMAGE_BYTES} bytes (not sendable)"
                )
            data = image.read_bytes()
            expected_sha = str(entry.get("artifact_sha256") or "")
            if expected_sha and hashlib.sha256(data).hexdigest() != expected_sha:
                raise ValueError("artifact digest mismatch")
            width, height = self._png_size(data)
            if width is None:
                raise ValueError("artifact is not PNG")
            if (self._safe_int(entry.get("width")) not in (0, width)
                    or self._safe_int(entry.get("height")) not in (0, height)):
                raise ValueError("artifact size mismatch")
        except (OSError, ValueError, TypeError) as error:
            _logger.warning("stage=portrait_query status=invalid_artifact skin=%s reason=%s",
                            entry.get("skin_id"), error)
            return None, str(error)
        return image, ""

    @staticmethod
    def _png_size(data: bytes) -> tuple[int | None, int | None]:
        """stdlib PNG 头解析：签名 + IHDR 宽高（不引入 Pillow）。"""
        if len(data) < 24 or not data.startswith(PNG_SIGNATURE):
            return None, None
        if data[12:16] != b"IHDR":
            return None, None
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        if width <= 0 or height <= 0:
            return None, None
        return width, height

    @staticmethod
    def _safe_ordinal(skin: dict) -> int:
        try:
            return int(skin.get("ordinal"))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _safe_int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
