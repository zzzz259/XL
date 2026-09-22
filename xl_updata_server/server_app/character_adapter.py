"""复用主工具中已经测试过的无 Qt 角色解析器。"""

from __future__ import annotations

import sys
from pathlib import Path


def _tool_app_root() -> Path:
    return Path(__file__).resolve().parents[2] / "xl_updata_tool"


def parse_character_snapshot(lua_dir: Path) -> dict:
    tool_root = _tool_app_root()
    if str(tool_root) not in sys.path:
        sys.path.insert(0, str(tool_root))
    from app.features.characters.parser import load_character_data

    index, characters, words = load_character_data(str(lua_dir))
    return {"index": index, "characters": characters, "words": words}
