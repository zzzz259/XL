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
    logger = logging.getLogger("xl_updata_tool")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 允许测试、便携版和受限目录通过环境变量指定日志目录。
    # 文件日志不可用时仍保留控制台日志，避免日志权限问题阻止应用启动。
    log_dir_error = None
    try:
        logs_dir = os.environ.get("XL_LOG_DIR") or get_logs_dir()
        os.makedirs(logs_dir, exist_ok=True)
        _cleanup_old_logs(logs_dir, keep=20)

        # 微秒避免同一秒内多次启动覆盖同一个日志文件。
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_file = os.path.join(logs_dir, f"app_{timestamp}.log")
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except (OSError, PermissionError) as exc:
        log_dir_error = exc

    # 控制台日志：INFO 级别
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    if log_dir_error:
        logger.warning("文件日志不可用，将仅输出控制台日志: %s", log_dir_error)

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
