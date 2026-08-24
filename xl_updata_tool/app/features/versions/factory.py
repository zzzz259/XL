"""Versions Feature 的组合根工厂。"""

from app.shared.contracts import FeatureDescriptor, FeatureRuntime

from .controller import VersionController
from .page import VersionPage
from .service import VersionService

DESCRIPTOR = FeatureDescriptor("versions", "版本列表", "list")


def create_feature(context, parent=None) -> FeatureRuntime:
    page = VersionPage(parent)
    controller = VersionController(
        page, VersionService(context.data_dir / "bundles"), parent
    )
    return FeatureRuntime(
        DESCRIPTOR,
        page,
        controller,
        status_signal=controller.status_changed,
        progress_signal=controller.progress_changed,
    )
