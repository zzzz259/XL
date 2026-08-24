"""Feature 边界的最小共享契约。

这些类型只描述跨层通信，不实现任何具体功能，也不依赖 Qt。具体 Feature
可以在自己的目录中扩展它们，但 Shell 不应反向依赖 Feature 内部实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
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


class ShellPort(Protocol):
    """Feature/Composition Root 面向桌面 Shell 的最小宿主端口。"""

    def set_status(self, message: str) -> None:
        """展示通用状态消息。"""

    def set_progress(self, current: int, total: int, message: str) -> None:
        """展示通用进度。"""

    def schedule(self, delay_ms: int, callback: Callable[[], None]) -> None:
        """在 Shell 所属 UI 线程调度回调。"""

    def show_warning(self, title: str, message: str) -> None:
        """展示警告。"""

    def show_information(self, title: str, message: str) -> None:
        """展示提示。"""

    def confirm(self, title: str, message: str) -> bool:
        """请求用户确认。"""

    def create_progress_dialog(self, label: str, cancel_text: str) -> object:
        """创建 Shell 托管的通用进度对话框。"""


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
    """一个已经装配完成、可交给 Shell 注册的 Feature。

    三个 signal 字段是可选的 opaque ports。shared/bootstrap 不知道 Qt
    signal 的具体类型，Shell 只通过它们绑定通用状态、进度和角标。
    """

    descriptor: FeatureDescriptor
    page: PagePort
    controller: ControllerPort
    status_signal: object | None = None
    progress_signal: object | None = None
    badge_signal: object | None = None
    badge_state: Callable[[], bool] | None = None


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
