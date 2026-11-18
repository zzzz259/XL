"""Parse, store and replay group keyword media."""

import asyncio
import hashlib
import logging
import re
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from PIL import Image, ImageSequence
from yarl import URL

_logger = logging.getLogger(__name__)
_CDN = "multimedia.nt.qq.com.cn"
_MAX_MEDIA_BYTES = 20 * 1024 * 1024
_MAX_TEXT_LENGTH = 4000
MAX_REPLIES = 5
_CHUNK_BYTES = 64 * 1024
_IMAGE_EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "GIF": "gif"}
# Pillow can decode PNG and GIF pixel data even if the last marker was cut off.
_IMAGE_END_MARKERS = {"PNG": b"\x00\x00\x00\x00IEND\xae\x42\x60\x82", "GIF": b";"}
_DRAFT_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_IMAGE_NAME = re.compile(r"[0-9]{2}\.(?:jpg|png|gif)")
_MENTION = re.compile(r"<@!?[^<>\s]+>")
_UNSUPPORTED = "只支持录入文字和图片，请重发这条消息。"
_IMAGE_UNAVAILABLE = "录入的图片文件不可用。"
_INVALID_IMAGE = "图片必须是完整的 JPEG、PNG 或 GIF，请重发这条消息。"
_INVALID_URL = "图片来源无效，请重发这条消息。"


class KeywordMediaError(ValueError):
    """A capture or replay error suitable for showing in the group."""


@dataclass(frozen=True)
class ReplyPart:
    kind: str
    text: str
    media_path: str = ""
    sha256: str = ""


def plan_replies(parts: Sequence[ReplyPart]) -> tuple[ReplyPart, ...]:
    replies: list[ReplyPart] = []
    for part in parts:
        if len(part.text) > _MAX_TEXT_LENGTH:
            raise KeywordMediaError("单条回复最多 4000 个字符，请缩短这条消息。")
        if part.kind == "text":
            if not part.text.strip():
                continue
            if replies and replies[-1].kind == "text":
                merged = f"{replies[-1].text}\n{part.text}"
                if len(merged) <= _MAX_TEXT_LENGTH:
                    replies[-1] = ReplyPart("text", merged)
                    continue
        elif part.kind != "image":
            raise KeywordMediaError(_UNSUPPORTED)
        replies.append(part)
        if len(replies) > MAX_REPLIES:
            raise KeywordMediaError("关键词最多回复 5 条，请缩短这条消息。")
    return tuple(replies)


def _message_digest(message_id: str) -> str:
    if not isinstance(message_id, str) or not message_id:
        raise KeywordMediaError("缺少 QQ 消息编号，请重发这条消息。")
    return hashlib.sha256(message_id.encode("utf-8")).hexdigest()


def _objects(value: object) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise KeywordMediaError(_UNSUPPORTED)
    return value


def _attachment_url(attachment: dict) -> str:
    content_type = attachment.get("content_type") or ""
    if not isinstance(content_type, str) or (
        content_type and not content_type.lower().startswith("image/")
        and content_type.lower() != "application/octet-stream"
    ):
        raise KeywordMediaError(_UNSUPPORTED)
    url = attachment.get("url")
    if not isinstance(url, str) or not url or any(
        char.isspace() or ord(char) < 32 or ord(char) == 127 or char in "\\#" for char in url
    ):
        raise KeywordMediaError(_INVALID_URL)
    if url.startswith("//"):
        url = "https:" + url
    elif url.partition("/")[0].lower() == _CDN:
        url = "https://" + url
    try:
        parts = urlsplit(url)
    except ValueError:
        raise KeywordMediaError(_INVALID_URL) from None
    if parts.scheme != "https" or parts.netloc.lower() not in (_CDN, f"{_CDN}:443") or not parts.path:
        raise KeywordMediaError(_INVALID_URL)
    return f"https://{_CDN}{parts.path}" + (f"?{parts.query}" if parts.query else "")


