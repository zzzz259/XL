"""音频功能域的 Qt-free 目录服务。"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from app.core.audio_library import (
    export_audio_files,
    format_duration,
    format_size,
    scan_audio_files,
)
from app.core.audio_repository import mark_all_read, mark_read, sync_audio_snapshot


def _normalise_directory(value: str | os.PathLike[str] | None) -> str:
    return str(value or "").replace("\\", "/").strip("/")


class AudioCatalogIndex:
    """音频目录的 Qt-free 层级索引。

    索引保存全部音频元数据，但不创建任何 Qt 节点；页面只在用户展开目录
    时根据索引构造下一层，避免一次性创建数千个 ``QTreeWidgetItem``。
    """

    def __init__(self, audio_files: list[dict]) -> None:
        self.audio_files = audio_files
        self._children: dict[str, tuple[str, ...]] = {}
        self._files_by_directory: dict[str, tuple[dict, ...]] = {}
        self._files_by_scope: dict[str, tuple[dict, ...]] = {}
        self._build(audio_files)

    def _build(self, audio_files: list[dict]) -> None:
        children: dict[str, set[str]] = defaultdict(set)
        files_by_directory: dict[str, list[dict]] = defaultdict(list)
        files_by_scope: dict[str, list[dict]] = defaultdict(list)

        for info in audio_files:
            directory = _normalise_directory(info.get("dir"))
            files_by_directory[directory].append(info)
            parts = directory.split("/") if directory else []
            ancestors = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
            parent = ""
            for current in ancestors:
                children[parent].add(current)
                parent = current
            for scope in [""] + ancestors:
                files_by_scope[scope].append(info)

        self._children = {
            directory: tuple(sorted(values))
            for directory, values in children.items()
        }
        self._files_by_directory = {
            directory: tuple(values)
            for directory, values in files_by_directory.items()
        }
        self._files_by_scope = {
            directory: tuple(values)
            for directory, values in files_by_scope.items()
        }

    def root_directories(self) -> tuple[str, ...]:
        return self._children.get("", ())

    def child_directories(self, directory: str) -> tuple[str, ...]:
        return self._children.get(_normalise_directory(directory), ())

    def files_in_directory(self, directory: str) -> tuple[dict, ...]:
        return self._files_by_directory.get(_normalise_directory(directory), ())

    def files_under(self, directory: str) -> tuple[dict, ...]:
        return self._files_by_scope.get(_normalise_directory(directory), ())

    def has_children(self, directory: str) -> bool:
        directory = _normalise_directory(directory)
        return bool(self.child_directories(directory) or self.files_in_directory(directory))


class AudioService:
    """提供音频目录的缓存读取与最终产物操作。"""

    format_duration = staticmethod(format_duration)
    format_size = staticmethod(format_size)

    def __init__(self, output_dir: str | os.PathLike[str]) -> None:
        self.output_dir = Path(output_dir)
        self._audio_files: list[dict] | None = None
        self._catalog_index: AudioCatalogIndex | None = None

    @property
    def audio_dir(self) -> Path:
        return self.output_dir / "audio"

    def load_catalog(self, force: bool = False) -> list[dict]:
        """读取并同步音频目录；非强制刷新时复用当前目录快照。"""
        if self._audio_files is not None and not force:
            return self._audio_files
        files = scan_audio_files(str(self.audio_dir))
        sync_audio_snapshot(str(self.audio_dir), files)
        self._audio_files = files
        self._catalog_index = AudioCatalogIndex(files)
        return files

    @property
    def catalog_index(self) -> AudioCatalogIndex | None:
        return self._catalog_index

    @property
    def catalog_loaded(self) -> bool:
        return self._audio_files is not None and self._catalog_index is not None

    def invalidate(self) -> None:
        """使内存目录缓存失效，下一次读取时重新扫描。"""
        self._audio_files = None
        self._catalog_index = None

    def mark_all_read(self) -> bool:
        changed = mark_all_read(str(self.audio_dir))
        if self._audio_files is not None:
            for info in self._audio_files:
                info["unread"] = False
        return changed

    def mark_read(self, relative_name: str) -> bool:
        changed = mark_read(str(self.audio_dir), relative_name)
        if changed and self._audio_files is not None:
            key = str(relative_name).replace("\\", "/")
            for info in self._audio_files:
                if str(info.get("name", "")).replace("\\", "/") == key:
                    info["unread"] = False
                    break
        return changed

    def export_selected(
        self,
        audio_files: list[dict],
        destination_dir: str | os.PathLike[str],
    ) -> tuple[int, list[str]]:
        return export_audio_files(audio_files, str(destination_dir))
