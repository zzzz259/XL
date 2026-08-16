import logging
import os
from datetime import datetime
from .path_utils import get_logs_dir


def _cleanup_old_logs(logs_dir, keep=20):
    """保留最近 keep 个日志文件，删除更旧的（含旧版固定名 app.log）"""
    # 删除旧版固定名 app.log（已被时间戳命名替代）
    old_app_log = os.path.join(logs_dir, "app.log")
    if os.path.isfile(old_app_log):
        try:
            os.remove(old_app_log)
        except OSError:
            pass
    try:
        logs = sorted(
            [f for f in os.listdir(logs_dir) if f.startswith("app_") and f.endswith(".log")],
            reverse=True,
        )
        for f in logs[keep:]:
            try:
                os.remove(os.path.join(logs_dir, f))
            except OSError:
                pass
    except OSError:
        pass


def setup_logger():
    logs_dir = get_logs_dir()
    os.makedirs(logs_dir, exist_ok=True)
    _cleanup_old_logs(logs_dir, keep=20)

    # 每次启动新建一个带时间戳的日志文件
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(logs_dir, f"app_{timestamp}.log")

    logger = logging.getLogger("xl_updata_tool")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 文件日志：DEBUG 级别，每次启动新建文件（不轮转）
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
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


def timed(name=None):
    """装饰器：记录函数耗时（性能优化依据）"""
    import time
    from functools import wraps

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            t0 = time.time()
            try:
                return func(*args, **kwargs)
            finally:
                logger.info(f"[耗时] {name or func.__name__}: {time.time() - t0:.2f}s")
        return wrapper
    return decorator