def _source_parts(data: dict, text: str, removed_mentions: set[str]) -> list[ReplyPart]:
    if data.get("message_type") not in (None, 0, 101) or data.get("ark_data"):
        raise KeywordMediaError(_UNSUPPORTED)
    parts = []
    for element in _objects(data.get("msg_elements")):
        content = element.get("content") or ""
        if not isinstance(content, str):
            raise KeywordMediaError(_UNSUPPORTED)
        if removed_mentions:
            content = _MENTION.sub(
                lambda match: "" if match[0] in removed_mentions else match[0], content
            )
        parts.extend(_source_parts(element, content, removed_mentions))

    if text.strip() and not any(part.kind == "text" for part in parts):
        parts.insert(0, ReplyPart("text", text))
    nested_images = {part.media_path for part in parts if part.kind == "image"}
    for attachment in _objects(data.get("attachments")):
        url = _attachment_url(attachment)
        if url not in nested_images:
            parts.append(ReplyPart("image", "", url))
    return parts


def _image_extension(path: Path) -> str:
    try:
        formats = tuple(_IMAGE_EXTENSIONS)
        with Image.open(path, formats=formats) as image:
            extension = _IMAGE_EXTENSIONS.get(image.format)
            if extension is None:
                raise KeywordMediaError(_INVALID_IMAGE)
            marker = _IMAGE_END_MARKERS.get(image.format)
            image.verify()
        if marker:
            with path.open("rb") as handle:
                handle.seek(-len(marker), 2)
                if handle.read() != marker:
                    raise KeywordMediaError(_INVALID_IMAGE)
        # Some decoders only check headers in verify(). Load the pixels too.
        with Image.open(path, formats=formats) as image:
            for frame in ImageSequence.Iterator(image):
                frame.load()
        return extension
    except (OSError, SyntaxError, EOFError, ValueError, Image.DecompressionBombError):
        raise KeywordMediaError(_INVALID_IMAGE) from None


def _image_digest(path: Path) -> str:
    if not path.is_file() or not 0 < path.stat().st_size <= _MAX_MEDIA_BYTES:
        raise KeywordMediaError(_IMAGE_UNAVAILABLE)
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            size += len(chunk)
            if size > _MAX_MEDIA_BYTES:
                raise KeywordMediaError(_IMAGE_UNAVAILABLE)
            digest.update(chunk)
    if not size:
        raise KeywordMediaError(_IMAGE_UNAVAILABLE)
    return digest.hexdigest()


async def _download_image(session: aiohttp.ClientSession, url: str, path: Path) -> str:
    digest = hashlib.sha256()
    size = 0
    try:
        # Keep QQ's signed query string byte-for-byte.
        async with session.get(URL(url, encoded=True), allow_redirects=False) as response:
            if response.status != 200:
                raise KeywordMediaError("图片下载失败，请重发这条消息。")
            if response.content_length is not None and response.content_length > _MAX_MEDIA_BYTES:
                raise KeywordMediaError("单张图片最多 20 MiB，请重发这条消息。")
            with path.open("xb") as handle:
                async for chunk in response.content.iter_chunked(_CHUNK_BYTES):
                    size += len(chunk)
                    if size > _MAX_MEDIA_BYTES:
                        raise KeywordMediaError("单张图片最多 20 MiB，请重发这条消息。")
                    handle.write(chunk)
                    digest.update(chunk)
    except (TimeoutError, aiohttp.ClientError):
        raise KeywordMediaError("图片下载失败，请重发这条消息。") from None
    return digest.hexdigest()


