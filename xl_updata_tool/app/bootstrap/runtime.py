"""与 Qt 无关的 Feature Runtime 注册、激活和通用信号绑定。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.shared.contracts import FeatureRuntime


@dataclass(frozen=True)
class ApplicationRuntime:
    """应用级组合结果，Shell 只接收这个 opaque runtime。"""

    context: object
    registry: "FeatureRuntimeRegistry"
    postprocessor_registry: object
    import_workflow: object

    @property
    def features(self) -> tuple[FeatureRuntime, ...]:
        return self.registry.features

    def feature(self, key: str) -> FeatureRuntime:
        return self.registry.get(key)


class FeatureRuntimeRegistry:
    """为 Shell 提供不认识具体领域名称的 Feature 生命周期门面。"""

    def __init__(self, features: Iterable[FeatureRuntime]):
        self._features = tuple(features)
        self._by_key = {}
        for feature in self._features:
            key = feature.descriptor.key
            if key in self._by_key:
                raise ValueError(f"Duplicate feature key: {key}")
            self._by_key[key] = feature

    @property
    def features(self) -> tuple[FeatureRuntime, ...]:
        return self._features

    def get(self, key: str) -> FeatureRuntime:
        try:
            return self._by_key[key]
        except KeyError as error:
            raise KeyError(f"Unknown feature key: {key}") from error

    def keys(self) -> tuple[str, ...]:
        return tuple(feature.descriptor.key for feature in self._features)

    def activate(self, key: str) -> None:
        """显示目标页面并隐藏其余页面；无 Qt 页面保持 opaque。"""
        for feature in self._features:
            page = feature.page
            visible = feature.descriptor.key == key
            setter = getattr(page, "set_visible", None) or getattr(page, "setVisible", None)
            if setter is not None:
                setter(visible)

    def bind_status(self, handler: Callable[[str], None]) -> None:
        self._bind("status_signal", handler)

    def bind_progress(self, handler: Callable[..., None]) -> None:
        self._bind("progress_signal", handler)

    def bind_badge(self, handler: Callable[..., None]) -> None:
        self._bind("badge_signal", handler)

    def _bind(self, attribute: str, handler: Callable[..., None]) -> None:
        for feature in self._features:
            signal = getattr(feature, attribute)
            if signal is not None:
                connect = getattr(signal, "connect", None)
                if connect is None:
                    raise TypeError(f"{attribute} must expose connect(): {feature.descriptor.key}")
                connect(handler)

    def close(self) -> None:
        """按注册顺序释放 Feature 持有的任务和资源。"""
        for feature in reversed(self._features):
            controller = feature.controller
            closer = getattr(controller, "close", None) or getattr(controller, "stop", None)
            if closer is not None:
                closer()
