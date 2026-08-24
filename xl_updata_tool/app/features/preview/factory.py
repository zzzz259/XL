"""Preview Feature 的组合根工厂。"""

from app.shared.contracts import FeatureDescriptor, FeatureRuntime

from .controller import PreviewController
from .page import PreviewPage
from .service import PreviewService

DESCRIPTOR = FeatureDescriptor("preview", "图片预览", "image")


def create_feature(context, parent=None) -> FeatureRuntime:
    page = PreviewPage(parent)
    controller = PreviewController(
        page,
        PreviewService(context.data_dir / "material", context.output_dir / "character"),
        parent,
    )
    return FeatureRuntime(
        DESCRIPTOR,
        page,
        controller,
        status_signal=controller.status_changed,
        progress_signal=controller.progress_changed,
    )