class KeywordMedia:
    def __init__(self, data_dir: str | Path):
        self._root = Path(data_dir).resolve() / "keyword_media"

    def _safe_path(self, path: Path) -> Path:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            raise KeywordMediaError(_IMAGE_UNAVAILABLE) from None
        if resolved != path or not resolved.is_relative_to(self._root) or resolved == self._root:
            raise KeywordMediaError(_IMAGE_UNAVAILABLE)
        return resolved

    def _draft_path(self, draft_id: str) -> Path:
        if not isinstance(draft_id, str) or not _DRAFT_ID.fullmatch(draft_id):
            raise KeywordMediaError(_IMAGE_UNAVAILABLE)
        return self._safe_path(self._root / draft_id)

    def _remove_directory(self, path: Path) -> None:
        path = self._safe_path(path)
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass
        except OSError:
            raise KeywordMediaError("关键词图片清理失败。") from None

    def discard_message(self, draft_id: str, message_id: str) -> None:
        self._remove_directory(self._draft_path(draft_id) / _message_digest(message_id))

    def discard(self, draft_id: str) -> None:
        self._remove_directory(self._draft_path(draft_id))

    def discard_unreferenced(self, referenced: set[str]) -> None:
        try:
            if not self._root.is_dir() or self._root.resolve() != self._root:
                return
            for directory in self._root.iterdir():
                if _DRAFT_ID.fullmatch(directory.name) and directory.name not in referenced:
                    try:
                        self.discard(directory.name)
                    except KeywordMediaError:
                        _logger.warning("stage=keyword_cleanup status=failed")
        except (OSError, RuntimeError):
            _logger.warning("stage=keyword_cleanup status=failed")

    async def capture(
        self, draft_id: str, message_id: str, data: dict, text: str
    ) -> tuple[ReplyPart, ...]:
        destination = self._safe_path(self._draft_path(draft_id) / _message_digest(message_id))
        raw_content = data.get("content")
        removed_mentions = set()
        if isinstance(raw_content, str):
            removed_mentions = set(_MENTION.findall(raw_content)) - set(_MENTION.findall(text))
        parts = _source_parts(data, text, removed_mentions)
        if not parts and (data.get("message_type") == 101 or data.get("msg_elements")):
            raise KeywordMediaError(_UNSUPPORTED)
        if len(parts) >= 2 and parts[0].kind == "text" and parts[1].kind == "image":
            parts[0:2] = [replace(parts[1], text=parts[0].text)]
        plan_replies(parts)

        image_positions = [index for index, part in enumerate(parts) if part.kind == "image"]
        if not image_positions:
            return tuple(parts)

        staging = None
        try:
            if destination.is_dir():
                return await asyncio.to_thread(self._restore, destination, parts, image_positions)
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".capture-", dir=destination.parent))
            timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for slot, position in enumerate(image_positions):
                    part = parts[position]
                    temporary = staging / f"{slot:02d}.part"
                    digest = await _download_image(session, part.media_path, temporary)
                    check = asyncio.create_task(asyncio.to_thread(_image_extension, temporary))
                    try:
                        extension = await asyncio.shield(check)
                    except asyncio.CancelledError:
                        # Windows can only remove the temporary directory after Pillow closes the file.
                        await asyncio.gather(check, return_exceptions=True)
                        raise
                    name = f"{slot:02d}.{extension}"
                    temporary.rename(staging / name)
                    parts[position] = replace(
                        part, media_path=self._relative(destination / name), sha256=digest
                    )
            self._safe_path(destination)
            try:
                staging.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
                return await asyncio.to_thread(self._restore, destination, parts, image_positions)
            return tuple(parts)
        except OSError:
            raise KeywordMediaError("图片无法保存，请重发这条消息。") from None
        finally:
            if staging is not None:
                try:
                    self._remove_directory(staging)
                except KeywordMediaError:
                    _logger.warning("Keyword capture staging cleanup failed")

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()

    def _restore(
        self, destination: Path, parts: list[ReplyPart], image_positions: list[int]
    ) -> tuple[ReplyPart, ...]:
        # A complete message directory can survive an interruption before its DB append.
        files = sorted(self._safe_path(destination).iterdir())
        if len(files) != len(image_positions):
            raise KeywordMediaError(_IMAGE_UNAVAILABLE)
        for slot, (path, position) in enumerate(zip(files, image_positions)):
            if not _IMAGE_NAME.fullmatch(path.name) or path.stem != f"{slot:02d}":
                raise KeywordMediaError(_IMAGE_UNAVAILABLE)
            digest = _image_digest(self._safe_path(path))
            if path.suffix != f".{_image_extension(path)}":
                raise KeywordMediaError(_IMAGE_UNAVAILABLE)
            parts[position] = replace(parts[position], media_path=self._relative(path), sha256=digest)
        return tuple(parts)

    def resolve(self, part: ReplyPart, media_id: str) -> Path:
        if not isinstance(part, ReplyPart) or part.kind != "image" or not isinstance(part.media_path, str):
            raise KeywordMediaError(_IMAGE_UNAVAILABLE)
        levels = part.media_path.split("/")
        if (
            len(levels) != 3 or levels[0] != media_id
            or not _SHA256.fullmatch(levels[1]) or not _IMAGE_NAME.fullmatch(levels[2])
            or not isinstance(part.sha256, str) or not _SHA256.fullmatch(part.sha256)
        ):
            raise KeywordMediaError(_IMAGE_UNAVAILABLE)
        path = self._safe_path(self._draft_path(media_id).joinpath(*levels[1:]))
        try:
            if _image_digest(path) != part.sha256:
                raise KeywordMediaError(_IMAGE_UNAVAILABLE)
        except OSError:
            raise KeywordMediaError(_IMAGE_UNAVAILABLE) from None
        return path
