"""导入功能域的无 Qt 服务。"""

from __future__ import annotations

import os
from pathlib import Path

from app.core.bundle_selector import select_audio_bundles, select_lua_bundles
from app.shared.contracts import ImportResult

from .spec import CATEGORY_DIRS, normalise_categories


class ImporterService:
    """负责导入前的分类、Bundle 筛选和跨层结果组装。"""

    selectors = {"lua": select_lua_bundles, "audio": select_audio_bundles}

    def __init__(self, material_dir: str | os.PathLike[str], lua_output_dir: str | os.PathLike[str]):
        self.material_dir = Path(material_dir)
        self.lua_output_dir = Path(lua_output_dir)

    @staticmethod
    def categories(categories) -> frozenset[str]:
        return normalise_categories(categories)

    def select_bundles(self, category: str, bundle_paths, map_path):
        """按资源映射筛选 Bundle；映射不可用时返回兼容回退结果。"""
        category = next(iter(self.categories({category})))
        selector = self.selectors.get(category)
        if selector is None:
            return list(bundle_paths), False, 0
        return selector(list(bundle_paths), map_path)

    def result(
        self,
        categories,
        completed_categories=(),
        failed_categories=(),
        published_outputs=(),
        *,
        cancelled=False,
        message="",
        lua_export_result=None,
    ) -> ImportResult:
        selected = self.categories(categories)
        if not selected:
            # 兼容旧入口：未勾选分类表示执行完整导入。
            selected = frozenset({"all"})
        completed = frozenset(completed_categories)
        failed = frozenset(failed_categories)
        postprocess = frozenset(
            category
            for category in ("lua", "audio")
            if category in selected and category in completed
        )
        return ImportResult(
            categories=selected,
            completed_categories=completed,
            failed_categories=failed,
            published_outputs=tuple(str(path) for path in published_outputs),
            cancelled=bool(cancelled),
            message=str(message or ""),
            lua_export_result=lua_export_result,
            postprocess_categories=postprocess,
        )

    @staticmethod
    def category_for_material_path(relative_path: str) -> str | None:
        value = str(relative_path).replace("\\", "/").strip("/")
        for category, directory in CATEGORY_DIRS.items():
            if value == directory or value.startswith(f"{directory}/"):
                return category
        return None
