"""应用组合根的迁移期骨架。

当前仍由旧 MainWindow 负责实际页面装配；本模块先提供稳定的 Feature
注册协议。后续各 Feature 完成迁移后，只需在这里注册工厂，不再把领域
依赖继续添加到 MainWindow。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.shared.contracts import FeatureDescriptor, FeatureRuntime

from .context import AppContext

FeatureFactory = Callable[[AppContext], FeatureRuntime]


@dataclass(frozen=True)
class FeatureDefinition:
    """Feature 的注册描述和延迟装配工厂。"""

    descriptor: FeatureDescriptor
    factory: FeatureFactory


def create_features(
    context: AppContext,
    definitions: Iterable[FeatureDefinition] = (),
) -> tuple[FeatureRuntime, ...]:
    """按注册顺序创建 Feature，并拒绝重复的稳定 key。"""

    features: list[FeatureRuntime] = []
    keys: set[str] = set()
    for definition in definitions:
        key = definition.descriptor.key
        if key in keys:
            raise ValueError(f"Duplicate feature key: {key}")
        feature = definition.factory(context)
        if feature.descriptor != definition.descriptor:
            raise ValueError(f"Feature factory returned unexpected descriptor: {key}")
        keys.add(key)
        features.append(feature)
    return tuple(features)
