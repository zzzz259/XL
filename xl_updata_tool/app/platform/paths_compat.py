"""文件系统路径的平台契约。"""

from app.core.path_utils import (
    DATA_DIR,
    get_base_dir,
    get_data_dir,
    get_logs_dir,
    get_output_dir,
    get_tools_dir,
)

__all__ = [
    "DATA_DIR",
    "get_base_dir",
    "get_data_dir",
    "get_logs_dir",
    "get_output_dir",
    "get_tools_dir",
]

