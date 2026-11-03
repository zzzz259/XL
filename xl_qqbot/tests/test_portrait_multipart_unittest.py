import hashlib
import tempfile
import unittest
from pathlib import Path

from bot_app.gateway import GatewayTransportClient
from bot_app.notification_service import NotificationService
from bot_app.proactive_outbox import ProactiveOutbox
from bot_app.sender import QQSender
from PIL import Image


class PortraitMultipartFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "image.png"
        Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(self.path)
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.box = ProactiveOutbox(self.root / "queue")


class PortraitMultipartTests(PortraitMultipartFixture):
    def test_generic_capability(self):
        self.assertTrue(callable(getattr(QQSender, "send_local_image", None)))
        self.assertTrue(
            callable(getattr(GatewayTransportClient, "send_local_image", None))
        )

    def test_persisted_mode_and_immutable_identity(self):
        self.assertIn(
            "image_delivery_mode",
            __import__("inspect").signature(self.box.enqueue_image).parameters,
        )
        key = self.box.enqueue_image(
            "portrait:1",
            "g",
            0,
            self.path,
            image_delivery_mode="local_multipart",
            expected_sha256=self.sha,
        )
        restarted = ProactiveOutbox(self.box.data_dir)
        self.assertEqual(restarted.get(key).image_delivery_mode, "local_multipart")
        self.assertEqual(
            key,
            restarted.enqueue_image(
                "portrait:1",
                "g",
                0,
                self.path,
                image_delivery_mode="local_multipart",
                expected_sha256=self.sha,
            ),
        )
        with self.assertRaises(ValueError):
            restarted.enqueue_image("portrait:1", "g", 0, self.path)
        self.path.unlink()
        self.assertTrue((restarted.data_dir / restarted.get(key).media_path).is_file())

    def test_explicit_root_and_hash_wire(self):
        self.assertIn(
            "portrait_roots",
            __import__("inspect").signature(NotificationService).parameters,
        )
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        self.path.replace(artifacts / (self.sha + ".png"))
        service = NotificationService(
            instance_id="test",
            allowed_groups=["g"],
            outbox=self.box,
            portrait_roots={"game": str(self.root)},
        )
        message = {
            "kind": "image",
            "recipient": "g",
            "ordinal": 0,
            "image_delivery_mode": "local_multipart",
            "source_id": "game",
            "artifact_path": "artifacts/" + self.sha + ".png",
            "expected_sha256": self.sha,
        }
        payload = {
            "event_key": "portrait:1",
            "instance_id": "test",
            "messages": [message],
        }
        self.assertEqual(service.enqueue(payload)[0], 200)
        self.assertEqual(
            self.box.list_pending()[0].image_delivery_mode, "local_multipart"
        )
        for patch in (
            {"artifact_path": "../image.png"},
            {"media_path": str(self.path)},
            {"source_id": "other"},
            {"expected_sha256": "0" * 64},
            {"image_delivery_mode": "other"},
        ):
            with self.subTest(patch=patch):
                self.assertEqual(
                    service.enqueue({**payload, "messages": [{**message, **patch}]})[0],
                    400,
                )


