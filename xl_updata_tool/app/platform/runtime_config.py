"""启动运行模式配置。

运行模式必须在导入主窗口和业务模块前确定，避免 Debug 参数已经解析完毕
后日志系统才开始配置。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class RuntimeConfig:
    """一次应用启动的不可变运行配置。"""

    debug: bool = False
    self_check: bool = False
    extra_args: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return "DEBUG" if self.debug else "NORMAL"

    @property
    def log_level(self) -> int:
        """返回文件诊断日志级别；普通运行也完整保留 Debug 细节。"""
        import logging

        return logging.DEBUG

    @property
    def console_level(self) -> int:
        import logging

        return logging.DEBUG if self.debug else logging.INFO

    @property
    def capture_external_output(self) -> bool:
        """正式版普通模式也必须捕获外部工具输出。"""
        return True


def parse_runtime_config(argv: Sequence[str] | None = None) -> RuntimeConfig:
    """解析 XL 自己的参数，保留 Qt 或启动器可能传入的未知参数。"""
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--debug", action="store_true", help="启用诊断运行模式")
    parser.add_argument("--self-check", action="store_true", help="运行 Release 环境自检后退出（不启动界面）")
    args, unknown = parser.parse_known_args(list(argv) if argv is not None else None)
    return RuntimeConfig(debug=bool(args.debug), self_check=bool(args.self_check), extra_args=tuple(unknown))
