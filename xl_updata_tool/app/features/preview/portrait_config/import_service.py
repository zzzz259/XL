"""Generate version-bound portrait scenes during preview post-processing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path

from app.platform.diagnostics import logger

from .asset_index import AssetIndex
from .builder import PARSER_VERSION, build_portrait_scenes
from .cache import PortraitSceneCache
from .lua_config import load_eventcover_roots, load_portrait_roots
from .model import PortraitScene, SceneDiagnostic


@dataclass(frozen=True)
class PortraitImportSummary:
    status: str
    roots: int = 0
    complete: int = 0
    incomplete: int = 0
    cache_hits: int = 0
    diagnostic: str = ""
    not_applicable: int = 0


def generate_portrait_manifests(
    version_root: Path, version_timestamp: int, catalog_path: Path, lua_root: Path,
    bundle_root: Path, *, cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> PortraitImportSummary:
    """Build scenes off-thread; unavailable derived inputs never break import."""
    version_root, catalog_path, lua_root, bundle_root = map(
        Path, (version_root, catalog_path, lua_root, bundle_root),
    )
    if cancel_check and cancel_check():
        return PortraitImportSummary("cancelled")
    if not catalog_path.is_file() or not lua_root.is_dir() or not bundle_root.is_dir():
        return PortraitImportSummary("skipped", diagnostic="portrait_inputs_missing")
    expected_catalog = bundle_root / "_map" / "assets_map.json"
    if (
        bundle_root.name != str(int(version_timestamp))
        or bundle_root.parent.name != "bundles"
        or version_root.resolve() != bundle_root.parent.parent.resolve()
        or catalog_path.resolve() != expected_catalog.resolve()
    ):
        return PortraitImportSummary("skipped", diagnostic="portrait_version_path_mismatch")
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
        catalog_version = payload.get("version_timestamp") if isinstance(payload, dict) else None
        if catalog_version is not None and int(catalog_version) != int(version_timestamp):
            return PortraitImportSummary("skipped", diagnostic="portrait_catalog_version_mismatch")
        if not isinstance(payload, (list, dict)):
            return PortraitImportSummary("skipped", diagnostic="portrait_catalog_invalid")
        roots = [
            *load_portrait_roots(lua_root, int(version_timestamp)),
            *load_eventcover_roots(lua_root, int(version_timestamp)),
        ]
        roots = [root for root in roots
                 if getattr(root, "unresolved_reason", None) != "missing_show_spine"]
        index = AssetIndex.from_catalog(
            catalog_path, bundle_root, int(version_timestamp),
            cab_cache_path=version_root / "cache" / "portrait-cab" / f"{int(version_timestamp)}.json",
        )
    except Exception as exc:
        logger.exception("立绘场景输入解析失败", extra={
            "event": "portrait_import.inputs_invalid",
            "error_code": "PORTRAIT_INPUTS_INVALID",
            "details": {
                "version": int(version_timestamp), "catalog": str(catalog_path),
                "lua_root": str(lua_root), "bundle_root": str(bundle_root),
                "error_type": type(exc).__name__,
            },
        })
        return PortraitImportSummary("skipped", diagnostic="portrait_inputs_invalid")
    if not roots:
        return PortraitImportSummary("skipped", diagnostic="portrait_roots_empty")

    cache = PortraitSceneCache(
        version_root / "cache" / "portrait-scenes", parser_version=PARSER_VERSION,
    )
    indexed_bundles = {asset.bundle_hash for asset in index.assets}
    present_bundles = {asset.bundle_hash for asset in index.assets if asset.bundle_present}
    missing_bundles = sorted(indexed_bundles - present_bundles)
    log_inputs = logger.warning if missing_bundles else logger.info
    log_inputs(
        "立绘场景输入验证 version=%s roots=%s assets=%s bundles=%s present=%s missing=%s",
        version_timestamp, len(roots), len(index.assets), len(indexed_bundles),
        len(present_bundles), len(missing_bundles), extra={
            "event": "portrait_import.inputs_verified", "details": {
                "version": int(version_timestamp), "roots": len(roots),
                "assets": len(index.assets), "bundles": len(indexed_bundles),
                "present_bundles": len(present_bundles),
                "missing_bundle_hashes": missing_bundles,
            },
        },
    )
    hits = [0]
    cache_hit_skins: set[str] = set()
    complete = incomplete = processed = 0
    not_applicable = 0
    for i, root in enumerate(roots, 1):
        if cancel_check and cancel_check():
            break
        try:
            scenes = build_portrait_scenes(
                int(version_timestamp), [root], index, cache=cache,
                cache_hit_callback=lambda skin: (
                    hits.__setitem__(0, hits[0] + 1), cache_hit_skins.add(skin),
                ),
            )
            for scene in scenes:
                errors = [item for item in scene.diagnostics if item.severity == "error"]
                excluded = dict(getattr(scene, "config_metadata", ())).get("export_status") == "not_applicable"
                status = "not_applicable" if excluded else ("incomplete" if errors else "complete")
                if errors and isinstance(scene, PortraitScene):
                    cache.store_snapshot(scene)
                if excluded:
                    not_applicable += 1
                elif errors:
                    incomplete += 1
                else:
                    complete += 1
                logger.log(
                    30 if errors else 20,
                    "立绘场景皮肤结果 status=%s skin=%s character=%s parts=%s cache_hit=%s",
                    status, scene.skin_id, scene.character_id, len(scene.parts),
                    scene.skin_id in cache_hit_skins,
                    extra={"event": f"portrait_import.scene.{status}", "details": {
                        "version": int(version_timestamp), "skin_id": scene.skin_id,
                        "character_id": scene.character_id,
                        "prefab_container": scene.prefab_container,
                        "part_count": len(scene.parts),
                        "cache_hit": scene.skin_id in cache_hit_skins,
                        "diagnostics": [
                            {"code": item.code, "message": item.message,
                             "source": (f"{item.source.bundle_hash}:{item.source.path_id}"
                                        if item.source else "")}
                            for item in scene.diagnostics
                        ],
                    }},
                )
        except Exception as error:
            incomplete += 1
            logger.exception("立绘场景皮肤构建异常 skin=%s", root.skin_id, extra={
                "event": "portrait_import.scene.failed", "error_code": "PORTRAIT_SCENE_BUILD_FAILED",
                "details": {"version": int(version_timestamp), "skin_id": root.skin_id},
            })
            metadata = dict(getattr(root, "metadata", ()))
            failed_scene = PortraitScene(
                game_version=str(version_timestamp), version_timestamp=str(version_timestamp),
                entry_config_id=metadata.get("entry_id", root.skin_id),
                character_id=metadata.get("card_id", ""), skin_id=root.skin_id,
                prefab=None, source_bundles=(), parts=(),
                prefab_container=str(getattr(root, "prefab_name", "") or ""),
                diagnostics=(SceneDiagnostic(
                    "scene_build_failed", f"{type(error).__name__}: {error}",
                    details=(("evidence", getattr(root, "evidence", "")),),
                ),),
            )
            cache.store_snapshot(failed_scene)
        processed += 1
        if progress_callback:
            progress_callback(root.skin_id, i, len(roots))
    if cancel_check and cancel_check():
        return PortraitImportSummary("cancelled", processed, complete, incomplete, hits[0], not_applicable=not_applicable)
    return PortraitImportSummary(
        "partial" if incomplete else "complete", processed, complete, incomplete, hits[0],
        not_applicable=not_applicable,
    )
