"""Audio Feature 的组合根工厂。"""

from app.shared.contracts import FeatureDescriptor, FeatureRuntime

from .controller import AudioController
from .page import AudioPage

DESCRIPTOR = FeatureDescriptor("audio", "音频", "music")


def create_feature(context, parent=None) -> FeatureRuntime:
    page = AudioPage(parent)
    controller = AudioController(
        page=page,
        material_dir=str(context.data_dir / "material"),
        debank_dir=str(context.tools_dir / "epic7_debank_v1_0"),
        lua_output_dir=str(context.output_dir / "lua"),
        output_dir=str(context.output_dir),
        parent=parent,
    )
    return FeatureRuntime(
        DESCRIPTOR,
        page,
        controller,
        status_signal=controller.status_changed,
        progress_signal=controller.processing_progress_value,
        badge_signal=controller.unread_changed,
        badge_state=lambda: controller.has_unread,
    )
