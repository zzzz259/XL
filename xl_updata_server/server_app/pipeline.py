"""一次更新任务的 staging、处理和原子发布边界。"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .versioning import VersionWorkspace, publish_version, write_current_pointer

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessResult:
    version_timestamp: int | None = None
    processed: bool = False
    downloaded_hashes: tuple[str, ...] = ()
    character_count: int = 0
    skin_count: int = 0
    card_count: int = 0
    card_warning_count: int = 0
    card_failure_count: int = 0
    error: str | None = None
    new_character_count: int = 0


class UpdatePipeline:
    def __init__(self, data_dir: str | Path, processor: Callable[[Path], ProcessResult] | None = None):
        self.data_dir = Path(data_dir)
        self.versions_dir = self.data_dir / "versions"
        self.processor = processor or self._not_configured

    @staticmethod
    def _not_configured(_staging_dir: Path) -> ProcessResult:
        raise RuntimeError("update processor is not configured")

    def run_once(self) -> ProcessResult:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        workspace = VersionWorkspace.create(self.versions_dir)
        try:
            result = self.processor(workspace.root)
            if not isinstance(result, ProcessResult):
                raise TypeError("update processor must return ProcessResult")
            if result.processed:
                publish_version(workspace, self.versions_dir, result.version_timestamp)
                write_current_pointer(self.data_dir, result.version_timestamp)
                self._sync_current_character_data(result.version_timestamp)
                self._carry_forward_cards(result.version_timestamp)
            return result
        except Exception as error:
            LOGGER.exception("update processing failed")
            return ProcessResult(error=str(error))
        finally:
            if workspace.root.exists():
                shutil.rmtree(workspace.root, ignore_errors=True)

    def _carry_forward_cards(self, version_timestamp: int) -> None:
        """把最近一个旧版本的图鉴长图继承进新版本目录，保证角色库不断档。

        每个版本只渲染新增角色，若不继承，@机器人 查询老角色会因找不到图片而断供。
        """
        current_dir = self.versions_dir / str(version_timestamp) / "character_cards"
        current_dir.mkdir(parents=True, exist_ok=True)
        existing = {p.name for p in current_dir.glob("*.png")}
        previous = sorted(
            (
                d for d in self.versions_dir.iterdir()
                if d.is_dir() and d.name != str(version_timestamp) and not d.name.startswith(".")
            ),
            key=lambda d: d.name,
            reverse=True,
        )
        for prev in previous:
            prev_cards = prev / "character_cards"
            if not prev_cards.is_dir():
                continue
            inherited = 0
            for card in prev_cards.glob("*.png"):
                if card.name not in existing:
                    shutil.copy2(card, current_dir / card.name)
                    inherited += 1
            if inherited:
                LOGGER.info("从版本 %s 继承 %d 张图鉴长图", prev.name, inherited)
            break

    def _sync_current_character_data(self, version_timestamp: int) -> None:
        """把已发布版本的角色数据镜像到 data 根，供下一版本计算新增差集。"""
        published = self.versions_dir / str(version_timestamp) / "character_data" / "current.json"
        if not published.is_file():
            return
        mirror = self.data_dir / "character_data" / "current.json"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        temporary = mirror.with_name("current.json.part")
        shutil.copy2(published, temporary)
        temporary.replace(mirror)