class PortraitMultipartConfigTests(unittest.TestCase):
    def test_default_off_and_config_mapping(self):
        from bot_app.config import NotificationConfig, load_config

        self.assertEqual(getattr(NotificationConfig(), "portrait_roots", None), {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                '[watch]\noutbox_dir="outbox"\n[notification.portrait_roots.test]\ngame="'
                + directory.replace("\\", "/")
                + '"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                load_config(path).notification.portrait_roots,
                {"test": {"game": directory.replace("\\", "/")}},
            )


class PortraitMultipartSafetyTests(PortraitMultipartFixture):
    def test_source_changes_during_copy(self):
        original = self.box._copy_to_temporary_spool

        def changed(source, **kwargs):
            Image.new("RGBA", (2, 2), (4, 5, 6, 255)).save(source)
            return original(source, **kwargs)

        self.box._copy_to_temporary_spool = changed
        with self.assertRaises(ValueError):
            self.box.enqueue_image(
                "p:race",
                "g",
                0,
                self.path,
                image_delivery_mode="local_multipart",
                expected_sha256=self.sha,
            )
        self.assertEqual(self.box.count(), 0)
        self.assertEqual(list(self.box.spool_dir.iterdir()), [])

    def test_corrupt_truncated_animated_invisible_and_budget_rejected(self):
        from unittest.mock import patch

        from bot_app.local_image import validate_png_file

        good = self.path.read_bytes()
        for bad in (b"not png", good[:-4], good + b"trailing"):
            self.path.write_bytes(bad)
            with self.assertRaises(ValueError):
                validate_png_file(self.path, hashlib.sha256(bad).hexdigest())
        Image.new("RGBA", (2, 2), (1, 1, 1, 0)).save(self.path)
        with self.assertRaises(ValueError):
            validate_png_file(
                self.path, hashlib.sha256(self.path.read_bytes()).hexdigest()
            )
        Image.new("RGBA", (2, 2), (1, 1, 1, 255)).save(
            self.path,
            save_all=True,
            append_images=[Image.new("RGBA", (2, 2), (2, 2, 2, 255))],
            duration=100,
        )
        with self.assertRaises(ValueError):
            validate_png_file(
                self.path, hashlib.sha256(self.path.read_bytes()).hexdigest()
            )
        self.path.write_bytes(good)
        for name, limit in (("MAX_BYTES", 10), ("MAX_PIXELS", 1)):
            with (
                patch("bot_app.local_image." + name, limit),
                self.assertRaises(ValueError),
            ):
                validate_png_file(self.path, self.sha)

    def test_migration_defaults_existing_rows_to_legacy(self):
        import sqlite3

        key = self.box.enqueue_image("old:1", "g", 0, self.path)
        from contextlib import closing

        with closing(sqlite3.connect(self.box.database_path)) as db:
            db.execute("ALTER TABLE messages DROP COLUMN image_delivery_mode")
            db.commit()
        restarted = ProactiveOutbox(self.box.data_dir)
        self.assertEqual(restarted.get(key).image_delivery_mode, "legacy")
        self.assertEqual(
            ProactiveOutbox(self.box.data_dir).get(key).media_sha256, self.sha
        )
        with self.assertRaises(ValueError):
            restarted.enqueue_image(
                "old:1",
                "g",
                0,
                self.path,
                image_delivery_mode="local_multipart",
                expected_sha256=self.sha,
            )

    def test_batch_validated_before_any_enqueue_and_text_mode_rejected(self):
        service = NotificationService(
            instance_id="test", allowed_groups=["g"], outbox=self.box
        )
        payload = {
            "event_key": "p:1",
            "instance_id": "test",
            "messages": [
                {"kind": "text", "recipient": "g", "ordinal": 0, "text": "ok"},
                {
                    "kind": "text",
                    "recipient": "g",
                    "ordinal": 1,
                    "text": "no",
                    "image_delivery_mode": "local_multipart",
                },
            ],
        }
        self.assertEqual(service.enqueue(payload)[0], 400)
        self.assertEqual(self.box.count(), 0)
        payload["messages"] = [payload["messages"][0]] * 101
        self.assertEqual(service.enqueue(payload)[0], 400)
        payload["messages"] = [payload["messages"][0]]
        payload["instance_id"] = "foreign"
        self.assertEqual(service.enqueue(payload)[0], 403)


class PortraitMultipartTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_sender_local_uses_multipart_with_url_configured(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        sender = QQSender.__new__(QQSender)
        sender.config = SimpleNamespace(
            upload=SimpleNamespace(file_base_url="https://cdn.example")
        )
        sender.http = SimpleNamespace(
            request=AsyncMock(return_value={"file_info": "url"})
        )
        sender._to_public_url = lambda path: "https://cdn.example/image.png"
        sender._upload_by_multipart = AsyncMock(return_value={"file_info": "bytes"})
        self.assertEqual(
            await sender._upload_group_file("g", "unused"), {"file_info": "url"}
        )
        self.assertEqual(
            await sender._upload_group_file("g", "unused", force_multipart=True),
            {"file_info": "bytes"},
        )
        sender._send_to_group = AsyncMock(return_value=True)
        await sender.send_local_image("path", "caption", ["g"])
        sender._send_to_group.assert_awaited_once_with(
            "path", "caption", "g", "", force_multipart=True
        )
        sender._send_to_group.reset_mock()
        await sender.send_schedule_image("path", "", ["g"])
        sender._send_to_group.assert_awaited_once_with(
            "path", "", "g", "", force_multipart=True
        )

    async def test_gateway_and_dispatcher_select_explicit_mode(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from bot_app.proactive_dispatcher import ProactiveDispatcher

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(path)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            client = GatewayTransportClient(
                "http://127.0.0.1:12345", bearer_token="x" * 32, instance_id="test"
            )
            client._send = AsyncMock(return_value=True)
            await client.send_local_image(str(path), "", ["g"])
            self.assertTrue(client._send.call_args.args[0]["force_multipart"])
            await client.send_image(str(path), "", ["g"])
            self.assertFalse(client._send.call_args.args[0]["force_multipart"])
            box = ProactiveOutbox(Path(directory) / "queue")
            key = box.enqueue_image(
                "p:1",
                "g",
                0,
                path,
                image_delivery_mode="local_multipart",
                expected_sha256=sha,
            )
            sender = SimpleNamespace(
                send_local_image=AsyncMock(return_value={"g": False}),
                send_image=AsyncMock(return_value={"g": True}),
            )
            dispatcher = ProactiveDispatcher(box, sender)
            self.assertFalse(await dispatcher._send(box.get(key)))
            sender.send_image.assert_not_awaited()
            self.assertEqual(box.get(key).status, "pending")
            sender.send_local_image.return_value = {"g": True}
            self.assertTrue(await dispatcher._send(box.get(key)))
            del sender.send_local_image
            with self.assertRaises(AttributeError):
                await dispatcher._send(box.get(key))


class PortraitMultipartIntakeBoundaries(PortraitMultipartFixture):
    def make_service_payload(self):
        (self.root / "artifacts").mkdir(exist_ok=True)
        artifact = self.root / "artifacts" / (self.sha + ".png")
        artifact.write_bytes(self.path.read_bytes())
        service = NotificationService(
            instance_id="test",
            allowed_groups=["g"],
            outbox=self.box,
            portrait_roots={"game": str(self.root)},
        )
        item = {
            "kind": "image",
            "recipient": "g",
            "ordinal": 0,
            "image_delivery_mode": "local_multipart",
            "source_id": "game",
            "artifact_path": "artifacts/" + self.sha + ".png",
            "expected_sha256": self.sha,
        }
        return service, {
            "event_key": "p:batch",
            "instance_id": "test",
            "messages": [item],
        }

    def test_strict_envelope_and_invalid_config_values(self):
        service, payload = self.make_service_payload()
        self.assertEqual(service.enqueue({**payload, "source_id": "ambiguous"})[0], 400)

    def test_invalid_empty_root_mapping_rejected(self):
        with self.assertRaises(ValueError):
            NotificationService(
                instance_id="test",
                allowed_groups=["g"],
                outbox=self.box,
                portrait_roots=[],
            )

    def test_partial_prefix_exact_retry_and_changed_content_conflict(self):
        from unittest.mock import patch

        service, payload = self.make_service_payload()
        payload["messages"].append({**payload["messages"][0], "ordinal": 1})
        original = self.box.enqueue_image

        def fail_second(*args, **kwargs):
            if args[2] == 1:
                raise OSError("injected append failure")
            return original(*args, **kwargs)

        with patch.object(self.box, "enqueue_image", side_effect=fail_second):
            self.assertEqual(service.enqueue(payload)[0], 400)
        self.assertEqual(self.box.count(), 1)
        self.assertEqual(service.enqueue(payload)[0], 200)
        self.assertEqual(self.box.count(), 2)
        payload["messages"][0]["text"] = "changed"
        self.assertEqual(service.enqueue(payload)[0], 400)
        self.assertEqual(self.box.count(), 2)

    def test_path_variants_and_group_and_default_off(self):
        service, payload = self.make_service_payload()
        item = payload["messages"][0]
        for bad in (
            str(self.path),
            "C:/artifacts/a.png",
            "../artifacts/" + self.sha + ".png",
            "artifacts\\" + self.sha + ".png",
        ):
            self.assertEqual(
                service.enqueue(
                    {**payload, "messages": [{**item, "artifact_path": bad}]}
                )[0],
                400,
            )
        self.assertEqual(
            service.enqueue(
                {**payload, "messages": [{**item, "recipient": "foreign"}]}
            )[0],
            403,
        )
        off = NotificationService(
            instance_id="test", allowed_groups=["g"], outbox=self.box
        )
        self.assertEqual(off.enqueue(payload)[0], 400)

    def test_links_are_rejected(self):
        from unittest.mock import patch

        service, payload = self.make_service_payload()
        # Deterministic cross-platform guard checks; no symlink privilege required.
        with patch.object(Path, "is_symlink", return_value=True):
            self.assertEqual(service.enqueue(payload)[0], 400)
        with patch.object(Path, "is_junction", return_value=True):
            self.assertEqual(service.enqueue(payload)[0], 400)


class PortraitMultipartGatewayRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_owned_route_uses_generic_and_cleans_up(self):
        import base64
        import json
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from bot_app.gateway import build_gateway_control_app

        with tempfile.TemporaryDirectory() as directory:
            sender = SimpleNamespace(
                send_local_image=AsyncMock(return_value={"g": True}),
                send_image=AsyncMock(return_value={"g": True}),
            )
            app = build_gateway_control_app(
                SimpleNamespace(scope_ids=["test"]),
                bearer_token="x" * 32,
                instance_tokens={"test": "y" * 32},
                group_instances={"g": "test"},
                sender=sender,
                media_dir=Path(directory),
            )
            handler = next(
                route.handler
                for route in app.router.routes()
                if route.method == "POST"
                and str(route.resource.canonical) == "/gateway/messages"
            )
            payload = {
                "instance_id": "test",
                "recipient": "g",
                "kind": "image",
                "media_base64": base64.b64encode(b"local bytes").decode(),
                "media_name": "image.png",
                "force_multipart": True,
            }
            request = SimpleNamespace(
                headers={
                    "Authorization": "Bearer " + "y" * 32,
                    "X-Instance-ID": "test",
                },
                path="/gateway/messages",
                json=AsyncMock(return_value=payload),
            )
            response = await app.middlewares[0](request, handler)
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.body)["sent"])
            sender.send_local_image.assert_awaited_once()
            sender.send_image.assert_not_awaited()
            self.assertEqual(list(Path(directory).iterdir()), [])
            request.headers["Authorization"] = "Bearer wrong"
            self.assertEqual((await app.middlewares[0](request, handler)).status, 401)
            request.headers["Authorization"] = "Bearer " + "y" * 32
            payload["recipient"] = "foreign"
            self.assertEqual((await app.middlewares[0](request, handler)).status, 403)
            payload["recipient"] = "g"
            payload["instance_id"] = "foreign"
            self.assertEqual((await app.middlewares[0](request, handler)).status, 403)


class PortraitMultipartConfigRejectionTests(unittest.TestCase):
    def test_roots_require_known_instance_and_canonical_source_and_absolute_root(self):
        from bot_app.config import load_config

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            root = directory.replace("\\", "/")
            for owner, source, target in (
                ("foreign", "game", root),
                ("test", "BadSource", root),
                ("test", "game", "relative"),
            ):
                path.write_text(
                    '[watch]\noutbox_dir="outbox"\n[notification.portrait_roots.'
                    + owner
                    + "]\n"
                    + source
                    + '="'
                    + target
                    + '"\n',
                    encoding="utf-8",
                )
                with (
                    self.subTest(owner=owner, source=source, target=target),
                    self.assertRaises(ValueError),
                ):
                    load_config(path)


class PortraitMultipartQueryGenericTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_prefers_generic_without_changing_caption_or_ordinal(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock

        from bot_app.query_handler import QueryHandler
        from bot_app.selection import SelectionStore

        sender = SimpleNamespace(
            send_local_image=AsyncMock(return_value={"g": True}),
            send_schedule_image=AsyncMock(return_value={"g": True}),
            send_image=AsyncMock(),
            send_text=AsyncMock(),
        )
        reader = SimpleNamespace(
            find=Mock(
                return_value={
                    "status": "stale",
                    "image_path": "/safe/a.png",
                    "source_version": "v1",
                    "catalog_version": "v2",
                }
            )
        )
        handler = QueryHandler(
            None, None, SelectionStore(), sender, portrait_querier=reader
        )
        await handler._send_portrait("g", SimpleNamespace(character_id="7"), 3, "reply")
        reader.find.assert_called_once_with("7", 3)
        sender.send_local_image.assert_awaited_once_with(
            "/safe/a.png", "立绘为旧版本 v1（当前目录版本 v2）", ["g"], "reply"
        )
        sender.send_schedule_image.assert_not_awaited()
        sender.send_image.assert_not_awaited()


class PortraitMultipartDecoderBoundaryTests(unittest.TestCase):
    @staticmethod
    def png_bytes():
        import io

        stream = io.BytesIO()
        Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(stream, format="PNG")
        return stream.getvalue()

    def test_duplicate_ihdr_rejected_before_full_decode(self):
        import io
        import struct
        import zlib
        from unittest.mock import patch

        from bot_app.local_image import validate_png_file
        from PIL.PngImagePlugin import PngImageFile

        original = self.png_bytes()
        header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
        chunk = (
            struct.pack(">I", len(header))
            + b"IHDR"
            + header
            + struct.pack(">I", zlib.crc32(b"IHDR" + header))
        )
        data = original[:8] + chunk + original[8:]
        original_load = PngImageFile.load
        with (
            patch.object(Path, "open", return_value=io.BytesIO(data)),
            patch("bot_app.local_image.MAX_PIXELS", 1),
            patch.object(
                PngImageFile, "load", autospec=True, side_effect=original_load
            ) as load,
        ):
            with self.assertRaises(ValueError):
                validate_png_file("unused", hashlib.sha256(data).hexdigest())
            load.assert_not_called()

    def test_decoder_metadata_must_agree_before_verify_and_load(self):
        import io
        from unittest.mock import MagicMock, patch

        from bot_app.local_image import validate_png_file

        data = self.png_bytes()
        for phase in (0, 1):
            for changed in (
                {"size": (1, 4)},
                {"format": "JPEG"},
                {"n_frames": 2},
                {"is_animated": True},
            ):
                with self.subTest(phase=phase, changed=changed):
                    images = []
                    for _ in range(2):
                        image = MagicMock()
                        image.size = (2, 2)
                        image.format = "PNG"
                        image.n_frames = 1
                        image.is_animated = False
                        image.__enter__.return_value = image
                        images.append(image)
                    for name, value in changed.items():
                        setattr(images[phase], name, value)
                    with (
                        patch.object(Path, "open", return_value=io.BytesIO(data)),
                        patch("bot_app.local_image.Image.open", side_effect=images),
                    ):
                        with self.assertRaises(ValueError):
                            validate_png_file(
                                "unused", hashlib.sha256(data).hexdigest()
                            )
                        for image in images:
                            image.load.assert_not_called()
