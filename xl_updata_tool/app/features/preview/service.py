"""Preview 目录服务，不依赖 Qt。"""

from __future__ import annotations

import os
from pathlib import Path

from app.core.preview_catalog import (
    build_skel_map,
    scan_cardspine_roles,
    scan_preview_roles,
)


class PreviewService:
    """集中处理预览素材目录、角色索引和最终图片目录。"""

    def __init__(self, material_dir: str | os.PathLike[str], preview_dir: str | os.PathLike[str]):
        self.material_dir = Path(material_dir)
        self.preview_dir = Path(preview_dir)

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
