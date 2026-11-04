"""Strict, bounded validation for explicitly opted-in local PNG delivery."""

import hashlib
import io
import re
import stat
import struct
import zlib
from pathlib import Path

from PIL import Image

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 8_388_608
SHA_RE = re.compile(r"[0-9a-f]{64}")
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def validate_roots(roots):
    if not isinstance(roots, dict):
        raise ValueError("portrait roots must be a source mapping")  # noqa: TRY004 - configuration error contract
    result = {}
    for source, root in roots.items():
        if not isinstance(source, str) or not ID_RE.fullmatch(source):
            raise ValueError("invalid portrait source ID")
        if not isinstance(root, str) or not root or not Path(root).is_absolute():
            raise ValueError("portrait root must be explicit and absolute")
        result[source] = Path(root)
    return result


def resolve_artifact(roots, source, artifact, expected):
    if not isinstance(source, str) or source not in roots:
        raise ValueError("unknown portrait source")
    if not isinstance(expected, str) or not SHA_RE.fullmatch(expected):
        raise ValueError("invalid expected SHA256")
    if artifact != f"artifacts/{expected}.png":
        raise ValueError("invalid portrait artifact path")
    root = roots[source]
    path = root / "artifacts" / (expected + ".png")
    for component in (path, *path.parents):
        if component.is_symlink() or _is_junction(component):
            raise ValueError("portrait path cannot contain links")
    path.resolve(strict=True).relative_to(root.resolve(strict=True))
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("portrait artifact must be a regular file")
    validate_png_file(path, expected)
    return path


def _is_junction(path):
    predicate = getattr(path, "is_junction", None)
    if callable(predicate):
        return predicate()
    # Python 3.11 Windows exposes the mount-point reparse tag through lstat.
    return getattr(path.lstat(), "st_reparse_tag", 0) == getattr(
        stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003
    )


def validate_png_file(path, expected):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("portrait exceeds byte budget")
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("portrait digest mismatch")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("portrait must be PNG")
    offset = 8
    ended = False
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + size
        if end > len(data) or zlib.crc32(data[offset + 4 : end - 4]) != int.from_bytes(
            data[end - 4 : end], "big"
        ):
            raise ValueError("invalid PNG chunk")
        if offset == 8:
            if kind != b"IHDR" or size != 13:
                raise ValueError("invalid PNG header")
            width, height = struct.unpack(">II", data[offset + 8 : offset + 16])
            if not width or not height or width * height > MAX_PIXELS:
                raise ValueError("portrait exceeds pixel budget")
        if kind == b"IHDR" and offset != 8:
            raise ValueError("duplicate PNG header")
        if kind == b"acTL":
            raise ValueError("animated PNG is not supported")
        offset = end
        if kind == b"IEND":
            ended = size == 0 and offset == len(data)
            break
    if not ended:
        raise ValueError("incomplete PNG")
    try:
        with Image.open(io.BytesIO(data)) as image:
            _validate_decoder_header(image, (width, height))
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            _validate_decoder_header(image, (width, height))
            image.load()
            if image.convert("RGBA").getchannel("A").getextrema()[1] == 0:
                raise ValueError("portrait has no visible pixels")
    except (OSError, SyntaxError) as exc:
        raise ValueError("invalid PNG decode") from exc


def _validate_decoder_header(image, dimensions):
    """Cross-check decoder metadata before verification or any full decode."""
    if (
        image.format != "PNG"
        or image.size != dimensions
        or image.size[0] * image.size[1] > MAX_PIXELS
        or image.n_frames != 1
        or image.is_animated
    ):
        raise ValueError("PNG decoder metadata disagrees with static header")
