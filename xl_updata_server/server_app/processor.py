"""真实 CDN 更新处理器。"""

from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .catalog import read_catalog
from .cdn import CdnClient
from .character_adapter import parse_character_snapshot
from .character_card_exporter import CardExportReport, export_character_cards
from .config import ServerConfig
from .extractor import extract_lua_files
from .lua_decoder import decode_lua_directory
from .pipeline import ProcessResult
from .selector import bundle_hashes_for_assets, select_lua_assets
from .versioning import read_current_pointer

LOGGER = logging.getLogger(__name__)


def bundle_output_dir(version_root: Path, category: str) -> Path:
    return version_root / "bundles" / category


def _load_current(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _canonical_character(data) -> str:
    """规范化 JSON（sort_keys）表示，用于跨版本同 ID 角色的全字段比较。"""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _log_stage(stage: str, started: float, paths: tuple[Path, ...] = ()) -> None:
    elapsed = max(time.perf_counter() - started, 0.001)
    total_bytes = sum(path.stat().st_size for path in paths if path.is_file())
    if total_bytes:
        LOGGER.info(
            "stage=%s elapsed=%.2fs files=%d bytes=%d throughput=%.2fMiB/s",
            stage,
            elapsed,
            len(paths),
            total_bytes,
            total_bytes / elapsed / 1024 / 1024,
        )
    else:
        LOGGER.info("stage=%s elapsed=%.2fs files=%d", stage, elapsed, len(paths))


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def build_manifest_payload(
    version: int,
    lua_hashes: tuple[str, ...],
    character_ids: tuple[str, ...] | list[str] | set[str],
    new_character_ids: set[str],
    card_report: CardExportReport,
    staging: Path,
    baseline: bool = False,
    updated_character_ids: set[str] | frozenset = frozenset(),
) -> dict:
    """Build the version manifest from the Lua-only processing result."""
    return {
        "version": version,
        "baseline": baseline,
        "lua_hashes": list(lua_hashes),
        "character_ids": sorted(character_ids),
        "new_characters_count": len(new_character_ids),
        "updated_characters_count": len(updated_character_ids),
        "character_cards": {
            "seen": card_report.seen,
            "created": card_report.created,
            "warned": card_report.warned,
            "failed": card_report.failed,
            "outputs": [_relative_posix(path, staging) for path in card_report.outputs],
            "warnings": list(card_report.warnings),
            "failures": [
                {"character_id": item.character_id, "error": item.error}
                for item in card_report.failures
            ],
        },
        "downloaded_hashes": list(lua_hashes),
    }


def _write_outbox(
    data_dir: Path,
    version: int,
    card_report: CardExportReport,
    characters: dict[str | int, dict[str, Any]],
    only_character_ids: set[str],
) -> None:
    """把新增长图复制到 outbox 并写清单，供 QQ Bot 消费。

    硬约束：outbox 只写新增角色（变更重渲的长图不推送 QQ 群）。
    过滤后没有新增产出时整个 outbox 目录都不建。
    """
    outputs = [
        (character_id, source_path)
        for character_id, source_path in card_report.character_outputs
        if str(character_id) in only_character_ids
    ]
    if not outputs:
        return
    outbox_dir = data_dir / "outbox" / str(version)
    outbox_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, str]] = []
    for character_id, source_path in outputs:
        destination = outbox_dir / source_path.name
        shutil.copy2(source_path, destination)
        character = characters.get(character_id) or characters.get(int(character_id)) if character_id.isdigit() else None
        name = "未知"
        if isinstance(character, dict):
            name = str(character.get("name", "未知")).split("/", 1)[0]
        items.append({
            "character_id": str(character_id),
            "name": name,
            "filename": source_path.name,
        })
    _write_json(
        outbox_dir / "manifest.json",
        {
            "version": version,
            "created_at": datetime.now().astimezone().isoformat(),
            "characters": items,
        },
    )


