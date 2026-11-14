"""history.png_pixel_sha256：PNG 解码后的像素哈希。

只支持 RGB8/RGBA8、8 位、非隔行；逐 chunk 校验 CRC，使用 Pillow 解码并
统一为 RGBA 再哈希（含尺寸）。同一像素内容即使压缩字节/文件 SHA 不同也
得到同一摘要；不支持的颜色类型/位深/隔行/坏数据明确报错。
"""

import hashlib
import struct
import tracemalloc
import zlib

import pytest

from bot_app import history


def chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def make_png(width=2, height=3, rgba=(1, 2, 3, 4), variant=0):
    """RGBA8 合法 PNG；variant=1 用 Sub 滤镜重打包，解码像素相同、文件字节不同。"""
    row = bytes(rgba) * width
    if variant == 0:
        raw = b"".join(b"\x00" + row for _ in range(height))
        level = 6
    else:
        first = row[:4]
        rest = bytes((row[i] - row[i - 4]) & 0xFF for i in range(4, len(row)))
        sub_row = first + rest
        raw = b"".join(b"\x01" + sub_row for _ in range(height))
        level = 9
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, level))
        + chunk(b"IEND", b"")
    )


def make_rgb8_png(width=2, height=2, rgb=(7, 8, 9)):
    """RGB8 合法 PNG（无 alpha）。"""
    row = bytes(rgb) * width
    raw = b"".join(b"\x00" + row for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def make_ihdr(width, height, bit_depth=8, color_type=6, interlace=0):
    return struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, interlace)


def test_same_pixels_different_bytes_same_hash():
    a = make_png(3, 2)
    b = make_png(3, 2, variant=1)
    assert hashlib.sha256(a).hexdigest() != hashlib.sha256(b).hexdigest()
    assert history.png_pixel_sha256(a) == history.png_pixel_sha256(b)


def test_rgb8_and_rgba8_same_visible_pixels_same_hash():
    rgb8 = make_rgb8_png(2, 2, rgb=(7, 8, 9))
    rgba8 = make_png(2, 2, rgba=(7, 8, 9, 255))
    assert history.png_pixel_sha256(rgb8) == history.png_pixel_sha256(rgba8)


def test_different_pixels_or_dimensions_differ():
    base = history.png_pixel_sha256(make_png(2, 2))
    assert history.png_pixel_sha256(make_png(2, 2, rgba=(9, 9, 9, 9))) != base
    assert history.png_pixel_sha256(make_png(2, 3)) != base
    assert history.png_pixel_sha256(make_png(3, 2)) != base


def test_interlaced_rejected():
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", make_ihdr(2, 2, interlace=1))
        + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(8)))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError, match="隔行"):
        history.png_pixel_sha256(data)


def test_unsupported_bit_depth_rejected():
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", make_ihdr(2, 2, bit_depth=16))
        + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(16)))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError, match="位深"):
        history.png_pixel_sha256(data)


@pytest.mark.parametrize("color_type", [0, 3, 4, 1])  # 灰度/调色板/灰度+Alpha/未定义
def test_unsupported_color_type_rejected(color_type):
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", make_ihdr(2, 2, color_type=color_type))
        + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(16)))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError, match="颜色类型"):
        history.png_pixel_sha256(data)


def test_bad_crc_rejected():
    good = make_png(2, 2)
    # 翻转 IEND CRC 的第一个字节
    corrupted = bytearray(good)
    corrupted[-4] ^= 0xFF
    with pytest.raises(ValueError, match="CRC"):
        history.png_pixel_sha256(bytes(corrupted))


def test_truncated_idat_rejected():
    ihdr = make_ihdr(4, 4)
    # 只提供 1 行原始数据，预期 4 行
    raw = b"\x00" + bytes(4 * 4)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError, match="长度"):
        history.png_pixel_sha256(data)


def test_missing_idat_rejected():
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", make_ihdr(2, 2)) + chunk(b"IEND", b"")
    with pytest.raises(ValueError, match="IDAT"):
        history.png_pixel_sha256(data)


def test_not_a_png_rejected():
    with pytest.raises(ValueError, match="PNG"):
        history.png_pixel_sha256(b"not a png at all")


def test_pixel_budget_enforced_before_inflate():
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", make_ihdr(10000, 10000))
        + chunk(b"IDAT", zlib.compress(b""))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError, match="像素"):
        history.png_pixel_sha256(data)


def test_large_uniform_png_does_not_duplicate_pixels_in_python():
    data = make_png(2000, 2000, rgba=(0, 0, 0, 0))
    tracemalloc.start()
    try:
        history.png_pixel_sha256(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024


def test_exact_decompression_chunk_boundary():
    data = make_png(256, 1024, rgba=(0, 0, 0, 0))
    expected = hashlib.sha256(
        struct.pack(">II", 256, 1024) + bytes(256 * 1024 * 4)
    ).hexdigest()
    assert history.png_pixel_sha256(data) == expected


def test_incomplete_zlib_checksum_rejected():
    raw = b"".join(b"\x00" + bytes(8) for _ in range(2))
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", make_ihdr(2, 2))
        + chunk(b"IDAT", zlib.compress(raw)[:-4])
        + chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError, match="不完整"):
        history.png_pixel_sha256(data)


def test_trailing_zlib_bytes_rejected():
    raw = b"".join(b"\x00" + bytes(8) for _ in range(2))
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", make_ihdr(2, 2))
        + chunk(b"IDAT", zlib.compress(raw) + b"trailing")
        + chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError, match="多余"):
        history.png_pixel_sha256(data)


def test_all_standard_row_filters_match_unfiltered_pixels():
    width = 5
    rows = [
        bytes((row * 43 + column * 17) % 256 for column in range(width * 4))
        for row in range(5)
    ]
    encoded = []
    previous = bytes(width * 4)
    for filter_type, row in enumerate(rows):
        line = bytearray(row)
        for index in range(len(row) - 1, -1, -1):
            left = row[index - 4] if index >= 4 else 0
            above = previous[index]
            above_left = previous[index - 4] if index >= 4 else 0
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                estimate = left + above - above_left
                distances = [abs(estimate - value) for value in (left, above, above_left)]
                predictor = (left, above, above_left)[distances.index(min(distances))]
            else:
                predictor = 0
            line[index] = (row[index] - predictor) & 0xFF
        encoded.append(bytes([filter_type]) + bytes(line))
        previous = row
    filtered = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", make_ihdr(width, len(rows)))
        + chunk(b"IDAT", zlib.compress(b"".join(encoded)))
        + chunk(b"IEND", b"")
    )
    plain = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", make_ihdr(width, len(rows)))
        + chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + row for row in rows)))
        + chunk(b"IEND", b"")
    )
    assert history.png_pixel_sha256(filtered) == history.png_pixel_sha256(plain)
