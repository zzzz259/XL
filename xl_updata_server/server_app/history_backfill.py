"""历史图鉴/立绘内容回填：只读 DB 索引 → 旧到新逐版本重建 → 显式发布。

- ``read_history_index`` 以只读（``mode=ro``）打开更新工具 DB，读取
  ``versions``/``bundles``/``sub_bundles``，按 timestamp 旧到新给出
  ``manifest_file`` 与分类包哈希（DB 只是输入，不是运行时依赖）。
- ``run_backfill`` 默认只预检；``execute=True`` 才下载/解码/渲染。
  复用现有组件（read_catalog、Lua 选择/提取/解码、parse_character_snapshot、
  export_character_cards、card manifest、run_portrait_update 回填策略）。
  最旧版本建立完整图鉴/立绘基线（回填策略允许首个历史基线真实渲染），后续版本只渲染新增或内容
  变化的角色/皮肤，其余继承并保留原 source_version。AB 下载经
  ``BundleCacheClient`` 在 ``work_root/cache`` 按内容哈希共享：首次下载由
  CdnClient 校验 MD5，缓存命中仍重新校验，不符即重下。全程关闭通知：
  不追加事件、不写 outbox/portrait_outbox、不触碰生产数据根。
  产物全部写入独立工作根 ``work_root/versions/<v>/``，逐版本保存进度
  （``backfill.json``，含样本 scope 与 full_scope）；重复执行可恢复且幂等
  ——全量完成可跳过任何后续运行，样本完成只允许同范围幂等跳过，后续全量
  运行不会直接跳过样本版本；存在失败立绘（failed_skins>0）的版本记 failed
  供重跑恢复。
- ``publish_backfill`` 是单独的显式阶段：缺失的新版本必须带
  status=completed、full_scope=True、failed_skins=0 的完成记录并通过完整
  产物校验，才经同文件系统临时目录原子改名合入生产版本目录；
  已存在的版本仅按**生产实际文件与角色数据**重建并验证派生的 card
  manifest（多候选改名残留优先取版本根 manifest outputs 声明的产物，仍
  歧义则跳过并记日志，绝不猜测；绝不从工作根复制、绝不覆盖已有图片或
  角色 JSON）；未通过校验的版本计入 rejected 且不合入。生产
  ``current_version.json``、根
  ``character_data/current.json``、``update_events.jsonl``、outbox 与
  portrait_outbox 永不修改。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .catalog import read_catalog
from .cdn import CategoryInfo, CdnClient
from .character_adapter import parse_character_snapshot
from .character_card_exporter import export_character_cards
from .character_card_manifest import (
    canonical_character_sha,
    copy_manifest_artifacts,
    load_card_manifest,
    read_version_card_outputs,
    resolve_character_artifact,
    save_card_manifest,
    validate_card_manifest,
    write_card_manifest,
)
from .config import ServerConfig
from .extractor import extract_lua_files, safe_role_name
from .lua_decoder import decode_lua_directory
from .portrait_update import run_portrait_update
from .selector import (
    bundle_hashes_for_assets,
    select_lua_assets,
    select_portrait_callback_lua_assets,
)

LOGGER = logging.getLogger(__name__)

PROGRESS_NAME = "backfill.json"


@dataclass(frozen=True)
class HistoryVersion:
    timestamp: int
    manifest_file: str
    categories: tuple[CategoryInfo, ...]
    bundle_hashes: tuple[str, ...]


@dataclass(frozen=True)
class BackfillVersionResult:
    version: int
    status: str  # pending | completed | failed
    rendered: int = 0
    inherited: int = 0
    cards: int = 0
    error: str | None = None


@dataclass(frozen=True)
class BackfillReport:
    versions: tuple[BackfillVersionResult, ...]


@dataclass(frozen=True)
class PublishReport:
    published: tuple[int, ...]
    merged: tuple[int, ...]
    rejected: tuple[int, ...]


def _md5_hex(path: Path) -> str:
    digest = hashlib.md5()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BundleCacheClient:
    """按内容哈希共享的 AB 下载缓存（work_root/cache），包一层 CdnClient。

    首次下载走被包装客户端（CdnClient 内部按 hash 校验 MD5）；缓存命中
    仍重新校验 MD5，不符即删缓存重新下载，绝不复用损坏副本。
    """

    def __init__(self, client: CdnClient, cache_dir: str | Path):
        self._client = client
        self._cache_dir = Path(cache_dir)

    def fetch_update_info(self):
        return self._client.fetch_update_info()

    def fetch_version_catalog(self, manifest_file):
        return self._client.fetch_version_catalog(manifest_file)

    def download_file(self, *args, **kwargs):
        return self._client.download_file(*args, **kwargs)

    def download_category_catalog(self, category, category_hash, destination):
        return self._client.download_category_catalog(category, category_hash, destination)

    def download_bundle(self, bundle_hash, destination):
        bundle_hash = str(bundle_hash)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cached = self._cache_dir / f"{bundle_hash}.bundle"
        if cached.is_file() and _md5_hex(cached) != bundle_hash.lower():
            LOGGER.warning("stage=history_backfill status=cache_corrupted hash=%s", bundle_hash)
            cached.unlink()
        if not cached.is_file():
            self._client.download_bundle(bundle_hash, cached)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # 复制而非硬链接：消费方（如 repair_bundle_header）可能改写副本，
        # 缓存必须保持 pristine 供后续 MD5 校验与复用。
        shutil.copy2(cached, destination)
        return destination


def read_history_index(db_path: str | Path) -> tuple[HistoryVersion, ...]:
    """只读打开更新工具 DB，按 timestamp 旧到新枚举历史版本。

    分类包哈希取自 ``bundles`` 表（构造历史版本清单用），
    ``bundle_hashes`` 取自 ``sub_bundles`` 表（预检下载范围用）。
    """
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"历史 DB 不存在: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT timestamp, manifest_file FROM versions ORDER BY timestamp ASC"
        ).fetchall()
        index: list[HistoryVersion] = []
        for timestamp, manifest_file in rows:
            categories = tuple(
                CategoryInfo(
                    name=str(name),
                    hash=str(bundle_hash),
                    size=int(size or 0),
                    version=int(ver or 0),
                )
                for name, bundle_hash, size, ver in connection.execute(
                    "SELECT name, hash, size, ver FROM bundles WHERE version_timestamp=? ORDER BY name",
                    (timestamp,),
                )
            )
            bundle_hashes = tuple(
                sorted(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT hash FROM sub_bundles WHERE version_timestamp=?", (timestamp,)
                    )
                )
            )
            index.append(
                HistoryVersion(
                    timestamp=int(timestamp),
                    manifest_file=str(manifest_file or ""),
                    categories=categories,
                    bundle_hashes=bundle_hashes,
                )
            )
        return tuple(index)
    finally:
        connection.close()


def _select_versions(
    index: Sequence[HistoryVersion], versions: Sequence[int] | None
) -> tuple[HistoryVersion, ...]:
    if versions is None:
        return tuple(index)
    wanted = tuple(dict.fromkeys(int(v) for v in versions))
    known = {item.timestamp for item in index}
    missing = [v for v in wanted if v not in known]
    if missing:
        raise ValueError(f"DB 中不存在的历史版本: {missing}")
    return tuple(item for item in index if item.timestamp in set(wanted))


def run_backfill(
    *,
    config: ServerConfig,
    client: CdnClient,
    db_path: str | Path,
    work_root: str | Path,
    versions: Sequence[int] | None = None,
    execute: bool = False,
    only_character_ids: Sequence[str] | None = None,
    only_skin_ids: Sequence[str] | None = None,
) -> BackfillReport:
    """历史回填。默认只预检（零下载、零渲染、零产物）；execute 才真正处理。

    ``only_character_ids`` / ``only_skin_ids`` 为样本过滤：过滤运行的完成
    记录绑定该范围（进度文件 ``scope`` + ``full_scope=False``），后续全量
    运行不会直接跳过；过滤样本也永远不会被 ``publish_backfill`` 接受。
    """
    index = read_history_index(db_path)
    selected = _select_versions(index, versions)
    if not execute:
        return BackfillReport(
            tuple(
                BackfillVersionResult(version=item.timestamp, status="pending")
                for item in selected
            )
        )
    work_root = Path(work_root)
    results: list[BackfillVersionResult] = []
    for item in selected:
        try:
            results.append(
                _process_version(
                    item,
                    config=config,
                    client=client,
                    work_root=work_root,
                    only_character_ids=only_character_ids,
                    only_skin_ids=only_skin_ids,
                )
            )
        except Exception as error:  # 单版本失败不拖垮其余版本，可修复后重跑恢复
            LOGGER.exception("stage=history_backfill version=%s status=failed", item.timestamp)
            _record_progress(
                work_root / "versions" / str(item.timestamp),
                {
                    "version": item.timestamp,
                    "status": "failed",
                    "full_scope": False,
                    "error": str(error)[:500],
                },
            )
            results.append(
                BackfillVersionResult(
                    version=item.timestamp, status="failed", error=str(error)[:500]
                )
            )
    return BackfillReport(tuple(results))


# ---- 单版本处理 --------------------------------------------------------------


def _scope_payload(
    only_character_ids: Sequence[str] | None,
    only_skin_ids: Sequence[str] | None,
) -> dict[str, Any]:
    return {
        "characters": sorted({str(c) for c in only_character_ids}) if only_character_ids else None,
        "skins": sorted({str(s) for s in only_skin_ids}) if only_skin_ids else None,
    }


def _read_progress(version_dir: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((version_dir / PROGRESS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _record_progress(version_dir: Path, payload: Mapping[str, Any]) -> None:
    version_dir.mkdir(parents=True, exist_ok=True)
    temporary = version_dir / f"{PROGRESS_NAME}.part"
    temporary.write_text(
        json.dumps({**payload, "at": datetime.now().astimezone().isoformat()}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    temporary.replace(version_dir / PROGRESS_NAME)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    temporary.replace(path)


def _numeric_version_dirs(versions_dir: Path) -> list[Path]:
    if not versions_dir.is_dir():
        return []
    return sorted(
        (d for d in versions_dir.iterdir() if d.is_dir() and d.name.isdigit()),
        key=lambda d: int(d.name),
    )


def _find_previous_backfilled(work_root: Path, version: int) -> tuple[Path | None, dict | None]:
    """工作根内当前版本之前、已有 card manifest 的版本（新到旧取第一个）。"""
    candidates = [
        d
        for d in _numeric_version_dirs(work_root / "versions")
        if int(d.name) < version and load_card_manifest(d / "character_cards") is not None
    ]
    if not candidates:
        return None, None
    previous_dir = candidates[-1]
    return previous_dir, load_card_manifest(previous_dir / "character_cards")


def _load_characters(version_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads((version_dir / "character_data" / "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    characters = payload.get("characters") if isinstance(payload, dict) else None
    return {str(k): v for k, v in characters.items()} if isinstance(characters, Mapping) else {}


def _process_version(
    item: HistoryVersion,
    *,
    config: ServerConfig,
    client: CdnClient,
    work_root: Path,
    only_character_ids: Sequence[str] | None,
    only_skin_ids: Sequence[str] | None,
) -> BackfillVersionResult:
    version = item.timestamp
    version_dir = work_root / "versions" / str(version)
    scope = _scope_payload(only_character_ids, only_skin_ids)
    full_scope = scope["characters"] is None and scope["skins"] is None
    progress = _read_progress(version_dir)
    # 完成记录绑定范围：全量完成可跳过任何后续运行；样本完成只允许同范围
    # 重复执行幂等跳过，后续全量运行（不同 scope）必须重处理。
    if (
        progress is not None
        and progress.get("status") == "completed"
        and (progress.get("full_scope") is True or progress.get("scope") == scope)
    ):
        return BackfillVersionResult(version=version, status="completed")

    cache_client = BundleCacheClient(client, work_root / "cache")
    data_category = next((c for c in item.categories if c.name == "Data"), None)
    if data_category is None:
        raise ValueError(f"历史版本 {version} 缺少 Data 分类哈希")
    arts_category = next((c for c in item.categories if c.name == "Arts"), None)

    catalog_cache = work_root / "catalogs"
    catalog_cache.mkdir(parents=True, exist_ok=True)
    data_catalog_path = catalog_cache / f"data_{data_category.hash}.bundle"
    if not data_catalog_path.is_file():
        cache_client.download_category_catalog("Data", data_category.hash, data_catalog_path)
    catalog = read_catalog(data_catalog_path, config.assetbundle_key)
    data_assets = select_lua_assets(catalog) + select_portrait_callback_lua_assets(catalog)
    lua_hashes = bundle_hashes_for_assets(catalog, data_assets)
    if not lua_hashes:
        raise ValueError(f"历史版本 {version} 未找到角色 Lua Bundle")

    staging = work_root / "staging" / str(version)
    bundles_dir = staging / "bundles" / "Data"
    lua_paths = tuple(
        cache_client.download_bundle(bundle_hash, bundles_dir / f"{bundle_hash}.bundle")
        for bundle_hash in lua_hashes
    )
    raw_lua = staging / "lua-raw"
    extract_lua_files(lua_paths, raw_lua)
    decoded_lua = staging / "lua-decoded"
    decode_report = decode_lua_directory(
        raw_lua, decoded_lua, config.java_bin, config.unluac_jar, config.unluac_opmap
    )
    if decode_report.failed or decode_report.success == 0:
        raise ValueError(
            f"历史版本 {version} Lua 解码失败: success={decode_report.success}, failed={decode_report.failed}"
        )
    snapshot = parse_character_snapshot(decoded_lua)
    characters = {str(k): v for k, v in (snapshot.get("characters") or {}).items()}
    if not characters:
        raise ValueError(f"历史版本 {version} 未解析出角色 ID")
    if only_character_ids:
        wanted = {str(cid) for cid in only_character_ids}
        characters = {cid: data for cid, data in characters.items() if cid in wanted}

    previous_dir, previous_manifest = _find_previous_backfilled(work_root, version)
    render_ids = None
    if previous_dir is not None:
        previous_characters = _load_characters(previous_dir)
        render_ids = {
            cid
            for cid, data in characters.items()
            if cid not in previous_characters
            or canonical_character_sha(data) != canonical_character_sha(previous_characters[cid])
        }

    cards_dir = version_dir / "character_cards"
    card_report = export_character_cards(
        characters,
        cards_dir,
        config.character_card_font,
        only_character_ids=render_ids if render_ids is not None else None,
        node_bin=config.node_bin,
    )
    manifest = write_card_manifest(
        cards_dir,
        version=version,
        characters=characters,
        card_report=card_report,
        previous_manifest=previous_manifest,
    )
    copy_manifest_artifacts(manifest, cards_dir, [previous_dir] if previous_dir else [])
    problems = validate_card_manifest(manifest, cards_dir)
    if problems:
        raise ValueError(f"历史版本 {version} 图鉴 manifest 校验失败: {list(problems)[:3]}")

    portrait_summary = None
    if arts_category is not None:
        derived_config = replace(
            config,
            data_dir=Path(work_root),
            portrait_enabled=True,
            portrait_work_dir=Path(work_root) / "portrait_work",
            portrait_outbox_dir=None,
        )
        portrait_summary = run_portrait_update(
            config=derived_config,
            client=cache_client,
            catalog_dir=catalog_cache,
            version=version,
            decoded_lua=decoded_lua,
            staging=version_dir,
            characters=characters,
            arts_category=arts_category,
            backfill=True,
            only_skin_ids=only_skin_ids,
        )
        if not (version_dir / "portraits" / "manifest.json").is_file():
            # 有效配置皮肤为零时 runner 不留 manifest；发布校验要求它存在。
            _write_json_atomic(
                version_dir / "portraits" / "manifest.json",
                {"schema_version": 1, "version": str(version), "skins": []},
            )
    else:
        LOGGER.warning("stage=history_backfill version=%s status=skipped reason=missing_arts_category", version)

    payload = {
        "version": version,
        "characters": characters,
        "index": snapshot.get("index", []),
        "words": snapshot.get("words", {}),
    }
    _write_json_atomic(version_dir / "character_data" / "current.json", payload)
    _write_json_atomic(version_dir / "character_data" / "versions" / f"{version}.json", payload)
    _write_json_atomic(
        version_dir / "manifest.json",
        {
            "version": version,
            "baseline": previous_dir is None,
            "character_ids": sorted(characters),
            "backfill": True,
        },
    )
    shutil.rmtree(staging, ignore_errors=True)
    rendered = portrait_summary.success if portrait_summary else 0
    inherited = portrait_summary.skipped if portrait_summary else 0
    failed = portrait_summary.failed if portrait_summary else 0
    # 有立绘渲染失败的版本不得标完成/可发布：记 failed 供重跑恢复（runner
    # 的 failed/stale 条目会在重跑时重试）。
    status = "completed" if failed == 0 else "failed"
    error = f"{failed} 套立绘渲染失败" if failed else None
    _record_progress(
        version_dir,
        {
            "version": version,
            "status": status,
            "full_scope": full_scope and failed == 0,
            "scope": scope,
            "rendered_skins": rendered,
            "inherited_skins": inherited,
            "failed_skins": failed,
            "cards": card_report.created,
            **({"error": error} if error else {}),
        },
    )
    return BackfillVersionResult(
        version=version, status=status, rendered=rendered, inherited=inherited,
        cards=card_report.created, error=error,
    )


# ---- 发布 --------------------------------------------------------------------


def validate_backfill_version(version_dir: Path) -> tuple[str, ...]:
    """合入生产前的完整校验：角色数据、card manifest（含覆盖性）与立绘产物。"""
    version_dir = Path(version_dir)
    problems: list[str] = []
    characters = _load_characters(version_dir)
    if not characters:
        problems.append("character_data/current.json 缺失或无角色数据")
    manifest = load_card_manifest(version_dir / "character_cards")
    if manifest is None:
        problems.append("character_cards/manifest.json 缺失或损坏")
    else:
        problems.extend(validate_card_manifest(manifest, version_dir / "character_cards"))
        covered = {str(entry.get("character_id")) for entry in manifest.get("characters", []) if isinstance(entry, Mapping)}
        missing = set(characters) - covered
        if missing:
            problems.append(f"card manifest 未覆盖角色: {sorted(missing)[:5]}")
    portraits_manifest_path = version_dir / "portraits" / "manifest.json"
    try:
        portraits = json.loads(portraits_manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        portraits = None
    if not isinstance(portraits, dict):
        problems.append("portraits/manifest.json 缺失或不是对象")
    else:
        skins = portraits.get("skins")
        if not isinstance(skins, list):
            problems.append("portraits/manifest.json skins 缺失或不是列表")
        else:
            for entry in skins:
                if not isinstance(entry, Mapping):
                    problems.append("portraits/manifest.json skins 含非对象条目")
                    continue
                artifact_path = str(entry.get("artifact_path") or "")
                if not artifact_path:
                    continue
                relative = Path(artifact_path)
                if relative.is_absolute() or ".." in relative.parts:
                    problems.append(f"skin {entry.get('skin_id')}: 非法路径 {artifact_path}")
                    continue
                artifact = version_dir / "portraits" / relative
                if not artifact.is_file():
                    problems.append(f"skin {entry.get('skin_id')}: 产物缺失 {artifact_path}")
                    continue
                sha = str(entry.get("artifact_sha256") or "")
                if sha and hashlib.sha256(artifact.read_bytes()).hexdigest() != sha:
                    problems.append(f"skin {entry.get('skin_id')}: 产物哈希不符 {artifact_path}")
    return tuple(problems)


def _validate_publish_scope(version_dir: Path) -> tuple[str, ...]:
    """发布保护：只接受全量范围、零失败立绘的完成记录（过滤样本不得发布）。"""
    try:
        payload = json.loads((version_dir / PROGRESS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return (f"{PROGRESS_NAME} 缺失或损坏",)
    if not isinstance(payload, dict):
        return (f"{PROGRESS_NAME} 不是对象",)
    if payload.get("status") != "completed":
        return (f"回填状态为 {payload.get('status')!r}，禁止发布",)
    if payload.get("full_scope") is not True:
        return ("回填范围为过滤样本（full_scope=False），禁止发布",)
    try:
        failed_skins = int(payload.get("failed_skins") or 0)
    except (TypeError, ValueError):
        failed_skins = -1
    if failed_skins != 0:
        return (f"存在 {failed_skins} 套失败立绘，禁止发布",)
    return ()


def _rebuild_manifest_from_production(prod_dir: Path, version: int) -> dict[str, Any] | None:
    """已存在生产版本的派生 card manifest：只按生产实际 PNG 与角色数据重建。

    绝不引用工作根产物；source_version 统一记为当版（旧版本无继承信息，
    内容修订链仍由 data/artifact 哈希正确识别）。
    """
    characters = _load_characters(prod_dir)
    if not characters:
        return None
    cards_dir = prod_dir / "character_cards"
    files = sorted(cards_dir.glob("*.png")) if cards_dir.is_dir() else ()
    by_character: dict[str, list[Path]] = {}
    for path in files:
        by_character.setdefault(path.name.split("_", 1)[0], []).append(path)
    outputs = read_version_card_outputs(prod_dir)
    entries: list[dict[str, Any]] = []
    for raw_id, data in characters.items():
        character_id = str(raw_id)
        candidates = by_character.get(safe_role_name(character_id)) or ()
        artifact = resolve_character_artifact(candidates, outputs)
        if artifact is None:
            if len(candidates) > 1:
                LOGGER.warning(
                    "stage=history_publish status=ambiguous_skipped character=%s candidates=%s",
                    character_id,
                    [path.name for path in candidates],
                )
            continue
        entries.append({
            "character_id": character_id,
            "state": "published",
            "artifact_path": f"character_cards/{artifact.name}",
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "data_sha256": canonical_character_sha(data),
            "source_version": str(version),
            "error": None,
        })
    if not entries:
        return None
    entries.sort(key=lambda entry: (0, int(entry["character_id"])) if entry["character_id"].isdecimal() else (1, entry["character_id"]))
    return {"schema_version": 1, "version": str(version), "characters": entries}


def _merge_card_manifest(prod_dir: Path, version: int) -> bool:
    """生产已存在版本只允许补缺失的派生 card manifest；其余一律不动。"""
    cards_dir = prod_dir / "character_cards"
    if load_card_manifest(cards_dir) is not None:
        return False
    manifest = _rebuild_manifest_from_production(prod_dir, version)
    if manifest is None:
        LOGGER.warning("stage=history_publish version=%s status=skipped reason=production_manifest_rebuild_failed", version)
        return False
    problems = validate_card_manifest(manifest, cards_dir)
    if problems:
        LOGGER.warning(
            "stage=history_publish version=%s status=skipped reason=production_manifest_invalid problems=%s",
            version,
            list(problems)[:3],
        )
        return False
    save_card_manifest(cards_dir, manifest)
    LOGGER.info("stage=history_publish version=%s status=merged reason=card_manifest_backfilled", version)
    return True


def _publish_version_dir(work_dir: Path, prod_dir: Path) -> None:
    """同文件系统临时目录整体复制后原子改名；中断遗留临时目录下次清理。"""
    prod_versions = prod_dir.parent
    prod_versions.mkdir(parents=True, exist_ok=True)
    for stale in prod_versions.glob(f".merge-{prod_dir.name}-*"):
        shutil.rmtree(stale, ignore_errors=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".merge-{prod_dir.name}-", dir=prod_versions))
    try:
        shutil.copytree(work_dir, temporary / "content")
        os.replace(temporary / "content", prod_dir)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def publish_backfill(
    *,
    work_root: str | Path,
    production_data_dir: str | Path,
    versions: Sequence[int] | None = None,
) -> PublishReport:
    """把已校验的工作根版本合入生产版本目录；生产其余状态一概不动。"""
    work_root = Path(work_root)
    production = Path(production_data_dir)
    work_versions = work_root / "versions"
    prod_versions = production / "versions"
    wanted = {int(v) for v in versions} if versions is not None else None

    candidates = sorted(
        {int(d.name) for d in _numeric_version_dirs(work_versions)}
        | {int(d.name) for d in _numeric_version_dirs(prod_versions)}
    )
    published: list[int] = []
    merged: list[int] = []
    rejected: list[int] = []
    for version in candidates:
        if wanted is not None and version not in wanted:
            continue
        prod_dir = prod_versions / str(version)
        work_dir = work_versions / str(version)
        if prod_dir.is_dir():
            if _merge_card_manifest(prod_dir, version):
                merged.append(version)
            continue
        if not work_dir.is_dir():
            continue
        problems = validate_backfill_version(work_dir) + _validate_publish_scope(work_dir)
        if problems:
            LOGGER.warning(
                "stage=history_publish version=%s status=rejected problems=%s",
                version,
                list(problems)[:3],
            )
            rejected.append(version)
            continue
        _publish_version_dir(work_dir, prod_dir)
        published.append(version)
        LOGGER.info("stage=history_publish version=%s status=published", version)
    return PublishReport(
        published=tuple(published), merged=tuple(merged), rejected=tuple(rejected)
    )