class ProductionUpdateProcessor:
    def __init__(self, config: ServerConfig):
        self.config = config
        self.client = CdnClient(config.cdn_base)
        self.catalog_dir = config.data_dir / "catalogs"

    def __call__(self, staging: Path) -> ProcessResult:
        update_info = self.client.fetch_update_info()
        current_version = read_current_pointer(self.config.data_dir)
        if current_version == update_info.timestamp:
            return ProcessResult()
        current = _load_current(self.config.data_dir / "character_data" / "current.json")
        if current_version is None and current and int(current.get("version", 0)) == update_info.timestamp:
            return ProcessResult()

        # 进入真实处理前向 Bot 广播更新开始；结束（含失败）后写出结果状态
        self._write_update_status(updating=True, version=update_info.timestamp)
        try:
            result = self._process(staging, update_info, current)
        except Exception as error:
            self._write_update_status(
                updating=False, version=update_info.timestamp, error=str(error)
            )
            raise
        self._write_update_status(
            updating=False,
            version=update_info.timestamp,
            new_characters=result.new_character_count,
        )
        return result

    def _write_update_status(
        self,
        *,
        updating: bool,
        version: int,
        new_characters: int = 0,
        error: str | None = None,
    ) -> None:
        now_iso = datetime.now().astimezone().isoformat()
        _write_json(
            self.config.data_dir / "update_status.json",
            {
                "updating": updating,
                "version": version,
                "new_characters": new_characters,
                "error": error,
                "updated_at": now_iso,
            },
        )
        # 事件流水：开始/结束各追加一行，Bot 按行消费，
        # 避免短于轮询间隔的快速更新被漏报
        event = "start" if updating else "finish"
        line = json.dumps(
            {
                "event": event,
                "version": version,
                "new_characters": new_characters,
                "error": error,
                "at": now_iso,
            },
            ensure_ascii=False,
        )
        events_log = self.config.data_dir / "update_events.jsonl"
        events_log.parent.mkdir(parents=True, exist_ok=True)
        with events_log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _process(self, staging: Path, update_info, current: dict | None) -> ProcessResult:
        total_started = time.perf_counter()
        version_catalog = self.client.fetch_version_catalog(update_info.file)
        data_entry = next((entry for entry in version_catalog.categories if entry.name == "Data"), None)
        if data_entry is None:
            raise ValueError("版本清单缺少 Data 分类")
        local_catalog = self.catalog_dir / f"data_{data_entry.hash}.bundle"
        if not local_catalog.is_file():
            self.client.download_category_catalog("Data", data_entry.hash, local_catalog)
        catalog = read_catalog(local_catalog, self.config.assetbundle_key)

        data_assets = select_lua_assets(catalog)
        lua_hashes = bundle_hashes_for_assets(catalog, data_assets)
        if not lua_hashes:
            raise ValueError("未找到角色 Lua Bundle")
        stage_started = time.perf_counter()
        lua_paths = self._download_hashes(lua_hashes, bundle_output_dir(staging, "Data"))
        _log_stage("download_data", stage_started, lua_paths)

        work_dir = staging / ".work"
        raw_lua = work_dir / "lua-raw"
        stage_started = time.perf_counter()
        extract_lua_files(lua_paths, raw_lua)
        _log_stage("extract_lua", stage_started)

        decoded_lua = work_dir / "lua-decoded"
        stage_started = time.perf_counter()
        decode_report = decode_lua_directory(
            raw_lua,
            decoded_lua,
            self.config.java_bin,
            self.config.unluac_jar,
            self.config.unluac_opmap,
        )
        _log_stage("decode_lua", stage_started)
        if decode_report.failed or decode_report.success == 0:
            raise ValueError(f"角色 Lua 解码失败: success={decode_report.success}, failed={decode_report.failed}")

        snapshot = parse_character_snapshot(decoded_lua)
        characters = snapshot.get("characters", {})
        character_ids = {str(key) for key in characters}
        if not character_ids:
            raise ValueError("角色 Lua 未解析出角色 ID")

        is_baseline = current is None
        current_ids = set(current.get("characters", {}).keys()) if current else set()
        current_characters = (
            {str(key): value for key, value in (current.get("characters") or {}).items()}
            if current else {}
        )
        parsed_characters = {str(key): value for key, value in characters.items()}
        new_character_ids = character_ids - current_ids
        # 变更检测：同 ID 但规范化 JSON 有任意字段差异（v1 从严，全字段比较；首跑无基线不产生变更）
        updated_character_ids: set[str] = set()
        if not is_baseline:
            for character_id in character_ids & current_ids:
                if _canonical_character(parsed_characters.get(character_id)) != _canonical_character(
                    current_characters.get(character_id)
                ):
                    updated_character_ids.add(character_id)
        render_character_ids = new_character_ids | updated_character_ids

        stage_started = time.perf_counter()
        if self.config.render_cards:
            card_report = export_character_cards(
                characters,
                staging / "character_cards",
                self.config.character_card_font,
                only_character_ids=render_character_ids if current else None,
                node_bin=self.config.node_bin,
            )
            LOGGER.info(
                "stage=export_character_cards elapsed=%.2fs characters_seen=%d cards_created=%d "
                "cards_warned=%d cards_failed=%d new=%d updated=%d",
                time.perf_counter() - stage_started,
                card_report.seen,
                card_report.created,
                card_report.warned,
                card_report.failed,
                len(new_character_ids),
                len(updated_character_ids),
            )
        else:
            # 只建角色数据库的模式：完全不渲染，节省弱机的 CPU 与磁盘
            card_report = CardExportReport(
                seen=0, created=0, warned=0, failed=0,
                outputs=(), warnings=(), failures=(),
            )
            LOGGER.info("stage=export_character_cards skipped reason=cards.enabled=false")

        if new_character_ids and not is_baseline:
            _write_outbox(
                self.config.data_dir, update_info.timestamp, card_report, characters,
                only_character_ids=new_character_ids,
            )
        elif is_baseline:
            LOGGER.info("stage=baseline skip_outbox version=%s reason=首跑建立基线，跳过 outbox 分发", update_info.timestamp)
        elif not new_character_ids:
            LOGGER.info("stage=skip_outbox version=%s reason=无新增角色（变更角色不推送 QQ 群）", update_info.timestamp)

        payload = {
            "version": update_info.timestamp,
            "characters": characters,
            "index": snapshot.get("index", []),
            "words": snapshot.get("words", {}),
        }
        _write_json(staging / "character_data" / "versions" / f"{update_info.timestamp}.json", payload)
        _write_json(staging / "character_data" / "current.json", payload)
        _write_json(
            staging / "manifest.json",
            build_manifest_payload(
                version=update_info.timestamp,
                lua_hashes=lua_hashes,
                character_ids=character_ids,
                new_character_ids=new_character_ids,
                card_report=card_report,
                staging=staging,
                baseline=is_baseline,
                updated_character_ids=updated_character_ids,
            ),
        )

        # 发布前删除 AB 包与中间工作目录，减少磁盘占用
        if (staging / "bundles").exists():
            shutil.rmtree(staging / "bundles", ignore_errors=True)
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)

        LOGGER.info(
            "stage=total elapsed=%.2fs version=%s characters=%d new_characters=%d updated_characters=%d cards=%d card_failures=%d",
            time.perf_counter() - total_started,
            update_info.timestamp,
            len(character_ids),
            len(new_character_ids),
            len(updated_character_ids),
            card_report.created,
            card_report.failed,
        )
        return ProcessResult(
            version_timestamp=update_info.timestamp,
            processed=True,
            downloaded_hashes=tuple(sorted(lua_hashes)),
            character_count=len(character_ids),
            skin_count=0,
            card_count=card_report.created,
            card_warning_count=card_report.warned,
            card_failure_count=card_report.failed,
            new_character_count=len(new_character_ids),
            updated_character_count=len(updated_character_ids),
        )

    def _download_hashes(self, hashes: tuple[str, ...], directory: Path) -> tuple[Path, ...]:
        paths = []
        for bundle_hash in hashes:
            paths.append(self.client.download_bundle(bundle_hash, directory / f"{bundle_hash}.bundle"))
        return tuple(paths)
