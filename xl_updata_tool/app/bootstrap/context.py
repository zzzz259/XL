"""迁移期应用运行时上下文。

上下文只负责把平台路径和启动配置作为依赖传入 Feature。它不创建 Qt
对象，也不在模块导入时初始化数据库、日志或外部工具。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppContext:
    """Feature 装配时共享的只读运行时依赖。"""

    base_dir: Path
    data_dir: Path
    output_dir: Path
    tools_dir: Path
    runtime_config: object | None = None


def build_app_context(
    runtime_config: object | None = None,
    *,
    base_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    tools_dir: str | Path | None = None,
) -> AppContext:
    """构建运行时上下文；显式路径主要用于测试和未来的依赖注入。"""

    if base_dir is None or data_dir is None or output_dir is None or tools_dir is None:
        from app.core.path_utils import (
            get_base_dir,
            get_data_dir,
            get_output_dir,
            get_tools_dir,
        )

        base_dir = base_dir or get_base_dir()
        data_dir = data_dir or get_data_dir()
        output_dir = output_dir or get_output_dir()
        tools_dir = tools_dir or get_tools_dir()

    return AppContext(
        base_dir=Path(base_dir),
        data_dir=Path(data_dir),
        output_dir=Path(output_dir),
        tools_dir=Path(tools_dir),
        runtime_config=runtime_config,
    )
