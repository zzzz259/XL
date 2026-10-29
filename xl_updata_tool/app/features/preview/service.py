"""Preview 目录服务，不依赖 Qt。"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, replace
from pathlib import Path

from app.platform.diagnostics import logger

from .catalog import (
    build_skel_map,
    scan_cardspine_roles,
    scan_preview_roles,
)
from .resource_catalog import (
    discover_preview_resources, display_skin_name, load_portrait_scene_catalog,
)
from .resource_model import PreviewResourceCatalog, SpineSkinRecord
from .resource_state import PreviewResourceState
from .output_publisher import RawSpinePublishSummary, publish_raw_spine_resources
from .portrait_config.import_service import PortraitImportSummary, generate_portrait_manifests
from .portrait_config.asset_index import AssetIndex
from .portrait_config.builder import build_portrait_scenes
from .portrait_config.lua_config import load_portrait_roots, load_eventcover_roots
from .portrait_package import (
    PortraitPackageSummary, load_published_portrait_scenes, publish_portrait_packages,
)
from .fgui_atlas import UIPackageTool
from .material_catalog import (
    GameMaterialCatalog,
    MaterialExportSummary,
    discover_game_materials as discover_materials,
    export_game_materials as export_materials,
    discover_processed_game_materials as discover_processed_materials,
)


@dataclass(frozen=True, slots=True)
class PreviewPreprocessSummary:
    catalog: object
    spine: RawSpinePublishSummary
    materials: MaterialExportSummary
    portrait: PortraitImportSummary | None = None
    packages: PortraitPackageSummary | None = None

    @property
    def partial(self):
        return bool(self.spine.diagnostics or self.materials.failed
                    or (self.portrait and self.portrait.status in {'partial', 'skipped'})
                    or (self.packages and (self.packages.failed or self.packages.cancelled
                                         or self.packages.metadata_incomplete)))


class PreviewService:
    """集中处理预览素材目录、角色索引和最终图片目录。"""

    def __init__(self, material_dir: str | os.PathLike[str], preview_dir: str | os.PathLike[str]):
        self.material_dir = Path(material_dir)
        self.preview_dir = Path(preview_dir)
        self._resource_state: PreviewResourceState | None = None

    @property
    def resource_state(self) -> PreviewResourceState:
        if self._resource_state is None:
            self._resource_state = PreviewResourceState(self.preview_dir.parent / "preview_state.json")
        return self._resource_state

    def ensure_output_dir(self) -> Path:
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        return self.preview_dir

    def image_paths(self) -> list[str]:
        self.ensure_output_dir()
        return sorted(
            str(path)
            for path in self.preview_dir.rglob("*.png")
            if path.is_file()
        )

    def has_images(self) -> bool:
        return bool(self.image_paths())

    def skel_map(self) -> dict[str, tuple[str, str]]:
        return build_skel_map(str(self.material_dir))

    def cardspine_roles(self) -> list[str]:
        return scan_cardspine_roles(
            str(self.material_dir / "assets" / "art" / "models" / "cardspine")
        )

    def preview_roles(self) -> list[str]:
        return scan_preview_roles(str(self.preview_dir))

    def discover_preview_resources(self, character_data=None, query_runner=None):
        """Discover Spine files and their internal skins from the staging tree."""
        return discover_preview_resources(
            self.material_dir,
            character_data=character_data,
            query_runner=query_runner,
        )

    def publish_raw_spine_resources(self, catalog=None) -> RawSpinePublishSummary:
        """Publish discovered source Spine groups without removing staging data."""
        return publish_raw_spine_resources(
            catalog or self.discover_preview_resources(),
            self.material_dir,
            self.preview_dir.parent,
        )

    def preprocess_preview_resources(
        self,
        progress_callback=None,
        cancel_check=None,
        detail_progress_callback=None,
        import_result=None,
    ) -> PreviewPreprocessSummary:
        """Build all non-user-selected preview outputs after AS import."""
        progress = progress_callback or (lambda _current, _total, _message: None)
        cancelled = cancel_check or (lambda: False)
        progress(0, 5, "发现角色 Spine 和皮肤")
        catalog = self.discover_preview_resources()
        selected_version = getattr(import_result, "version_timestamp", None)
        lua_result = getattr(import_result, "lua_export_result", None)
        if selected_version is None and isinstance(lua_result, dict):
            selected_version = lua_result.get("version")
        if cancelled():
            return PreviewPreprocessSummary(catalog, RawSpinePublishSummary(), MaterialExportSummary())

        progress(1, 5, "发布原始 Spine 资源")
        spine_summary = self.publish_raw_spine_resources(catalog)
        if cancelled():
            return PreviewPreprocessSummary(catalog, spine_summary, MaterialExportSummary())

        progress(2, 5, "切割游戏图集和大头照")
        material_catalog = self.discover_game_materials()
        material_summary = self.export_game_materials(
            material_catalog,
            progress_callback=detail_progress_callback,
        )
        logger.info(
            "图片素材预处理: BurstHead=%s, 图集=%s, 独立素材=%s, 导出成功=%s, 导出失败=%s",
            len(material_catalog.burst_heads),
            len(material_catalog.atlases),
            len(material_catalog.standalone),
            material_summary.exported,
            material_summary.failed,
        )
        if cancelled():
            return PreviewPreprocessSummary(catalog, spine_summary, material_summary)
        progress(3, 5, "写入图片预览资源索引")
        portrait_summary = None
        package_summary = None
        lua_result = getattr(import_result, "lua_export_result", None)
        completed = getattr(import_result, "completed_categories", frozenset())
        selected_version = getattr(import_result, "version_timestamp", None)
        lua_version = lua_result.get("version") if isinstance(lua_result, dict) else None
        version = selected_version or lua_version
        if (
            import_result is not None
            and not import_result.cancelled
            and {"all", "character"} & completed
            and version
        ):
            version = int(version)
            if lua_version and int(lua_version) != version:
                raise ValueError("立绘场景配置与本次导入版本不一致")
            bundle_root = self.material_dir.parent / "bundles" / str(version)
            catalog_path = bundle_root / "_map" / "assets_map.json"
            lua_version_dir = (
                Path(lua_result["directory"])
                if isinstance(lua_result, dict) and lua_result.get("directory")
                else self.preview_dir.parent / "lua" / str(version)
            )
            if lua_version_dir.name != str(version):
                raise ValueError("立绘场景 Lua 目录与本次导入版本不一致")
            logger.info("立绘场景清单阶段开始 version=%s", version, extra={
                "event": "portrait_import.start", "details": {
                    "version": version, "catalog": str(catalog_path),
                    "lua_root": str(lua_version_dir.parent), "bundle_root": str(bundle_root),
                },
            })
            progress(4, 5, "根据游戏配置生成立绘场景清单")
            portrait_summary = generate_portrait_manifests(
                self.material_dir.parent, version, catalog_path, lua_version_dir.parent,
                bundle_root, cancel_check=cancelled,
                progress_callback=lambda skin, current, total: (
                    detail_progress_callback(current, total, f"立绘场景 · {skin}")
                    if detail_progress_callback else None
                ),
            )
            if portrait_summary.status in {"complete", "partial"} and not cancelled():
                scenes, _diagnostics = load_portrait_scene_catalog(
                    self.material_dir.parent / "cache" / "portrait-scenes",
                )
                selected = tuple(scene for scene in scenes
                                 if str(scene.version_timestamp) == str(version) and scene.parts
                                 and dict(scene.config_metadata).get("export_status") != "not_applicable")
                if selected:
                    index = AssetIndex.from_catalog(
                        catalog_path, bundle_root, version,
                        cab_cache_path=self.material_dir.parent / 'cache' / 'portrait-cab' / f'{version}.json',
                    )
                    package_summary = publish_portrait_packages(
                        selected, index, self.material_dir, self.preview_dir.parent,
                        cancel_check=cancelled,
                        progress_callback=lambda event: logger.log(
                            30 if event["status"] == "failed" else 20,
                            "立绘皮肤包发布: %s", event,
                            extra={"event": "portrait_package." + event["status"], "details": event},
                        ),
                    )
            if portrait_summary.status == "cancelled":
                logger.info("立绘场景清单阶段取消", extra={
                    "event": "portrait_import.cancelled",
                    "details": {"roots": portrait_summary.roots},
                })
            elif portrait_summary.status == "skipped":
                logger.warning("立绘场景清单阶段跳过 diagnostic=%s", portrait_summary.diagnostic,
                               extra={"event": "portrait_import.skipped",
                                      "error_code": portrait_summary.diagnostic})
            elif portrait_summary.status == "partial":
                logger.warning(
                    "立绘场景清单部分完成 roots=%s complete=%s incomplete=%s cache_hits=%s",
                    portrait_summary.roots, portrait_summary.complete,
                    portrait_summary.incomplete, portrait_summary.cache_hits,
                    extra={"event": "portrait_import.partial", "details": {
                        "roots": portrait_summary.roots,
                        "complete": portrait_summary.complete,
                        "incomplete": portrait_summary.incomplete,
                        "cache_hits": portrait_summary.cache_hits,
                    }},
                )
            else:
                logger.info("立绘场景清单阶段完成 roots=%s complete=%s incomplete=%s cache_hits=%s",
                            portrait_summary.roots, portrait_summary.complete,
                            portrait_summary.incomplete, portrait_summary.cache_hits,
                            extra={"event": "portrait_import.complete", "details": {
                                "roots": portrait_summary.roots,
                                "complete": portrait_summary.complete,
                                "incomplete": portrait_summary.incomplete,
                                "cache_hits": portrait_summary.cache_hits,
                            }})
        if (
            import_result is not None and not import_result.cancelled
            and {"all", "character"} & completed and not version
        ):
            portrait_summary = PortraitImportSummary("skipped", diagnostic="portrait_version_context_missing")
            logger.warning("立绘场景未生成：导入结果缺少版本上下文", extra={
                "event": "portrait_import.skipped", "error_code": "portrait_version_context_missing",
            })
        progress(5, 5, "图片资源预处理完成")
        return PreviewPreprocessSummary(catalog, spine_summary, material_summary, portrait_summary, package_summary)

    def refresh_portrait_scenes(self, scenes, *, cancel_check=None, progress_callback=None):
        """Rebuild only selected exact-version roots without trusting old cached evidence."""
        from .portrait_import import load_battle_roots, refresh_portrait_packages

        cancelled = cancel_check or (lambda: False)
        progress = progress_callback or (lambda _event: None)
        paths, failures = [], []
        metadata_incomplete = 0
        for old_scene in scenes:
            if cancelled():
                break
            try:
                version = int(old_scene.version_timestamp)
                bundle = self.material_dir.parent / 'bundles' / str(version)
                catalog = bundle / '_map' / 'assets_map.json'
                lua_root = self.preview_dir.parent / 'lua'
                lua_version = lua_root / str(version)
                source = lua_version / ('BaseActivityStageConfig.lua'
                                       if old_scene.skin_id.startswith('eventcovers_') else 'BaseFashion.lua')
                if not bundle.is_dir() or not catalog.is_file() or not source.is_file():
                    raise ValueError(f'版本 {version} 缺少同版源 AB、assets_map.json 或 Lua：{bundle}；{source}')
                roots = (load_eventcover_roots(lua_root, version) if old_scene.skin_id.startswith('eventcovers_')
                         else load_battle_roots(lua_root, version) if old_scene.skin_id.startswith('battle_')
                         else load_portrait_roots(lua_root, version))
                roots = [root for root in roots if root.skin_id == old_scene.skin_id]
                if len(roots) != 1:
                    raise ValueError(f'版本 {version} 找不到唯一配置 {old_scene.skin_id}')
                progress({'phase': '检查工程', 'skin_id': old_scene.skin_id, 'status': 'rebuilding'})
                index = AssetIndex.from_catalog(
                    catalog, bundle, version,
                    cab_cache_path=self.material_dir.parent / 'cache' / 'portrait-cab' / f'{version}.json',
                )
                rebuilt = tuple(build_portrait_scenes(version, roots, index, cache=None))
                selected = tuple(scene for scene in rebuilt if scene.skin_id == old_scene.skin_id)
                if len(selected) != 1 or not selected[0].parts:
                    raise ValueError(f'配置 {old_scene.skin_id} 未生成有效场景，保留旧工程')
                if cancelled():
                    break
                result = refresh_portrait_packages(
                    selected, index, bundle, self.material_dir, self.preview_dir.parent,
                    cancel_check=cancelled, progress_callback=progress,
                )
                paths.extend(result.manifest_paths)
                failures.extend(result.failures)
                metadata_incomplete += result.metadata_incomplete
                if result.cancelled:
                    return PortraitPackageSummary(len(paths), len(failures), True, tuple(paths), tuple(failures), metadata_incomplete)
            except InterruptedError:
                break
            except Exception as error:
                failure = {'skin_id': old_scene.skin_id, 'error': str(error)}
                failures.append(failure)
                logger.exception('场景工程更新失败 skin=%s', old_scene.skin_id,
                                 extra={'event': 'portrait_refresh.failed', 'details': failure})
                progress({'phase': '检查工程', 'status': 'failed', **failure})
        return PortraitPackageSummary(len(paths), len(failures), cancelled(), tuple(paths), tuple(failures), metadata_incomplete)

    def discover_game_materials(self, metadata=None) -> GameMaterialCatalog:
        return discover_materials(self.material_dir, metadata)

    def discover_processed_game_materials(self) -> GameMaterialCatalog:
        """Read the final cut-material tree without scanning staging input."""
        return discover_processed_materials(self.preview_dir.parent)

    def load_published_preview_resources(self) -> PreviewResourceCatalog:
        """Load the postprocess index without querying or scanning source assets."""
        index_path = self.preview_dir.parent / "preview_index.json"
        payload = {}
        if index_path.is_file():
            try:
                payload = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                logger.warning("图片预览索引不可读取，将继续加载立绘场景清单: %s", index_path)
        records = []
        for group in payload.get("spine", ()) if isinstance(payload, dict) else ():
            for value in group.get("records", ()) if isinstance(group, dict) else ():
                if not isinstance(value, dict):
                    continue
                try:
                    record = SpineSkinRecord(**value)
                    records.append(
                        replace(
                            record,
                            display_name=display_skin_name(
                                record.source_skel,
                                record.character_id,
                                record.resource_family,
                            ),
                        )
                    )
                except (TypeError, ValueError):
                    continue
        scenes, diagnostics = load_portrait_scene_catalog(
            self.material_dir.parent / "cache" / "portrait-scenes",
        )
        packaged, package_diagnostics = load_published_portrait_scenes(self.preview_dir.parent)
        merged = {(scene.version_timestamp, scene.skin_id): scene for scene in scenes}
        merged.update(((scene.version_timestamp, scene.skin_id), scene) for scene in packaged)
        scenes = tuple(merged.values())
        diagnostics = (*diagnostics, *package_diagnostics)
        for manifest_path, diagnostic in diagnostics:
            logger.warning("立绘场景清单被隔离 path=%s reason=%s", manifest_path, diagnostic, extra={
                "event": "portrait_catalog.manifest_rejected",
                "error_code": "PORTRAIT_MANIFEST_REJECTED",
                "details": {"manifest_path": manifest_path, "reason": diagnostic},
            })
        return PreviewResourceCatalog.from_records(
            records, portrait_scenes=scenes, portrait_diagnostics=diagnostics,
        )

    def export_game_materials(
        self,
        catalog: GameMaterialCatalog | None = None,
        splitter=None,
        progress_callback=None,
    ) -> MaterialExportSummary:
        return export_materials(
            catalog or self.discover_game_materials(),
            self.preview_dir.parent,
            splitter if splitter is not None else UIPackageTool.split_atlas_to_package_dir,
            progress_callback=progress_callback,
        )
