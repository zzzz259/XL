"""角色立绘查询：读当前版本 portraits/manifest.json，按角色 ordinal 定位 PNG。

只依赖 stdlib：版本指针与 manifest 解析、SHA-256、PNG 头（IHDR）尺寸校验。
状态语义（与 server portrait manifest schema 1 对应）：
- published：正常发图；source_version 与当前版本不一致时按旧图提示；
- stale：随版本发布的旧图，可发但必须提示来源旧版本/本轮未更新；
- not_generated / failed：只提示，不触发渲染，不做历史回填；
- 越界 ordinal：返回该角色可用序号范围。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

_logger = logging.getLogger(__name__)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MANIFEST_SCHEMA_VERSION = 1
STATES_WITH_IMAGE = ("published", "stale")
NOTICE_STATES = ("not_generated", "failed")


@dataclass(frozen=True)
class PortraitQueryResult:
    status: str  # "found" | "not_generated" | "failed" | "out_of_range" | "not_found" | "unavailable"
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
        )
        if state in NOTICE_STATES:
            return PortraitQueryResult(status=state, **base)
        if state not in STATES_WITH_IMAGE:
            _logger.warning("stage=portrait_query status=unknown_state state=%s skin=%s",
                            state, base["skin_id"])
            return PortraitQueryResult(status="unavailable", **base)
        image = self._validated_image(entry)
        if image is None:
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

    # ---------- 校验 ----------

    def _validated_image(self, entry: dict) -> Path | None:
        """路径防逃逸 + 文件存在 + SHA-256 + PNG 签名 + IHDR 尺寸交叉校验。"""
        try:
            relative = Path(str(entry.get("artifact_path") or ""))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError("unsafe artifact path")
            root = (self._versions_dir / str(self._cache_key[0]) / "portraits").resolve()
            image = (root / relative).resolve()
            if not image.is_relative_to(root) or not image.is_file():
                raise ValueError("artifact missing or outside portraits directory")
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
            return None
        return image

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
