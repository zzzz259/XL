"""音频功能域的 Qt-free 目录服务。"""

from __future__ import annotations

import os
from pathlib import Path

from app.core.audio_library import (
    export_audio_files,
    format_duration,
    format_size,
    scan_audio_files,
)
from app.core.audio_repository import mark_all_read, mark_read, sync_audio_snapshot


class AudioService:
    """提供音频目录的缓存读取与最终产物操作。"""

    format_duration = staticmethod(format_duration)
    format_size = staticmethod(format_size)

    def __init__(self, output_dir: str | os.PathLike[str]) -> None:
        self.output_dir = Path(output_dir)
        self._audio_files: list[dict] | None = None

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
        return files

    def invalidate(self) -> None:
        """使内存目录缓存失效，下一次读取时重新扫描。"""
        self._audio_files = None

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
