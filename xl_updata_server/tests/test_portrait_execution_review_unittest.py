"""R1/R2 regressions: virtual clock, actual closed Store and manual CLI."""

import dataclasses
import gc
import io
import json
import unittest
import weakref
from contextlib import contextmanager, redirect_stdout
from unittest.mock import patch

import test_portrait_metadata_runtime_unittest as fixture
from server_app.portraits.execution_manual import manual_request
from server_app.portraits.execution_runtime import (
    ExecutionConfig,
    PortraitRuntime,
    failure,
)
from server_app.portraits.metadata_runtime import MetadataConfig
from server_app.portraits.store import PortraitStore


class ReviewTests(unittest.TestCase):
    setUp = fixture.FlowTests.setUp
    promote = fixture.FlowTests.promote
    preparation = fixture.FlowTests.preparation
    drive = fixture.FlowTests.drive

    def test_full_manifest_released_before_package_and_native_seams(self):
        from server_app.portraits.source_io import MetadataRef

        runtime, request, intent, desired = self.prepared(3)
        reader = runtime.metadata.store_reader
        read_manifest = reader.read_manifest
        references = []
        seams = []

        class TrackedDict(dict):
            pass

        def tracked_manifest(pid):
            manifest = TrackedDict(read_manifest(pid))
            references.append(weakref.ref(manifest))
            return manifest

        def released():
            gc.collect()
            self.assertGreaterEqual(len(references), 2)
            self.assertTrue(all(ref() is None for ref in references))

        @contextmanager
        def package(slot, ready, *args, **kwargs):
            released()
            self.assertIs(type(ready), MetadataRef)
            self.assertEqual(dataclasses.asdict(ready), intent["request_ref"])
            with self.assertRaises(dataclasses.FrozenInstanceError):
                ready.size = 0
            seams.append("prepare")
            yield "live-package"

        def render(slot, claim, package, **kwargs):
            released()
            self.assertEqual(package, "live-package")
            self.assertEqual(claim["attempt"], 1)
            self.assertEqual(claim["owner"], runtime.config.owner)
            seams.append("native")
            return failure(claim, "controlled terminal")

        document = {
            "catalog": desired,
            "source_snapshot": {
                "skins": [
                    dict(row, lazy_input={"frozen": True}) for row in desired["skins"]
                ]
            },
        }
        with (
            patch.object(reader, "read_manifest", side_effect=tracked_manifest),
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value=document,
            ),
            patch(
                "server_app.portraits.package_prepare.prepare_lazy_package",
                side_effect=package,
            ),
            patch(
                "server_app.portraits.render_prepare.render_candidate",
                side_effect=render,
            ),
            patch("server_app.portraits.execution_runtime.time.time", return_value=101),
        ):
            result = runtime.execute_tick(
                [request["plan_id"]],
                now=lambda: 101,
                manual_request_id=intent["request_id"],
            )
        self.assertEqual(result["status"], "finished")
        self.assertEqual(seams, ["prepare", "native"])
        counts = reader.read_plan_snapshot(request["plan_id"])["counts"]
        self.assertEqual((counts["failed"], counts["pending"]), (1, 2))

    def test_small_reference_still_authenticates_before_package(self):
        from server_app.portraits.source_io import MetadataRef

        runtime, request, intent, _ = self.prepared(1)
        original_render = runtime._render
        checked = []

        def wrong_ref(slot, claim, ready, cancel):
            self.assertIs(type(ready), MetadataRef)
            checked.append(claim["attempt"])
            return original_render(
                slot, claim, dataclasses.replace(ready, sha256="0" * 64), cancel
            )

        with (
            patch.object(runtime, "_render", side_effect=wrong_ref),
            patch(
                "server_app.portraits.package_prepare.prepare_lazy_package"
            ) as package,
        ):
            result = runtime.execute_tick(
                [request["plan_id"]],
                now=lambda: 101,
                manual_request_id=intent["request_id"],
            )
        self.assertEqual(result["status"], "finished")
        self.assertEqual(checked, [1])
        package.assert_not_called()
        snapshot = runtime.metadata.store_reader.read_plan_snapshot(request["plan_id"])
        self.assertEqual(snapshot["counts"]["failed"], 1)
        self.assertTrue(snapshot["sealed"])

    def prepared(self, count=6):
        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        ref, desired = self.preparation(
            self.promote("base", 123), tuple(range(1, count + 1))
        )
        self.drive(runtime.metadata, ref, desired)
        reader = runtime.metadata.store_reader
        base = reader.read_manifest(reader.list_plans()["plans"][0]["plan_id"])
        intent = {
            "request_id": "explicit-all",
            "version": "123",
            "characters": "all",
            "skins": "all",
            "prepared_ref": dataclasses.asdict(ref),
            "request_ref": base["core_publication_ref"],
        }
        with patch(
            "server_app.portraits.source_prepare.read_prepared_source",
            return_value={"catalog": desired},
        ):
            request = manual_request(runtime, intent, now=100)
        return runtime, request, intent, desired

    def test_plan_window_policy_bounds(self):
        self.assertTrue(
            hasattr(MetadataConfig(), "plan_window_seconds"),
            "missing independent plan window",
        )
        self.assertEqual(MetadataConfig().plan_window_seconds, 604800)
        for value in (True, 299, 31536001, float("inf"), float("nan"), "604800"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MetadataConfig(plan_window_seconds=value)

    def test_manual_queued_members_renew_defer_resume_past_old_deadline(self):
        runtime, request, intent, desired = self.prepared()
        reader = runtime.metadata.store_reader
        pid = request["plan_id"]
        manifest = reader.read_manifest(pid)
        self.assertEqual(manifest["recovery"]["deadline"], 604900)
        store = PortraitStore(self.store, "test", "fixed")
        for index in range(4):
            now = 350 + index * 90
            claim = store.claim("virtual", now, 60, allowed_plan_ids=[pid])
            self.assertFalse(claim["recovery_required"])
            self.assertTrue(
                store.renew(claim["job_id"], claim["attempt"], "virtual", now + 45, 60)
            )
            self.assertTrue(
                store.finish(
                    claim["job_id"],
                    claim["attempt"],
                    failure(claim, "controlled terminal"),
                    owner="virtual",
                    now=now + 90,
                )
            )
        claim = store.claim("virtual", 800, 60, allowed_plan_ids=[pid])
        self.assertFalse(claim["recovery_required"])
        self.assertTrue(
            store.defer(claim["job_id"], claim["attempt"], "virtual", 801, "updater")
        )
        resumed = store.claim("virtual", 1000, 60, allowed_plan_ids=[pid])
        self.assertEqual(resumed["job_id"], claim["job_id"])
        self.assertFalse(resumed["recovery_required"])
        self.assertTrue(
            store.finish(
                resumed["job_id"],
                resumed["attempt"],
                failure(resumed, "controlled terminal"),
                owner="virtual",
                now=1001,
            )
        )
        untouched = store.claim("virtual", 1100, 60, allowed_plan_ids=[pid])
        self.assertEqual(untouched["attempt"], 1)
        self.assertFalse(untouched["recovery_required"])
        runtime.config = dataclasses.replace(
            runtime.config,
            metadata=dataclasses.replace(self.config, plan_window_seconds=1000000),
        )
        with patch(
            "server_app.portraits.source_prepare.read_prepared_source",
            return_value={"catalog": desired},
        ):
            manual_request(runtime, intent, now=2000)
        self.assertEqual(reader.read_manifest(pid), manifest)

    def test_automatic_freezes_new_window_and_does_not_extend_on_restart(self):
        runtime = PortraitRuntime(ExecutionConfig(metadata=self.config))
        self.drive(runtime.metadata, *self.preparation(self.promote("base", 123)))
        prepared, desired = self.preparation(
            self.promote("new", 124), (1, 2, 3, 4, 5, 6)
        )
        self.drive(runtime.metadata, prepared, desired)
        pid = runtime.metadata.store_reader.list_plans()["plans"][-1]["plan_id"]
        manifest = runtime.metadata.store_reader.read_manifest(pid)
        self.assertEqual(manifest["recovery"]["deadline"], 604900)
        restarted = PortraitRuntime(
            ExecutionConfig(
                metadata=dataclasses.replace(self.config, plan_window_seconds=1000000)
            )
        )
        self.drive(restarted.metadata, prepared, desired)
        self.assertEqual(restarted.metadata.store_reader.read_manifest(pid), manifest)

    def test_cli_drains_selection_and_paces(self):
        from server_app.portraits.cli import main

        runtime, _request, intent, desired = self.prepared()
        waits = []
        with (
            patch("server_app.portraits.cli.load_config", return_value=runtime.config),
            patch("server_app.portraits.cli.read_json", return_value=intent),
            patch(
                "server_app.portraits.execution_runtime.PortraitRuntime",
                return_value=runtime,
            ),
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value={"catalog": desired},
            ),
            patch.object(
                runtime,
                "_render",
                side_effect=lambda slot, claim, manifest, cancel: failure(
                    claim, "fixture"
                ),
            ),
            patch.object(
                runtime.stop_event,
                "wait",
                side_effect=lambda seconds: waits.append(seconds) or False,
            ),
            patch("server_app.portraits.execution_runtime.time.time", return_value=101),
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(
                    main(
                        [
                            "manual",
                            "--enable",
                            "--config",
                            "fixture",
                            "--request",
                            "intent",
                        ]
                    ),
                    0,
                )
        response = json.loads(out.getvalue())["execution"]
        self.assertEqual(response["remaining"], 0)
        self.assertTrue(response["terminal"])
        self.assertEqual(response["attempted"], 6)
        self.assertEqual(waits, [3])

    def test_manual_bound_defer_stop_and_resume(self):
        from server_app.portraits.executor import Deferred

        runtime, request, intent, _desired = self.prepared(3)
        self.assertTrue(
            hasattr(runtime, "drain_manual"), "missing bounded manual drain"
        )
        pid = request["plan_id"]
        with patch.object(
            runtime,
            "_render",
            side_effect=lambda slot, claim, manifest, cancel: failure(claim, "fixture"),
        ):
            limited = runtime.drain_manual(
                pid, intent["request_id"], max_jobs=1, now=lambda: 101
            )
        self.assertEqual(
            (limited["remaining"], limited["status"], limited["terminal"]),
            (2, "bound", False),
        )
        with patch.object(runtime, "_render", side_effect=Deferred("updater")):
            deferred = runtime.drain_manual(pid, intent["request_id"], now=lambda: 102)
        self.assertEqual((deferred["remaining"], deferred["status"]), (2, "deferred"))
        runtime.stop_event.set()
        stopped = runtime.drain_manual(pid, intent["request_id"], now=lambda: 103)
        self.assertEqual(
            (stopped["remaining"], stopped["status"], stopped["attempted"]),
            (2, "stop", 0),
        )
        runtime.stop_event.clear()
        with patch.object(
            runtime,
            "_render",
            side_effect=lambda slot, claim, manifest, cancel: failure(claim, "fixture"),
        ):
            final = runtime.drain_manual(pid, intent["request_id"], now=lambda: 104)
        self.assertEqual((final["remaining"], final["terminal"]), (0, True))
        with self.assertRaises(ValueError):
            runtime.drain_manual(pid, "wrong", now=lambda: 105)

    def test_old_short_plan_is_never_extended(self):
        self.config = dataclasses.replace(self.config, plan_window_seconds=300)
        runtime, request, intent, desired = self.prepared(2)
        reader = runtime.metadata.store_reader
        original = reader.read_manifest(request["plan_id"])
        self.assertEqual(original["recovery"]["deadline"], 400)
        runtime.config = dataclasses.replace(
            runtime.config,
            metadata=dataclasses.replace(self.config, plan_window_seconds=604800),
        )
        with patch(
            "server_app.portraits.source_prepare.read_prepared_source",
            return_value={"catalog": desired},
        ):
            manual_request(runtime, intent, now=1000)
        self.assertEqual(reader.read_manifest(request["plan_id"]), original)
        with patch.object(runtime, "_render") as render:
            result = runtime.drain_manual(
                request["plan_id"], intent["request_id"], now=lambda: 1001
            )
        render.assert_not_called()
        self.assertEqual(
            (result["attempted"], result["remaining"], result["counts"]["failed"]),
            (2, 0, 2),
        )
        self.assertTrue(result["terminal"])

    def test_bound_validation_no_progress_and_pacing_stop(self):
        runtime, request, intent, _ = self.prepared(6)
        for value in (True, 0, -1, 10001, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.drain_manual(
                    request["plan_id"], intent["request_id"], max_jobs=value
                )
        with patch.object(
            runtime, "execute_tick", return_value={"status": "finished"}
        ) as tick:
            stalled = runtime.drain_manual(request["plan_id"], intent["request_id"])
        self.assertEqual(stalled["status"], "no_progress")
        self.assertEqual(stalled["remaining"], 6)
        tick.assert_called_once()

        def stop_at_pause(seconds):
            self.assertEqual(seconds, 3)
            runtime.stop_event.set()
            return True

        with (
            patch.object(
                runtime,
                "_render",
                side_effect=lambda slot, claim, manifest, cancel: failure(
                    claim, "fixture"
                ),
            ),
            patch.object(runtime.stop_event, "wait", side_effect=stop_at_pause),
        ):
            stopped = runtime.drain_manual(
                request["plan_id"], intent["request_id"], now=lambda: 101
            )
        self.assertEqual(
            (
                stopped["attempted"],
                stopped["remaining"],
                stopped["status"],
                stopped["terminal"],
            ),
            (5, 1, "stop", False),
        )

    def test_cli_explicit_bound_and_nonmanual_rejection(self):
        from server_app.portraits.cli import main

        runtime, _request, intent, desired = self.prepared(3)
        with (
            patch("server_app.portraits.cli.load_config", return_value=runtime.config),
            patch("server_app.portraits.cli.read_json", return_value=intent),
            patch(
                "server_app.portraits.execution_runtime.PortraitRuntime",
                return_value=runtime,
            ),
            patch(
                "server_app.portraits.source_prepare.read_prepared_source",
                return_value={"catalog": desired},
            ),
            patch.object(
                runtime,
                "_render",
                side_effect=lambda slot, claim, manifest, cancel: failure(
                    claim, "fixture"
                ),
            ),
            patch("server_app.portraits.execution_runtime.time.time", return_value=101),
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(
                    main(
                        [
                            "manual",
                            "--enable",
                            "--config",
                            "fixture",
                            "--request",
                            "intent",
                            "--max-jobs",
                            "1",
                        ]
                    ),
                    0,
                )
        result = json.loads(out.getvalue())["execution"]
        self.assertEqual(
            (result["attempted"], result["remaining"], result["status"]),
            (1, 2, "bound"),
        )
        first = runtime.metadata.store_reader.list_plans()["plans"][0]["plan_id"]
        with self.assertRaisesRegex(ValueError, "manual plan required"):
            runtime.drain_manual(first, intent["request_id"])
        with patch("server_app.portraits.cli.load_config") as load:
            self.assertEqual(main(["tick", "--enable", "--max-jobs", "1"]), 2)
        load.assert_not_called()
