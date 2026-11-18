import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from bot_app.keyword_media import (
    KeywordMedia,
    KeywordMediaError,
    ReplyPart,
    plan_replies,
)

CDN = "multimedia.nt.qq.com.cn"
MAX_BYTES = 20 * 1024 * 1024


def image_bytes(image_format):
    with io.BytesIO() as handle, Image.new("RGB", (12, 10), "orange") as image:
        image.save(handle, image_format)
        return handle.getvalue()


def attachment(url, content_type="image/png"):
    return {"url": url, "content_type": content_type, "filename": "../original.fake"}


class ImageResponse:
    def __init__(self, chunks=(), *, status=200, content_length=None, timeout=False):
        self.status = status
        self.content_length = content_length
        self.content = self
        self.chunks = chunks
        self.timeout = timeout
        self.read = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def iter_chunked(self, size):
        self.read = True
        for chunk in self.chunks:
            yield chunk
        if self.timeout:
            raise TimeoutError


class ImageSession:
    def __init__(self):
        self.responses = {}
        self.requests = []
        self.timeout = None

    def __call__(self, *, timeout):
        self.timeout = timeout
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, *, allow_redirects):
        if allow_redirects:
            raise AssertionError("media requests may not follow redirects")
        self.requests.append(str(url))
        return self.responses[str(url)]


class ReplyPlanningTests(unittest.TestCase):
    def test_merges_only_adjacent_text_and_preserves_captions_and_line_breaks(self):
        image = ReplyPart("image", "说明\n两行", "stored.png", "digest")
        parts = (ReplyPart("text", "  第一行\n第二行"), ReplyPart("text", " 第三行 "),
                 image, ReplyPart("text", "  第四行"))
        self.assertEqual(plan_replies(parts), (
            ReplyPart("text", "  第一行\n第二行\n 第三行 "), image,
            ReplyPart("text", "  第四行"),
        ))

    def test_splits_at_original_boundaries_and_limits_gateway_sends(self):
        first = ReplyPart("text", "甲" * 3999)
        second = ReplyPart("text", "乙")
        third = ReplyPart("text", "丙")
        self.assertEqual(plan_replies((first, second, third)),
                         (first, ReplyPart("text", "乙\n丙")))
        self.assertEqual(len(plan_replies((first,) * 5)), 5)
        with self.assertRaises(KeywordMediaError):
            plan_replies((first,) * 6)
        with self.assertRaises(KeywordMediaError):
            plan_replies((ReplyPart("text", "甲" * 4001),))
        with self.assertRaises(KeywordMediaError):
            plan_replies((ReplyPart("image", "甲" * 4001),))


class KeywordMediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_dir = Path(directory.name)
        self.media = KeywordMedia(self.data_dir)
        self.draft_id = uuid4().hex
        self.session = ImageSession()
        patcher = patch("bot_app.keyword_media.aiohttp.ClientSession", new=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_image(self, name, image_format):
        url = f"https://{CDN}/{name}?rkey=redacted"
        original = image_bytes(image_format)
        self.session.responses[url] = ImageResponse((original[:20], original[20:]))
        return url, original

    async def test_plain_text_uses_supplied_text_and_blank_event_has_no_content(self):
        text = "  原文 https://example.invalid/x\n 第二行  "
        parts = await self.media.capture(self.draft_id, "plain", {"content": "<@!bot>" + text}, text)
        self.assertEqual(parts, (ReplyPart("text", text),))
        self.assertEqual(await self.media.capture(self.draft_id, "empty", {}, " \n\t"), ())
        self.assertEqual(self.session.requests, [])

    async def test_structured_order_preserves_captions_and_ignores_mirrored_attachments(self):
        first_url, first_bytes = self.add_image("first", "PNG")
        second_url, second_bytes = self.add_image("second", "GIF")
        first = attachment(first_url.removeprefix("https:"))
        second = attachment(second_url.removeprefix("https://"), "image/gif")
        data = {
            "message_type": 101,
            "content": "首图说明\n中间文字\n最后一行",
            "attachments": [first, second],
            "msg_elements": [
                {"content": "首图说明\n"}, {"attachments": [first]},
                {"content": "中间文字"}, {"attachments": [second]},
                {"content": "最后一行"},
            ],
        }
        parts = await self.media.capture(self.draft_id, "mixed", data, data["content"])
        self.assertEqual([(part.kind, part.text) for part in parts], [
            ("image", "首图说明\n"), ("text", "中间文字"),
            ("image", ""), ("text", "最后一行"),
        ])
        self.assertEqual(self.session.requests, [first_url, second_url])
        for part, original, extension in [(parts[0], first_bytes, ".png"), (parts[2], second_bytes, ".gif")]:
            self.assertEqual(part.sha256, hashlib.sha256(original).hexdigest())
            path = self.media.resolve(part, self.draft_id)
            self.assertEqual(path.suffix, extension)
            self.assertEqual(path.read_bytes(), original)

    async def test_ordinary_image_only_and_partial_capture_retry_uses_staged_original(self):
        url, original = self.add_image("picture", "JPEG")
        data = {"attachments": [attachment(url, "image/jpeg")]}
        first = await self.media.capture(self.draft_id, "image/123", data, " \n")
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].text, "")
        path = self.media.resolve(first[0], self.draft_id)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.suffix, ".jpg")
        self.assertEqual(path.parent.name, hashlib.sha256(b"image/123").hexdigest())
        self.session.responses.clear()
        second = await self.media.capture(self.draft_id, "image/123", data, " \n")
        self.assertEqual(first, second)
        self.assertEqual(self.session.requests, [url])

    async def test_ordinary_text_is_only_the_first_images_caption(self):
        first, _ = self.add_image("first", "PNG")
        second, _ = self.add_image("second", "PNG")
        data = {"attachments": [attachment(first), attachment(second)]}
        parts = await self.media.capture(self.draft_id, "ordinary", data, "首图\n说明")
        self.assertEqual([(part.kind, part.text) for part in parts],
                         [("image", "首图\n说明"), ("image", "")])

    async def test_nested_text_removes_only_the_bot_tag_removed_from_top_level_text(self):
        raw = "<@!bot> <@!another> 文字\n第二行"
        text = raw.replace("<@!bot>", "")
        data = {"content": raw, "message_type": 101, "msg_elements": [
            {"message_type": 101, "msg_elements": [{"content": raw}]},
        ]}
        self.assertEqual(await self.media.capture(self.draft_id, "mentions", data, text),
                         (ReplyPart("text", text),))

    async def test_quote_and_chat_records_are_not_collected(self):
        for message_type in (102, 103):
            with self.subTest(message_type=message_type), self.assertRaises(KeywordMediaError):
                await self.media.capture(self.draft_id, "forwarded", {
                    "msg_elements": [{"message_type": message_type, "msg_elements": [
                        {"content": "another user's text"},
                    ]}],
                }, "")
        self.assertEqual(self.session.requests, [])

    async def test_rejects_untrusted_urls_before_opening_a_request(self):
        for url in (
            f"http://{CDN}/image", f"https://{CDN}.example.invalid/image",
            f"https://host.invalid@{CDN}/image", f"https://{CDN}@host.invalid/image",
            f"https://{CDN}:444/image", f"https://{CDN}/image#fragment",
            f"https://{CDN}\\path", "file:///etc/passwd",
        ):
            with self.subTest(url=url), self.assertRaises(KeywordMediaError):
                await self.media.capture(self.draft_id, "url", {"attachments": [attachment(url)]}, "")
        self.assertEqual(self.session.requests, [])

    async def test_rejects_redirects_and_oversize_content_length_without_reading(self):
        url = f"https://{CDN}/invalid"
        for response in (ImageResponse(status=302), ImageResponse(content_length=MAX_BYTES + 1)):
            self.session.responses[url] = response
            with self.assertRaises(KeywordMediaError):
                await self.media.capture(self.draft_id, "invalid", {"attachments": [attachment(url)]}, "")
            self.assertFalse(response.read)
        self.assertGreater(self.session.timeout.total, 0)
        self.assertGreater(self.session.timeout.sock_read, 0)

    async def test_rejects_stream_larger_than_declared_extent_and_removes_its_staging(self):
        url = f"https://{CDN}/huge"
        self.session.responses[url] = ImageResponse((b"x" * (1024 * 1024),) * 21, content_length=1)
        with self.assertRaises(KeywordMediaError):
            await self.media.capture(self.draft_id, "huge", {"attachments": [attachment(url)]}, "")
        self.assertFalse(any((self.data_dir / "keyword_media").rglob("*.*")))

    async def test_rejects_truncation_timeout_and_unsupported_file_without_losing_prior_message(self):
        good_url, original = self.add_image("cover", "PNG")
        prior = await self.media.capture(self.draft_id, "prior", {"attachments": [attachment(good_url)]}, "")
        bad_url = f"https://{CDN}/bad"
        for message, response in (
            ("truncated", ImageResponse((original[:len(original) // 2],))),
            ("timeout", ImageResponse((original[:12],), timeout=True)),
            ("unsupported", ImageResponse((image_bytes("WEBP"),))),
        ):
            self.session.responses[bad_url] = response
            with self.subTest(message=message), self.assertRaises(KeywordMediaError):
                await self.media.capture(self.draft_id, message, {
                    "attachments": [attachment(good_url), attachment(bad_url)],
                }, "")
        resolved = self.media.resolve(prior[0], self.draft_id)
        self.assertEqual(resolved.read_bytes(), original)
        self.assertEqual(list(resolved.parent.parent.iterdir()), [resolved.parent])

    async def test_rejects_missing_png_checksum_and_gif_trailer(self):
        url = f"https://{CDN}/truncated"
        for image_format in ("PNG", "GIF"):
            self.session.responses[url] = ImageResponse((image_bytes(image_format)[:-1],))
            with self.subTest(image_format=image_format), self.assertRaises(KeywordMediaError):
                await self.media.capture(self.draft_id, image_format, {
                    "attachments": [attachment(url)],
                }, "")

    async def test_resolve_checks_owner_relative_path_size_and_digest(self):
        url, original = self.add_image("valid", "PNG")
        part, = await self.media.capture(self.draft_id, "valid", {"attachments": [attachment(url)]}, "")
        path = self.media.resolve(part, self.draft_id)
        with self.assertRaises(KeywordMediaError):
            self.media.resolve(part, uuid4().hex)
        for media_path in (str(path), "../outside.png", part.media_path.replace("/", "\\")):
            with self.subTest(media_path=media_path), self.assertRaises(KeywordMediaError):
                self.media.resolve(ReplyPart("image", "", media_path, part.sha256), self.draft_id)
        with self.assertRaises(KeywordMediaError):
            self.media.resolve(ReplyPart("text", "", part.media_path, part.sha256), self.draft_id)
        with path.open("wb") as handle:
            handle.truncate(MAX_BYTES + 1)
        with self.assertRaises(KeywordMediaError):
            self.media.resolve(part, self.draft_id)
        path.write_bytes(b"X" + original[1:])
        with self.assertRaises(KeywordMediaError):
            self.media.resolve(part, self.draft_id)
        path.unlink()
        with self.assertRaises(KeywordMediaError):
            self.media.resolve(part, self.draft_id)

    async def test_cleanup_stays_within_its_draft_and_message(self):
        url, _ = self.add_image("valid", "PNG")
        data = {"attachments": [attachment(url)]}
        kept, = await self.media.capture(self.draft_id, "keep", data, "")
        deleted, = await self.media.capture(self.draft_id, "delete", data, "")
        other_draft = uuid4().hex
        other, = await self.media.capture(other_draft, "keep", data, "")
        with self.assertRaises(KeywordMediaError):
            self.media.discard("..")
        self.media.discard_message(self.draft_id, "delete")
        with self.assertRaises(KeywordMediaError):
            self.media.resolve(deleted, self.draft_id)
        self.assertTrue(self.media.resolve(kept, self.draft_id).exists())
        self.media.discard(self.draft_id)
        self.media.discard(self.draft_id)
        with self.assertRaises(KeywordMediaError):
            self.media.resolve(kept, self.draft_id)
        self.assertTrue(self.media.resolve(other, other_draft).exists())

    async def test_cleanup_rejects_a_draft_directory_link_outside_media_root(self):
        outside = self.data_dir / "outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        root = self.data_dir / "keyword_media"
        root.mkdir()
        link = root / self.draft_id
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc.winerror if hasattr(exc, 'winerror') else exc.errno}")
        with self.assertRaises(KeywordMediaError):
            self.media.discard(self.draft_id)
        self.assertTrue(marker.exists())
