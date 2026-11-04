import dataclasses
import importlib
import unittest
from pathlib import Path

from server_app.portraits.model import digest
from server_app.portraits.package_prepare import PreparedPackageRef
from test_portrait_store_v2_unittest import plan


class RenderTests(unittest.TestCase):
    def api(self):
        try:
            return importlib.import_module("server_app.portraits.render_protocol")
        except ModuleNotFoundError:
            self.fail("missing render protocol")

    def fixture(self):
        j = dict(plan()["jobs"][0], attempt=1)
        p = PreparedPackageRef(
            "portrait-prepared-package/v1",
            digest(["i", "s"]),
            "a" * 64,
            "v",
            "d" * 64,
            "b" * 64,
            "c" * 64,
            "1",
            "2",
            "a" * 64,
            Path("/private/input/skin.json"),
            Path("/private/input"),
            123,
            "e" * 64,
            (),
        )
        return j, p

    def test_binding(self):
        j, p = self.fixture()
        self.assertEqual(self.api().bind_package(j, p), j)

    def test_each_package_binding_is_required(self):
        j, p = self.fixture()
        for key in (
            "scope_key",
            "pin_digest",
            "version",
            "capture_sha256",
            "snapshot_digest",
            "tool_fingerprint",
            "character_id",
            "skin_id",
            "render_key",
            "schema",
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.api().bind_package(j, dataclasses.replace(p, **{key: "wrong"}))

    def test_explicit_v2_attempt_and_capability(self):
        j, p = self.fixture()
        for bad in (
            dict(j, attempt=0),
            dict(j, schema_version=1),
            {k: v for k, v in j.items() if k != "attempt"},
            dict(j, profile={}),
        ):
            with self.assertRaises(ValueError):
                self.api().bind_package(bad, p)
        with self.assertRaises(TypeError):
            self.api().bind_package(j, dataclasses.asdict(p))

    def test_worker_preheavy_unknown_request(self):
        self.api()
        from server_app.portraits.render_worker import run_request

        for value in ({}, None, {"schema": "portrait-render/v1", "unexpected": 1}):
            with self.assertRaises(ValueError):
                run_request(value, request_path=Path("/x"), result_path=Path("/y"))


class EnvelopeTests(RenderTests):
    def request(self, root):
        from server_app.portraits.executor import ResourcePolicy

        j, p = self.fixture()
        p = dataclasses.replace(
            p, input_root=root / "input", package_path=root / "input/skin.json"
        )
        policy = dataclasses.asdict(
            ResourcePolicy(
                lock_path=root / "lease",
                updater_status_paths=(root / "guard",),
                disk_roots=(root,),
            )
        )
        policy["lock_path"] = str(policy["lock_path"])
        for key in ("updater_status_paths", "disk_roots"):
            policy[key] = list(map(str, policy[key]))
        return {
            "schema": "portrait-render/v1",
            "operation_id": "f" * 64,
            "job": j,
            "package": self.api().package_wire(p),
            "attempt_root": str(root / "attempt/render"),
            "data_root": str(root / "media"),
            "runtime_manifest": {
                "relative_path": "runtime.json",
                "size": 1,
                "sha256": "a" * 64,
            },
            "caller_uid": 1000,
            "namespace_tools": {"/usr/bin/mount": "a" * 64, "/usr/bin/sh": "b" * 64},
            "python": str(root / "python"),
            "python_sha256": "c" * 64,
            "deadline": 1000,
            "policy": policy,
        }

    def test_envelope_and_policy(self):
        import copy
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            r = self.request(Path(tmp).resolve())
            self.api().validate_request(r)
            for key in r:
                v = copy.deepcopy(r)
                v.pop(key)
                with self.subTest(missing=key), self.assertRaises(ValueError):
                    self.api().validate_request(v)
            for key, value in [
                ("data_root", r["package"]["input_root"]),
                ("deadline", float("nan")),
                ("caller_uid", 0),
                ("extra", True),
            ]:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    self.api().validate_request(dict(r, **{key: value}))
            for key, value in [
                ("memory_bytes", 536870913),
                ("admission_bytes", 1),
                ("abort_bytes", 1),
                ("disk_free_bytes", 1),
                ("poll_seconds", 1),
                ("updater_status_paths", []),
            ]:
                v = copy.deepcopy(r)
                v["policy"][key] = value
                with self.subTest(policy=key), self.assertRaises(ValueError):
                    self.api().validate_request(v)

    def test_forged_result_and_candidate(self):
        import copy
        import tempfile
        from unittest.mock import patch

        from test_portrait_store_v2_unittest import result

        with tempfile.TemporaryDirectory() as tmp:
            r = self.request(Path(tmp).resolve())
            j = r["job"]
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
            with patch("server_app.portraits.render_protocol.verify_media_header"):
                self.assertEqual(self.api().validate_result(v, r), c)
                for key in v:
                    bad = copy.deepcopy(v)
                    bad[key] = None
                    with (
                        self.subTest(result=key),
                        self.assertRaises((ValueError, TypeError)),
                    ):
                        self.api().validate_result(bad, r)
                for key in c:
                    bad = copy.deepcopy(v)
                    bad["candidate"][key] = None

                    with (
                        self.subTest(candidate=key),
                        self.assertRaises((ValueError, TypeError)),
                    ):
                        self.api().validate_result(bad, r)
                with self.assertRaises(ValueError):
                    self.api().validate_result(
                        dict(v, candidate=dict(c, accepted=True)), r
                    )

    def test_parent_rejects_invalid_slot_and_pure_import(self):
        import subprocess
        import sys

        from server_app.portraits.executor import BoundaryError
        from server_app.portraits.render_prepare import render_candidate

        with self.assertRaises(BoundaryError):
            render_candidate(
                None,
                None,
                None,
                runtime=None,
                package_runtime=None,
                data_root=None,
                work_root=None,
                stop_event=None,
            )
        run = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys;from server_app.portraits import render_prepare;assert not any(m.split('.')[0] in ('PIL','UnityPy','numpy','PyQt5','PySide6') for m in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)


class GuardTests(EnvelopeTests):
    def test_worker_guard_precedes_runtime_and_namespace(self):
        import tempfile
        from unittest.mock import patch

        from server_app.portraits import render_worker as worker
        from server_app.portraits.executor import BoundaryError, Deferred
        from server_app.portraits.source_prepare import PreparationError

        with tempfile.TemporaryDirectory() as tmp:
            r = self.request(Path(tmp).resolve())
            attempt = Path(r["attempt_root"])
            for error in (
                BoundaryError("OFD"),
                Deferred("updater"),
                Deferred("resource"),
            ):
                with (
                    patch.object(worker, "_guard", side_effect=error),
                    patch.object(worker, "read_package") as read,
                ):
                    with self.assertRaises(type(error)):
                        worker.run_request(
                            r,
                            request_path=attempt.parent / "render-request.json",
                            result_path=attempt / "result.json",
                        )
                    read.assert_not_called()
            with (  # noqa: SIM117 - keep asserted failure separate from guard setup
                patch.object(worker, "_guard"),
                patch.object(worker.time, "monotonic", return_value=1001),
            ):
                with self.assertRaises(PreparationError):
                    worker.run_request(
                        r,
                        request_path=attempt.parent / "render-request.json",
                        result_path=attempt / "result.json",
                    )
            with (  # noqa: SIM117 - keep asserted failure separate from guard setup
                patch.object(worker, "_guard"),
                patch.object(worker.time, "monotonic", return_value=1),
                patch.object(Path, "read_text", return_value="0 0 4294967295"),
            ):
                with self.assertRaises(BoundaryError):
                    worker.run_request(
                        r,
                        request_path=attempt.parent / "render-request.json",
                        result_path=attempt / "result.json",
                    )

    def test_parent_owner_cancel_and_deadline_before_source_read(self):
        import os
        import tempfile
        import threading
        from unittest.mock import patch

        from server_app.portraits import render_prepare as parent
        from server_app.portraits.executor import (
            BoundaryError,
            CleanupError,
            Deferred,
            ResourcePolicy,
            ResourceSlot,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            slot = ResourceSlot(
                ResourcePolicy(
                    lock_path=root / "lease",
                    updater_status_paths=(root / "guard",),
                    disk_roots=(root,),
                )
            )
            args = {
                "runtime": None,
                "package_runtime": None,
                "data_root": root / "media",
                "work_root": root,
                "stop_event": None,
            }
            with self.assertRaises(CleanupError):
                parent.render_candidate(slot, None, None, **args)
            slot.owner_thread = -1
            with self.assertRaises(BoundaryError):
                parent.render_candidate(slot, None, None, **args)
            slot.owner_thread = threading.get_ident()
            slot.fd = os.open(root / "fd", os.O_CREAT | os.O_RDWR)
            try:
                with (
                    patch.object(parent, "verify_resource_boundary"),
                    patch.object(parent, "check_admission"),
                    patch.object(parent, "read_package") as read,
                ):
                    stop = threading.Event()
                    stop.set()
                    args["stop_event"] = stop
                    with self.assertRaises(Deferred):
                        parent.render_candidate(slot, None, None, **args)
                    read.assert_not_called()
                    slot.poisoned = True
                    with self.assertRaises(CleanupError):
                        parent.render_candidate(slot, None, None, **args)
            finally:
                os.close(slot.fd)

    def test_guard_reason_precedes_nonzero_exit(self):
        from types import SimpleNamespace

        from server_app.portraits.executor import BoundaryError, Deferred
        from server_app.portraits.source_prepare import PreparationError, check_stage

        for reason, error in [
            ("timeout", PreparationError),
            ("updater", Deferred),
            ("stop", Deferred),
            ("memory", Deferred),
            ("descendants", BoundaryError),
        ]:
            with self.subTest(reason=reason), self.assertRaises(error):
                check_stage(SimpleNamespace(reason=reason, returncode=1))


class CandidateSemanticsTests(EnvelopeTests):
    def test_diagnostics_must_be_bounded_string_list(self):
        from server_app.portraits import render_protocol as p

        self.assertTrue(
            hasattr(p, "validate_candidate_semantics"),
            "missing candidate semantic guard",
        )
        from test_portrait_store_v2_unittest import result

        j, _ = self.fixture()
        c = result(j, "success")
        p.validate_candidate_semantics(c)
        for bad in (
            dict(c, diagnostics=None),
            dict(c, archive_width=64 * 1024 * 1024 + 1),
            dict(c, width=3),
            dict(c, artifact_sha256="a" * 64),
        ):
            with self.assertRaises(ValueError):
                p.validate_candidate_semantics(bad)


class IdleHeapTests(unittest.TestCase):
    def test_only_idle_owned_slot_can_trim_and_live_objects_survive(self):
        import ctypes
        import os
        import tempfile
        import threading
        from unittest.mock import MagicMock, patch

        from server_app.portraits import render_prepare as parent
        from server_app.portraits.executor import (
            BoundaryError,
            ResourcePolicy,
            ResourceSlot,
        )

        self.assertTrue(
            hasattr(parent, "_release_idle_heap"), "missing owned idle heap release"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            slot = ResourceSlot(
                ResourcePolicy(
                    lock_path=root / "lease",
                    updater_status_paths=(root / "guard",),
                    disk_roots=(root,),
                )
            )
            slot.fd = os.open(root / "fd", os.O_CREAT | os.O_RDWR)
            live = bytearray(b"x" * 10000)
            library = MagicMock()
            library.malloc_trim.return_value = 0
            order = []
            try:
                with (
                    patch.object(parent.sys, "platform", "linux"),
                    patch("gc.collect", side_effect=lambda: order.append("gc")),
                    patch("ctypes.CDLL", return_value=library) as cdll,
                ):
                    library.malloc_trim.side_effect = lambda n: (
                        order.append(("trim", n)) or 0
                    )
                    self.assertEqual(parent._release_idle_heap(slot), 0)
                    cdll.assert_called_once_with(None)
                    self.assertEqual(order, ["gc", ("trim", 0)])
                    self.assertEqual(library.malloc_trim.argtypes, [ctypes.c_size_t])
                    self.assertEqual(library.malloc_trim.restype, ctypes.c_int)
                    self.assertEqual(live, b"x" * 10000)
                    for bad in ("poisoned", "process", "owner"):
                        slot.poisoned = bad == "poisoned"
                        slot.process = object() if bad == "process" else None
                        slot.owner_thread = (
                            -1 if bad == "owner" else threading.get_ident()
                        )
                        with self.assertRaises(BoundaryError):
                            parent._release_idle_heap(slot)
                    self.assertEqual(len(order), 2)
            finally:
                os.close(slot.fd)

    def test_missing_symbol_has_typed_failure(self):
        import os
        import tempfile
        from unittest.mock import patch

        from server_app.portraits import render_prepare as parent
        from server_app.portraits.executor import (
            BoundaryError,
            ResourcePolicy,
            ResourceSlot,
        )

        self.assertTrue(
            hasattr(parent, "_release_idle_heap"), "missing owned idle heap release"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            slot = ResourceSlot(
                ResourcePolicy(
                    lock_path=root / "lease",
                    updater_status_paths=(root / "guard",),
                    disk_roots=(root,),
                )
            )
            slot.fd = os.open(root / "fd", os.O_CREAT | os.O_RDWR)
            try:
                with (
                    patch.object(parent.sys, "platform", "linux"),
                    patch("ctypes.CDLL", return_value=object()),
                    self.assertRaises(BoundaryError),
                ):
                    parent._release_idle_heap(slot)
            finally:
                os.close(slot.fd)


class MediaHeaderTests(unittest.TestCase):
    def test_parent_checks_header_without_decode(self):
        import hashlib
        import struct
        import tempfile

        from server_app.portraits import render_protocol as protocol

        self.assertTrue(
            hasattr(protocol, "verify_media_header"),
            "missing stdlib media header verification",
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "p.png"
            p.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + struct.pack(">I", 13)
                + b"IHDR"
                + struct.pack(">II", 3, 4)
                + b"\x08\x06\0\0\0"
            )
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
            protocol.verify_media_header(p, sha, (3, 4), 100)
            for expected, dims, cap in [
                ("f" * 64, (3, 4), 100),
                (sha, (4, 3), 100),
                (sha, (3, 4), 1),
            ]:
                with self.assertRaises(ValueError):
                    protocol.verify_media_header(p, expected, dims, cap)
            p.write_bytes(b"not png")
            with self.assertRaises(ValueError):
                protocol.verify_media_header(
                    p, hashlib.sha256(p.read_bytes()).hexdigest(), (3, 4), 100
                )


if __name__ == "__main__":
    unittest.main()
