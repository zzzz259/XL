"""路径适配工具：支持 PyInstaller 打包后正确获取资源路径

- 数据目录（data/、logs/、output/）→ exe 所在目录（持久化）
- 工具目录（tools/）→ sys._MEIPASS（只读解压资源）
"""

import os
import sys


def get_base_dir():
    """应用根目录（开发时为项目根目录，打包后为 exe 所在目录）
    路径说明：
    - 开发环境：__file__ → app/core/path_utils.py，需要上溯 3 级 → 项目根目录
    - 打包后：sys.executable → exe 文件所在目录，即为应用根目录
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_data_dir():
    """获取 data/ 目录（持久化数据）"""
    return os.path.join(get_base_dir(), "data")


DATA_DIR = get_data_dir()


def get_tools_dir():
    """工具目录（打包后从临时解压目录读取，开发时从项目目录读取）"""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, "tools")
    return os.path.join(get_base_dir(), "tools")


def get_output_dir():
    """获取 output/ 目录（持久化数据，自动创建）"""
    d = os.path.join(get_base_dir(), "output")
    os.makedirs(d, exist_ok=True)
    return d


def get_logs_dir():
    """获取 logs/ 目录（持久化数据，自动创建）"""
    d = os.path.join(get_base_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d