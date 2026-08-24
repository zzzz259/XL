"""导入结果的后处理注册表，不使用全局事件总线。"""

from __future__ import annotations

from collections.abc import Iterable

from app.shared.contracts import ImportResult


class PostProcessorRegistry:
    """记录哪些结果分类需要后处理，并提供稳定的待处理列表。"""

    def __init__(self, categories: Iterable[str] = ()):
        self._categories = {str(category) for category in categories}

    def register(self, category: str) -> None:
        if not str(category).strip():
            raise ValueError("后处理分类不能为空")
        self._categories.add(str(category))

    def pending(self, result: ImportResult | None) -> frozenset[str]:
        if result is None:
            return frozenset()
        return frozenset(
            category
            for category in result.postprocess_categories
            if category in self._categories
        )

    def handles(self, category: str) -> bool:
        return str(category) in self._categories
