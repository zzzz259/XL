import logging
import os
from logging.handlers import RotatingFileHandler
from .path_utils import get_logs_dir


def setup_logger():
    logs_dir = get_logs_dir()
    os.makedirs(logs_dir, exist_ok=True)

    # 固定文件名，使用 RotatingFileHandler 限制大小
    log_file = os.path.join(logs_dir, "app.log")

    logger = logging.getLogger("xl_updata_tool")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 文件日志：DEBUG 级别，单文件最大 10MB，backupCount=0（仅保留当前文件，超出时自动清空重写）
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=0,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # 控制台日志：INFO 级别
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


logger = setup_logger()
