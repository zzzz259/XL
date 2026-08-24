"""Characters Feature 的组合根工厂。"""

from app.shared.contracts import FeatureDescriptor, FeatureRuntime

from .controller import CharacterController
from .page import CharacterPage
from .service import CharacterService

DESCRIPTOR = FeatureDescriptor("character", "角色", "users")


def create_feature(context, parent=None) -> FeatureRuntime:
    page = CharacterPage(parent)
    controller = CharacterController(
        page,
        CharacterService(context.output_dir / "character_data", context.output_dir / "lua"),
        parent,
    )
    return FeatureRuntime(
        DESCRIPTOR,
        page,
        controller,
        status_signal=controller.status_changed,
        progress_signal=controller.parse_progress,
        badge_signal=controller.unread_changed,
    )
