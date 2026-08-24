"""角色 Lua 解析器兼容入口。

正式实现归属 ``app.features.characters.parser``；保留本模块以兼容已有脚本和
旧版本内部调用，不在此处维护第二份解析实现。
"""

from app.features.characters.parser import *  # noqa: F403
