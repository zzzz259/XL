"""跨功能域共享的、与 Qt 无关的契约和数据结构。"""

from .contracts import (
    ControllerPort,
    FeatureDescriptor,
    FeatureRuntime,
    ImportResult,
    PagePort,
    ServicePort,
    WorkerPort,
)

__all__ = [
    "ControllerPort",
    "FeatureDescriptor",
    "FeatureRuntime",
    "ImportResult",
    "PagePort",
    "ServicePort",
    "WorkerPort",
]
