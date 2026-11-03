"""一次更新任务的 staging、处理和原子发布边界。"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .portraits.source_io import SourcePin

from .gacha_catalog import publish_catalog, read_published_catalog
from .versioning import (
    VersionWorkspace,
    publish_version,
    read_current_pointer,
    write_current_pointer,
)

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
    updated_character_count: int = 0  # 同 ID 但内容变更、被重渲的角色数（v1 全字段比较，从严）
    warnings: tuple[str, ...] = ()
    schedule_enabled: bool = True
    portrait_pin: SourcePin | None = field(default=None, repr=False)
    card_intent: dict | None = field(default=None, repr=False)
    portrait_ready_ref: object | None = field(default=None, repr=False)
    portrait_source_state: str = "failed"
    portrait_pin_digest: str | None = None


class UpdatePipeline:
    def __init__(self, data_dir: str | Path, processor: Callable[[Path], ProcessResult] | None = None, *, publication=None):
        self.data_dir = Path(data_dir)
        self.versions_dir = self.data_dir / "versions"
        self.processor = processor or self._not_configured
        self.publication = publication if publication is not None else getattr(processor, "publication", None)

    @staticmethod
    def _not_configured(_staging_dir: Path) -> ProcessResult:
        raise RuntimeError("update processor is not configured")

    def run_once(self) -> ProcessResult:
        from .portraits.core_outcomes import CoreJournalError, CoreReader, current_run
        context = current_run() if self.publication is not None else None
        if self.publication is not None and (context is None or context.journal.publication is not self.publication or self.data_dir.absolute() != self.publication.data_root):
            raise ValueError("enabled pipeline requires owned core run context")
        if context is not None:
            context.journal._owned()
        result = ProcessResult()
        core_returned = False
        self.data_dir.mkdir(parents=True, exist_ok=True)
        workspace = VersionWorkspace.create(self.versions_dir)
        try:
            candidate = self.processor(workspace.root)
            if not isinstance(candidate, ProcessResult):
                raise TypeError("update processor must return ProcessResult")
            result = candidate
            if result.processed:
                publish_version(workspace, self.versions_dir, result.version_timestamp)
                if result.schedule_enabled:
                    self._carry_forward_schedule(result.version_timestamp)
                write_current_pointer(self.data_dir, result.version_timestamp)
                self._sync_current_character_data(result.version_timestamp)
                catalog_warning = self._sync_current_gacha_catalog(result.version_timestamp)
                if catalog_warning:
                    result = replace(result, warnings=tuple(dict.fromkeys((*result.warnings, catalog_warning))))
                self._carry_forward_cards(result.version_timestamp)
                if result.schedule_enabled:
                    schedule_warning = self._sync_current_schedule(result.version_timestamp)
                    if schedule_warning:
                        warnings = list(dict.fromkeys((*result.warnings, schedule_warning)))
                        if schedule_warning not in warnings[:5]:
                            warnings = [*warnings[:4], schedule_warning]
                        else:
                            warnings = warnings[:5]
                        result = replace(result, warnings=tuple(warnings))
            core_returned = True
            if context is not None:
                self._finish_core(context, result)
            try:
                if context is not None:
                    if result.portrait_ready_ref is not None:
                        context.journal.link_source(context.run_id, result.portrait_ready_ref)
                    elif not result.error and result.portrait_pin is None and result.portrait_source_state == 'failed':
                        context.journal.fail_source(context.run_id, code='source_capture_unavailable')
                if result.processed and result.portrait_pin is not None and self.publication is not None:
                    self.publication.attest_published(result.version_timestamp, schedule_enabled=result.schedule_enabled)
                    ref = self.publication.complete(result.version_timestamp, schedule_enabled=result.schedule_enabled)
                    if context is not None:
                        context.journal.link_source(context.run_id, ref)
            except Exception:  # noqa: BLE001 - optional source storage cannot fail published core
                result = replace(result, warnings=(*result.warnings[:4], "portrait: request deferred"))
            return result
        except Exception as error:
            LOGGER.exception("update processing failed")
            if context is not None and core_returned and CoreReader(context.journal.path, scope=context.journal.scope).read_run(context.run_id) is None:
                raise CoreJournalError('core outcome persistence failed') from error
            version = result.version_timestamp
            if context is not None:
                bound_version = context.target_version()
                if version is None and bound_version is not None:
                    version = int(bound_version)
            result = replace(result, version_timestamp=version, error=str(error))
            if context is not None and CoreReader(context.journal.path, scope=context.journal.scope).read_run(context.run_id) is None:
                self._finish_core(context, result)
            return result
        finally:
            if workspace.root.exists():
                shutil.rmtree(workspace.root, ignore_errors=True)

    @staticmethod
    def _finish_core(context, result):
        phase = 'failed' if result.error else 'published' if result.processed else 'no_change'
        source_state = 'not_requested' if phase == 'failed' else 'pending' if result.portrait_pin is not None or result.portrait_ready_ref is not None else result.portrait_source_state
        if phase in ('published', 'no_change') and source_state == 'failed':
            source_state = 'pending'  # Source resolution is independent of the immutable core commit.
        if context.target_version() is None and result.version_timestamp is not None:
            context.bind_target(result.version_timestamp, classification='processed' if result.processed else 'noop', announcement_eligible=False)
        context.finish(
            phase=phase, source_state=source_state,
            counts={name: getattr(result, name) for name in (
                'character_count', 'skin_count', 'card_count', 'card_warning_count',
                'card_failure_count', 'new_character_count', 'updated_character_count')},
            warnings=tuple('core_optional_warning' for _ in result.warnings[:5]),
            cards=result.card_intent,
            pin_digest=result.portrait_pin.pin_digest if result.portrait_pin is not None else result.portrait_ready_ref.pin_digest if result.portrait_ready_ref is not None else result.portrait_pin_digest,
            bootstrap_ref=result.portrait_ready_ref,
            source_recovery=({'kind': 'published_attestation', 'schedule_enabled': result.schedule_enabled}
                             if phase == 'published' and result.portrait_pin is not None
                             and result.card_intent is not None
                             and result.card_intent.get('kind') in ('none', 'batch')
                             and result.card_intent.get('reason') != 'bootstrap-history-unavailable'
                             else None),
            code='core_failed' if result.error else None,
        )

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

    def _carry_forward_schedule(self, version_timestamp: int) -> None:
        """Keep the last valid immutable schedule available when parsing/rendering skips."""
        current_schedule = self.versions_dir / str(version_timestamp) / "rerun_schedule"
        if (current_schedule / "current.json").is_file():
            return
        previous_version = read_current_pointer(self.data_dir)
        if previous_version is None or previous_version == version_timestamp:
            return
        previous_schedule = self.versions_dir / str(previous_version) / "rerun_schedule"
        if not (previous_schedule / "current.json").is_file():
            return
        try:
            from .rerun_schedule import read_current_schedule
            read_current_schedule(previous_schedule)
            shutil.copytree(previous_schedule, current_schedule)
        except (FileExistsError, OSError, ValueError, KeyError, TypeError) as error:
            LOGGER.warning(
                "stage=rerun_publish status=skipped reason=invalid_last_known_good version=%s error=%s",
                version_timestamp,
                error,
            )
            return
        LOGGER.info(
            "stage=rerun_publish game_version=%s status=stale_fallback source_version=%s",
            version_timestamp,
            previous_version,
        )

    def _sync_current_gacha_catalog(self, version_timestamp: int) -> str | None:
        """Expose only validated artifacts from an already published game version."""
        source = self.versions_dir / str(version_timestamp) / 'gacha_config'
        if not (source / 'current.json').is_file():
            return None
        try:
            catalog, _ = read_published_catalog(source)
            if catalog['version'] != str(version_timestamp):
                raise ValueError('catalog does not match published game version')
            publish_catalog(catalog, self.data_dir / 'gacha_config')
            return None
        except (OSError, ValueError, KeyError, TypeError) as error:
            LOGGER.warning('stage=gacha_catalog_publish version=%s status=failed fallback=last_known_good error=%s',
                           version_timestamp, error)
            return f'gacha catalog publish: {error}'[:500]

    def _sync_current_schedule(self, version_timestamp: int) -> str | None:
        """Mirror the immutable snapshot to data/rerun_schedule for operators."""
        source = self.versions_dir / str(version_timestamp) / "rerun_schedule"
        try:
            manifest = source / "current.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            relative = Path(str(payload["artifact_path"]))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe schedule artifact path")
            snapshot = (source / relative).resolve()
            if not snapshot.is_relative_to(source.resolve()) or not snapshot.is_file():
                raise ValueError("schedule artifact is missing")
            digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            if digest != payload.get("render_sha256"):
                raise ValueError("schedule artifact digest mismatch")
            target = self.data_dir / "rerun_schedule"
            target_snapshot = target / relative
            target_snapshot.parent.mkdir(parents=True, exist_ok=True)
            if not target_snapshot.exists():
                shutil.copy2(snapshot, target_snapshot)
            self._atomic_copy(snapshot, target / "current.png")
            self._atomic_copy(manifest, target / "current.json")
            return None
        except (OSError, ValueError, KeyError, TypeError) as error:
            LOGGER.exception(
                "stage=rerun_publish game_version=%s status=failed mirror_error=%s",
                version_timestamp,
                error,
            )
            return f"rerun schedule publish: {error}"[:500]

    @staticmethod
    def _atomic_copy(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".part")
        shutil.copy2(source, temporary)
        temporary.replace(destination)
