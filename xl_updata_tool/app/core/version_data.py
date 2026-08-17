"""版本差异计算：只处理数据，不依赖 Qt 或窗口状态。"""

from collections.abc import Iterable, Mapping

from .bundle_parser import compute_delta


def compute_download_hashes(
    timestamp: int,
    version_timestamps: Iterable[int],
    hashes_by_version: Mapping[int, Iterable[str]],
) -> set[str]:
    """计算某版本相对最近历史版本的下载增量；没有增量时回退到全量。"""
    previous_timestamp = max(
        (value for value in version_timestamps if value < timestamp),
        default=None,
    )
    current = set(hashes_by_version.get(timestamp, ()))
    previous = set(hashes_by_version.get(previous_timestamp, ())) if previous_timestamp else set()
    delta = current - previous
    return delta or current


def compute_version_delta_map(
    version_timestamps: Iterable[int],
    hashes_by_version: Mapping[int, Iterable[str]],
) -> dict[int, tuple[int, int, int]]:
    """返回每个版本相对上一版本的 ``(added, removed, common)``。"""
    ordered = sorted(version_timestamps)
    result = {}
    previous = None
    for timestamp in ordered:
        current = set(hashes_by_version.get(timestamp, ()))
        if previous is not None:
            delta = compute_delta(previous, current)
            result[timestamp] = (
                len(delta["added"]),
                len(delta["removed"]),
                delta["common"],
            )
        previous = current
    return result
