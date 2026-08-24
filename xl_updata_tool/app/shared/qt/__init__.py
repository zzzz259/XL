"""共享 Qt 令牌与控件门面。

Feature 页面可以从这里获取统一的主题和页头能力，避免重新依赖具体旧 UI
模块。迁移期实现仍由旧模块提供，后续可在不改调用方的情况下替换。
"""

from .chrome import (
    create_action_button,
    create_command_bar,
    create_empty_state,
    create_page_header,
    create_status_label,
)
from .tokens import *  # noqa: F401,F403

__all__ = [
    "create_action_button",
    "create_command_bar",
    "create_empty_state",
    "create_page_header",
    "create_status_label",
]

