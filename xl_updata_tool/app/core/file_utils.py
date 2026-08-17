"""文件落盘辅助函数。"""

import os
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
