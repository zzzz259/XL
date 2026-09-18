"""Preview 目录服务，不依赖 Qt。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .catalog import (
    build_skel_map,
    scan_cardspine_roles,
    scan_preview_roles,
)
from .resource_catalog import discover_preview_resources
from .resource_state import PreviewResourceState
from .output_publisher import RawSpinePublishSummary, publish_raw_spine_resources
from .fgui_atlas import UIPackageTool
from .material_catalog import (
    GameMaterialCatalog,
    MaterialExportSummary,
    discover_game_materials as discover_materials,
    export_game_materials as export_materials,
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

    def preprocess_preview_resources(self, progress_callback=None, cancel_check=None) -> PreviewPreprocessSummary:
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
        material_summary = self.export_game_materials(material_catalog)
        progress(3, 4, "写入图片预览资源索引")
        progress(4, 4, "图片资源预处理完成")
        return PreviewPreprocessSummary(catalog, spine_summary, material_summary)

    def discover_game_materials(self, metadata=None) -> GameMaterialCatalog:
        return discover_materials(self.material_dir, metadata)

    def export_game_materials(
        self,
        catalog: GameMaterialCatalog | None = None,
        splitter=None,
    ) -> MaterialExportSummary:
        return export_materials(
            catalog or self.discover_game_materials(),
            self.preview_dir.parent,
            splitter if splitter is not None else UIPackageTool.split_atlas_to_package_dir,
        )
