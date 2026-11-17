"""PNG 内容摘要：尺寸和逐行解码后的 RGBA；文件哈希另作完整性校验。"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from PIL import Image

MAX_PORTRAIT_PIXELS = 25_000_000


def png_pixel_sha256(path: Path) -> str:
    """完整解码 PNG，返回 SHA256(big-endian width,height + row-major RGBA)。"""
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError(f"立绘产物不是 PNG: {path}")
        width, height = image.size
        if width * height > MAX_PORTRAIT_PIXELS:
            raise ValueError(f"立绘超过 {MAX_PORTRAIT_PIXELS} 像素上限: {width}x{height}")
        image.load()  # 截断/损坏的像素数据必须失败，不能退回文件 SHA。
        digest = hashlib.sha256(struct.pack(">II", width, height))
        for y in range(height):
            with image.crop((0, y, width, y + 1)).convert("RGBA") as row:
                digest.update(row.tobytes())
        return digest.hexdigest()
