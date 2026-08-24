"""角色 Lua 解析器公共入口。"""

from .assembler import load_character_data
from .cards import parse_basecard_file
from .common import (
    extract_all_card_blocks,
    extract_t_references,
    merge_t_function_params,
    parse_skill_up_args,
    parse_t_args,
    parse_t_function_params,
    process_t_function_params,
    resolve_t_call,
)
from .progression import (
    get_breakthrough_cost,
    get_normal_skill_upgrade_cost,
    get_passive_skill_upgrade_cost,
    parse_quality_up_file_with_cost,
    parse_skill_level_up_file_with_cost,
)
from .skills import (
    extract_awakening_info,
    extract_awakening_skill_info,
    extract_multi_level_skill_info_new,
    extract_skill_info,
)
from .words import (
    parse_badge_suit_file,
    parse_cv_file,
    parse_item_file,
    parse_level_up_file,
    parse_word_file,
)

__all__ = [
    "load_character_data", "parse_basecard_file",
    "extract_all_card_blocks", "extract_t_references", "parse_skill_up_args",
    "parse_t_args", "parse_t_function_params", "process_t_function_params",
    "resolve_t_call", "merge_t_function_params", "parse_word_file", "parse_cv_file",
    "parse_level_up_file", "parse_badge_suit_file", "parse_item_file",
    "parse_quality_up_file_with_cost", "parse_skill_level_up_file_with_cost",
    "extract_skill_info", "extract_awakening_skill_info", "extract_awakening_info",
    "extract_multi_level_skill_info_new", "get_breakthrough_cost",
    "get_normal_skill_upgrade_cost", "get_passive_skill_upgrade_cost",
]
