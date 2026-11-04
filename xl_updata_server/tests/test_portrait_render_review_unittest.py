"""Independent review regression cases; stdlib and deterministic fault injection."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_portrait_render_unittest as fixtures
from server_app.portraits import render_protocol as protocol
from test_portrait_store_v2_unittest import result


class StrictResultTests(unittest.TestCase):
    def test_numeric_aliases_rejected_at_every_pinned_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = fixtures.EnvelopeTests().request(Path(tmp).resolve())
            j = r["job"]
            j["source_capture_ref"]["unassigned"] = 1
            p = r["package"]
            c = result(j, "success")
            c["reason"] = j["reason"]
            v = {
                "schema": "portrait-render-result/v1",
                "operation_id": r["operation_id"],
                "job_id": j["job_id"],
                "attempt": 1,
                "pin_digest": p["pin_digest"],
                "capture_sha256": p["capture_sha256"],
                "tool_fingerprint": p["tool_fingerprint"],
                "package_sha256": p["manifest_sha256"],
                "candidate": c,
            }
            with patch.object(protocol, "verify_media_header"):
                protocol.validate_result(v, r)
                for path in [
                    ("attempt",),
                    ("candidate", "attempt"),
                    ("candidate", "schema_version"),
                    ("candidate", "profile", "scale"),
                    ("candidate", "source_capture_ref", "unassigned"),
                    ("candidate", "source_capture_ref", "capture", "size"),
                ]:
                    for alias in (
                        True,
                        float(
                            2
                            if path[-1] in ("scale", "schema_version")
                            else 123
                            if path[-1] == "size"
                            else 1
                        ),
                    ):
                        bad = copy.deepcopy(v)
                        target = bad
                        for key in path[:-1]:
                            target = target[key]
                        target[path[-1]] = alias
                        with (
                            self.subTest(path=path, alias=alias),
                            self.assertRaises(ValueError),
                        ):
                            protocol.validate_result(bad, r)


@unittest.skipUnless(os.name == "posix", "actual flock requires Linux")
class LeaseCleanupTests(unittest.TestCase):
    def test_closed_reused_unlocked_and_other_ofd_retain_attempt(self):
        import fcntl

        from server_app.portraits.executor import (
            CleanupError,
            ResourcePolicy,
            ResourceSlot,
        )
        from server_app.portraits.package_prepare import _cleanup_attempt

        for kind in ("closed", "wrongfile", "unlocked", "other_ofd", "valid"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                area = root / "attempt"
                area.mkdir()
                (area / "kept").write_bytes(b"proof")
                slot = ResourceSlot(
                    ResourcePolicy(
                        lock_path=root / "lease",
                        updater_status_paths=(root / "updater",),
                        disk_roots=(root,),
                    )
                )
                fd = os.open(root / "lease", os.O_CREAT | os.O_RDWR, 0o600)
                slot.fd = fd
                extra = None
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if kind == "closed":
                    os.close(fd)
                elif kind == "wrongfile":
                    os.close(fd)
                    slot.fd = os.open(root / "wrong", os.O_CREAT | os.O_RDWR, 0o600)
                elif kind == "unlocked":
                    fcntl.flock(fd, fcntl.LOCK_UN)
                elif kind == "other_ofd":
                    extra = fd
                    slot.fd = os.open(root / "lease", os.O_RDWR)
                try:
                    if kind == "valid":
                        (root / "updater").write_text('{"updating":true}')
                        _cleanup_attempt(slot, area)
                        self.assertFalse(area.exists())
                    else:
                        with self.assertRaises(CleanupError):
                            _cleanup_attempt(slot, area)
                        self.assertTrue((area / "kept").exists())
                finally:
                    if kind != "closed":
                        os.close(slot.fd)
                    if extra is not None:
                        os.close(extra)


class WorkerOrderingTests(unittest.TestCase):
    def _exercise_mode(self, mode):
        from contextlib import ExitStack, nullcontext
        from types import ModuleType

        from server_app.portraits import render_worker as worker
        from server_app.portraits.executor import CleanupError, Deferred

        with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            from test_portrait_bundle_unittest import fixture as bundle_fixture
            bundle_config, _ = bundle_fixture(root / "runtime-inputs")
            descriptor = json.loads(Path(bundle_config["descriptor_path"]).read_bytes())
            request = fixtures.EnvelopeTests().request(root)
            attempt = Path(request["attempt_root"])
            attempt.mkdir(parents=True)
            package = protocol.package_from_wire(request["package"])
            job = request["job"]
            events = []
            state = {"stop": False}
            launch = SimpleNamespace(display=None, env={})

            def guard(_):
                if state["stop"]:
                    raise Deferred("updater")

            def prepare(*args, **kw):
                launch.cancel_check = kw["cancel_check"]
                return launch

            class Display:
                def __init__(self, obj):
                    self.launch = obj

                def __enter__(self):
                    self.launch.display = self
                    return self.launch

                def __exit__(self, *exc):
                    if mode in ("closefail", "bothfail", "latchedclosefail"):
                        raise CleanupError("display close failed")
                    self.launch.display = None
                    events.append("display-reaped")

            def render(payload, *, launch):
                events.append("native-start")
                if mode in ("nativefail", "bothfail"):
                    raise CleanupError("native cleanup failed")
                events.append("native-reaped")
                raw = {
                    "schema_version": 1,
                    "status": "success",
                    "job_id": job["job_id"],
                    "png_working_rows": 128,
                    "resource_mode": "disk_rows",
                    "staged_path": str(attempt / "output" / (job["job_id"] + ".png")),
                }
                if mode in ("latched", "latchedclosefail"):
                    state["stop"] = True
                    self.assertTrue(launch.cancel_check())
                    raw["status"] = "error"
                return raw

            native = ModuleType("app.platform.portrait_linux_runtime")
            native.LinuxManagedRuntime = lambda **kw: SimpleNamespace(prepare=prepare, **kw)
            native.PrivateDisplay = Display
            native.RuntimeFile = lambda **kw: SimpleNamespace(**kw)
            packaged = ModuleType("app.features.preview.portrait_package")
            packaged.load_portrait_package = lambda _: SimpleNamespace(
                scene=SimpleNamespace(
                    version_timestamp=job["version"],
                    character_id=job["character_id"],
                    skin_id=job["skin_id"],
                )
            )
            headless = ModuleType("app.features.preview.portrait_headless")
            headless.run_request = render
            with ExitStack() as stack:
                stack.enter_context(
                    patch.dict(
                        "sys.modules",
                        {
                            native.__name__: native,
                            packaged.__name__: packaged,
                            headless.__name__: headless,
                        },
                    )
                )
                for name, value in [
                    (
                        "validate_request",
                        (job, package, attempt, root / "media", object()),
                    ),
                    ("_directory", nullcontext(0)),
                    ("read_package", b"{}"),
                    ("read_bound_file", b"{}"),
                    ("_load", descriptor),
                ]:
                    stack.enter_context(patch.object(worker, name, return_value=value))
                stack.enter_context(patch.object(worker, "_guard", side_effect=guard))
                stack.enter_context(
                    patch.object(worker.time, "monotonic", return_value=1)
                )
                actual_listdir = os.listdir
                stack.enter_context(patch.object(worker.os, "listdir", side_effect=lambda path: [] if path == 0 else actual_listdir(path)))
                stack.enter_context(
                    patch.object(Path, "read_text", return_value="0 1000 1")
                )
                stack.enter_context(
                    patch("server_app.portraits.private_namespace.prepare_namespace")
                )
                stack.enter_context(patch.object(worker, "validate_result"))
                stack.enter_context(patch.object(worker, "_publish"))
                publish = stack.enter_context(
                    patch(
                        "server_app.portraits.publisher.publish_png",
                        side_effect=lambda *a, **kw: events.append("publish") or {},
                    )
                )
                if mode == "success":
                    worker.run_request(
                        request,
                        request_path=attempt.parent / "render-request.json",
                        result_path=attempt / "result.json",
                    )
                    self.assertEqual(
                        events,
                        ["native-start", "native-reaped", "display-reaped", "publish"],
                    )
                    publish.assert_called_once()
                else:
                    expected = (
                        Deferred
                        if mode in ("latched", "latchedclosefail")
                        else CleanupError
                    )
                    with self.assertRaises(expected):
                        worker.run_request(
                            request,
                            request_path=attempt.parent / "render-request.json",
                            result_path=attempt / "result.json",
                        )
                    publish.assert_not_called()

    def test_publisher_only_after_native_and_display_quiescence(self):
        for mode in (
            "success",
            "nativefail",
            "closefail",
            "bothfail",
            "latched",
            "latchedclosefail",
        ):
            self._exercise_mode(mode)


if __name__ == "__main__":
    unittest.main()
