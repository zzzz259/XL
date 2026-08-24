"""文件落盘辅助函数。"""

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path


def atomic_write_bytes(
    destination: str | os.PathLike[str],
    data: bytes,
    transform: Callable[[str], bool | None] | None = None,
) -> None:
    """将 bytes 安全写入目标文件，可在替换前对临时文件做校验或转换。

    临时文件与目标文件位于同一目录，保证 ``os.replace`` 在 Windows 上也是同卷原子替换。
    ``transform`` 返回 ``False`` 时视为失败，旧目标文件不会被覆盖。
    """
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

        if transform is not None and transform(temp_name) is False:
            raise OSError(f"临时文件处理失败: {target}")

        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def replace_directory(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
    """用 source 替换 destination，失败时尽量恢复原目录。"""
    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_dir():
        raise FileNotFoundError(f"替换源目录不存在: {source_path}")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if destination_path.exists():
        backup_path = Path(
            tempfile.mkdtemp(prefix=f".{destination_path.name}.backup-", dir=destination_path.parent)
        )
        backup_path.rmdir()
        os.replace(destination_path, backup_path)

    try:
        os.replace(source_path, destination_path)
    except Exception:
        if backup_path is not None and not destination_path.exists():
            os.replace(backup_path, destination_path)
        raise
    else:
        if backup_path is not None:
            shutil.rmtree(backup_path, ignore_errors=True)
