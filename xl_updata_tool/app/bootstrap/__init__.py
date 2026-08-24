"""应用启动和 Feature 组合根。"""

from .app_factory import FeatureDefinition, create_features, default_feature_definitions
from .context import AppContext, build_app_context
from .runtime import FeatureRuntimeRegistry

__all__ = [
    "AppContext",
    "FeatureDefinition",
    "build_app_context",
    "create_features",
    "default_feature_definitions",
    "FeatureRuntimeRegistry",
]
