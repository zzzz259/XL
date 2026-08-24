# -*- coding: utf-8 -*-
"""角色页面的迁移期兼容入口。

新代码使用 ``app.features.characters.CharacterPage``；这里保留旧工厂返回值，
让已有插件和 UI 回归测试可以在迁移期间继续工作。
"""

from app.features.characters.page import CharacterPage


def create_character_view(parent=None):
    page = CharacterPage(parent)
    controls = {
        "character_title": page.character_title,
        "character_search": page.character_search,
        "character_table": page.character_table,
        "character_detail": page.character_detail,
        "character_profile_view": page.character_profile_view,
        "character_status": page.character_status,
        "character_empty": page.character_empty,
        "btn_mark_all_read": page.btn_mark_all_read,
    }
    return page, controls
