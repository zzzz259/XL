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
from .resource_catalog import discover_preview_resources, display_skin_name
from .resource_model import PreviewResourceCatalog, SpineSkinRecord
from .resource_state import PreviewResourceState
from .output_publisher import RawSpinePublishSummary, publish_raw_spine_resources
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
    ) -> PreviewPreprocessSummary:
        """Build all non-user-selected preview outputs after AS import."""
        progress = progress_callback or (lambda _current, _total, _message: None)
        cancelled = cancel_check or (lambda: False)
        progress(0, 4, "发现角色 Spine 和皮肤")
        catalog = self.discover_preview_resources()
        if cancelled():
            return PreviewPreprocessSummary(catalog, RawSpinePublishSummary(), MaterialExportSummary())

        progress(1, 4, "发布原始 Spine 资源")
        spine_summary = self.publish_raw_spine_resources(catalog)
        if cancelled():
            return PreviewPreprocessSummary(catalog, spine_summary, MaterialExportSummary())

        progress(2, 4, "切割游戏图集和大头照")
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
        if material_summary.failed:
            details = "; ".join(material_summary.diagnostics) or "没有可用的错误详情"
            raise RuntimeError(
                f"游戏素材导出失败（{material_summary.failed} 项）：{details}"
            )
        progress(3, 4, "写入图片预览资源索引")
        progress(4, 4, "图片资源预处理完成")
        return PreviewPreprocessSummary(catalog, spine_summary, material_summary)

    def discover_game_materials(self, metadata=None) -> GameMaterialCatalog:
        return discover_materials(self.material_dir, metadata)

    def discover_processed_game_materials(self) -> GameMaterialCatalog:
        """Read the final cut-material tree without scanning staging input."""
        return discover_processed_materials(self.preview_dir.parent)

    def load_published_preview_resources(self) -> PreviewResourceCatalog:
        """Load the postprocess index without querying or scanning source assets."""
        index_path = self.preview_dir.parent / "preview_index.json"
        if not index_path.is_file():
            return PreviewResourceCatalog.from_records(())
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return PreviewResourceCatalog.from_records(())
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
        return PreviewResourceCatalog.from_records(records)

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
