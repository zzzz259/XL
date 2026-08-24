"""Feature 边界的最小共享契约。

这些类型只描述跨层通信，不实现任何具体功能，也不依赖 Qt。具体 Feature
可以在自己的目录中扩展它们，但 Shell 不应反向依赖 Feature 内部实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class PagePort(Protocol):
    """页面向 Controller 暴露的最小展示端口。"""

    def set_loading(self, loading: bool) -> None:
        """切换页面的忙碌状态。"""


class ControllerPort(Protocol):
    """Feature 控制器的生命周期端口。"""

    def start(self) -> None:
        """连接页面信号并开始控制器生命周期。"""

    def stop(self) -> None:
        """断开信号并释放控制器资源。"""


class ServicePort(Protocol):
    """应用服务的可选释放端口。"""

    def close(self) -> None:
        """释放服务持有的资源。"""


class WorkerPort(Protocol):
    """后台任务的取消端口；Qt Worker 通过适配实现它。"""

    def cancel(self) -> None:
        """请求任务取消。"""


@dataclass(frozen=True)
class FeatureDescriptor:
    """Shell 注册 Feature 所需的稳定元数据。"""

    key: str
    title: str
    icon: str = ""

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("Feature key must not be empty")
        if not self.title.strip():
            raise ValueError("Feature title must not be empty")


@dataclass(frozen=True)
class FeatureRuntime:
    """一个已经装配完成、可交给 Shell 注册的 Feature。"""

    descriptor: FeatureDescriptor
    page: PagePort
    controller: ControllerPort


@dataclass(frozen=True)
class ImportResult:
    """导入流程与后处理器之间的稳定结果契约。"""

    categories: frozenset[str] = field(default_factory=frozenset)
    completed_categories: frozenset[str] = field(default_factory=frozenset)
    failed_categories: frozenset[str] = field(default_factory=frozenset)
    published_outputs: tuple[str, ...] = ()
    cancelled: bool = False
    message: str = ""
    lua_export_result: object | None = None
    postprocess_categories: frozenset[str] = field(default_factory=frozenset)

    def has_category(self, category: str) -> bool:
        """返回本次导入是否包含指定分类。"""
        return category in self.categories

    @property
    def succeeded(self) -> bool:
        """返回所有请求分类是否完成且任务未被取消。"""
        return (
            bool(self.categories)
            and self.categories <= self.completed_categories
            and not self.failed_categories
            and not self.cancelled
        )
