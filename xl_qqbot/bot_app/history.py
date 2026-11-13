"""内容历史共享：版本枚举、内容修订折叠、历史图片统一校验。

图鉴与立绘的历史聚合共用这里的纯函数，不新增持久历史数据库：
- 修订只折叠连续相同内容键；缺失/不适用造成的间断显式断开，不跨合并。
- 历史图鉴与立绘统一执行路径 containment、文件存在、QQ 20MiB 上限（stat 先行，
  超限即拒，不把大文件读进内存）、SHA-256、PNG 签名/IHDR 与可选 manifest 尺寸交叉校验。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

_logger = logging.getLogger(__name__)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# QQ 图片硬限（20MiB）：超限历史图片绝不交给 Gateway，直接按 unavailable 处理。
MAX_HISTORY_IMAGE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ContentHistoryResult:
    """单条内容历史查询结果；status: found | not_found | out_of_range | unavailable。"""

    status: str
    character_id: str = ""
    name: str = ""
    image_path: Path | None = None
    data_sha256: str = ""
    artifact_sha256: str = ""
    source_version: str = ""
    first_seen_version: str = ""
    last_seen_version: str = ""
    offset: int = 0
    total: int = 0
    stale: bool = False
    error: str = ""
    # 立绘专属字段（图鉴历史留默认值）
    skin_id: str = ""
    ordinal: int | None = None
    available_range: tuple[int, int] | None = None
    render_scope: str = "full"
    render_notice: str = ""
    width: int = 0
    height: int = 0


def numeric_versions(versions_dir: str | Path) -> list[str]:
    """versions 目录下的纯数字版本名（旧到新）。

    排除 .pending-*、工作目录和非数字目录；目录不可用时返回空列表。
    单条 stat 失败（OSError）跳过该条目并记日志，不拖垮整个查询。
    """
    try:
        entries = list(Path(versions_dir).iterdir())
    except OSError as error:
        _logger.warning("stage=history_versions status=unreadable reason=%s", error)
        return []
    names = []
    for entry in entries:
        try:
            if entry.is_dir() and entry.name.isdigit():
                names.append(entry.name)
        except OSError as error:
            _logger.warning(
                "stage=history_versions status=skip_entry path=%s reason=%s", entry, error
            )
    return sorted(names, key=int)


def png_ihdr_size(data: bytes) -> tuple[int | None, int | None]:
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


def validate_image(
    root: str | Path,
    relative: str,
    expected_sha256: str = "",
    expected_width: int = 0,
    expected_height: int = 0,
) -> tuple[Path | None, str]:
    """历史图片统一校验，返回 (image, "")；失败返回 (None, 可行动错误)。"""
    try:
        rel = Path(str(relative or ""))
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise ValueError("unsafe artifact path")
        base = Path(root).resolve()
        image = (base / rel).resolve()
        if not image.is_relative_to(base) or not image.is_file():
            raise ValueError("artifact missing or outside version directory")
        size = image.stat().st_size
        if size > MAX_HISTORY_IMAGE_BYTES:
            raise ValueError(
                f"history image {size} bytes exceeds QQ image limit"
                f" {MAX_HISTORY_IMAGE_BYTES} bytes (not sendable)"
            )
        data = image.read_bytes()
        if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("artifact digest mismatch")
        width, height = png_ihdr_size(data)
        if width is None:
            raise ValueError("artifact is not PNG")
        if expected_width not in (0, width) or expected_height not in (0, height):
            raise ValueError("artifact size mismatch")
    except (OSError, ValueError, TypeError) as error:
        return None, str(error)
    return image, ""


def fold_revision_runs(cells):
    """把逐版本格子折叠成内容修订运行段。

    cells: [(version, key, payload), ...] 按版本旧到新；key=None 表示该版本无有效
    内容（缺失/不适用），是间断。只折叠连续相同 key；间断两侧的相同 key 不合并。
    返回新到旧的修订字典列表，payload 取该修订首次出现的格子：

        [{"key", "first_seen_version", "last_seen_version", "payload"}, ...]
    """
    runs: list[dict] = []
    contiguous = False
    for version, key, payload in cells:
        if key is None:
            contiguous = False
            continue
        if runs and contiguous and runs[-1]["key"] == key:
            runs[-1]["last_seen_version"] = version
        else:
            runs.append(
                {
                    "key": key,
                    "first_seen_version": version,
                    "last_seen_version": version,
                    "payload": payload,
                }
            )
        contiguous = True
    runs.reverse()
    return runs
