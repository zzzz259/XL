"""应用组合根与 Feature 注册工厂。

生产 Shell 仍处于迁移期，但具体 Feature 已可由这里集中注册和隔离装配。
P3 再切换生产启动路径，避免在同一阶段同时改变装配和 Shell 行为。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.shared.contracts import FeatureDescriptor, FeatureRuntime

from .context import AppContext
from .runtime import ApplicationRuntime, FeatureRuntimeRegistry

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


def default_feature_definitions(parent=None) -> tuple[FeatureDefinition, ...]:
    """返回 XL 当前五个 Feature 的正式注册顺序。

    `parent` 只由组合根作为 Qt 生命周期宿主传入；共享契约和 AppContext
    不持有 Qt 对象。生产启动切换前，MainWindow 仍可继续使用旧装配路径。
    """

    from app.features.audio.factory import DESCRIPTOR as AUDIO, create_feature as create_audio
    from app.features.characters.factory import (
        DESCRIPTOR as CHARACTER,
        create_feature as create_character,
    )
    from app.features.importer.factory import (
        DESCRIPTOR as IMPORTER,
        create_feature as create_importer,
    )
    from app.features.preview.factory import DESCRIPTOR as PREVIEW, create_feature as create_preview
    from app.features.versions.factory import DESCRIPTOR as VERSIONS, create_feature as create_versions

    creators = (
        (VERSIONS, create_versions),
        (PREVIEW, create_preview),
        (AUDIO, create_audio),
        (CHARACTER, create_character),
        (IMPORTER, create_importer),
    )
    return tuple(
        FeatureDefinition(
            descriptor,
            lambda context, creator=creator: creator(context, parent=parent),
        )
        for descriptor, creator in creators
    )


def create_application_runtime(context: AppContext, parent=None) -> ApplicationRuntime:
    """创建完整应用 Runtime；P3 由生产入口显式调用。"""

    from app.features.importer.postprocessing import PostProcessorRegistry
    from .workflows import ImportPostprocessWorkflow

    features = create_features(context, default_feature_definitions(parent=parent))
    registry = FeatureRuntimeRegistry(features)
    postprocessor_registry = PostProcessorRegistry(("lua", "audio"))
    return ApplicationRuntime(
        context=context,
        registry=registry,
        postprocessor_registry=postprocessor_registry,
        import_workflow=ImportPostprocessWorkflow(
            registry.get("importer").controller,
            registry.get("audio").controller,
            registry.get("character").controller,
            postprocessor_registry,
        ),
    )
