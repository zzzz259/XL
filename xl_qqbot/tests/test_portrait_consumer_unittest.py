import importlib.util
import tempfile
import unittest
from pathlib import Path


class DeliveryStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_prefix_restart_and_scope(self):
        self.assertIsNotNone(
            importlib.util.find_spec("bot_app.portrait_delivery_state"),
            "durable consumer state missing",
        )
        from bot_app.portrait_delivery_state import DeliveryState

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "delivery.sqlite"
            state = DeliveryState(path, {"instance": "test", "source": "main"})
            groups = [str(i) for i in range(205)]
            state.freeze_generation("run", "1", groups, True)
            state.freeze_intent("run", "cards", [{"kind": "text", "text": "card"}])
            calls = []

            class Sink:
                async def enqueue(self, **envelope):
                    calls.append(envelope)
                    if len(calls) == 2:
                        raise OSError("accepted prefix then disconnected")

            with self.assertRaises(OSError):
                await state.submit("run", "cards", Sink())
            self.assertFalse(state.accepted("run", "cards"))
            state.close()
            state = DeliveryState(path, {"instance": "test", "source": "main"})
            state.freeze_generation("run", "1", ["late-group"], True)
            await state.submit("run", "cards", Sink())
            self.assertEqual(calls[1], calls[2])
            self.assertEqual([100, 100, 100, 5], [len(c["messages"]) for c in calls])
            self.assertTrue(state.accepted("run", "cards"))
            self.assertEqual(groups, state.generation("run")["recipients"])
            state.close()
            with self.assertRaises(ValueError):
                DeliveryState(path, {"instance": "other", "source": "main"})


class ConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_failure_recovers_mute_without_finish_or_notifier_ack(self):
        self.assertIsNotNone(
            importlib.util.find_spec("bot_app.portrait_consumer"),
            "consumer integration missing",
        )
        from bot_app.portrait_consumer import PortraitConsumer
        from bot_app.portrait_delivery_state import DeliveryState
        from bot_app.updater import ServiceMute, UpdateEvent
        from portrait_consumer_fixtures import Evidence
        from server_app.portraits.core_outcomes import CoreReader
        from server_app.portraits.reader import PortraitReader
        from server_app.portraits.runtime_ledger_reader import RuntimeLedgerReader
        from server_app.portraits.store import PortraitStore

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = Evidence(root)
            PortraitStore(root / "store.db", "i", "s")
            state = DeliveryState(root / "bot.db", {"instance": "i", "source": "s"})
            calls = []

            class Sink:
                async def enqueue(self, **envelope):
                    calls.append(envelope)
                    raise OSError("notifier unavailable")

            mute = ServiceMute()
            consumer = PortraitConsumer(
                state,
                CoreReader(evidence.core_path, scope=evidence.scope),
                RuntimeLedgerReader(evidence.ledger_path, "i", "s"),
                PortraitReader(root / "store.db", "i", "s"),
                Sink(),
                root,
                root,
                lambda: ["group"],
                mute,
            )
            consumer.activate(root / "events")
            consumer.observe_lifecycle(
                UpdateEvent("start", 5, line_number=1, run_id="a")
            )
            self.assertTrue(mute.muted)
            evidence.outcome(phase="failed")
            with self.assertRaises(OSError):
                await consumer.poll_once()
            self.assertFalse(mute.muted)
            self.assertEqual(1, len(calls))
            self.assertIn("失败", calls[0]["messages"][0]["text"])
            self.assertFalse(state.generation("a")["done"])
            state.close()

    async def test_registered_success_then_one_sealed_summary_real_readers(self):
        import hashlib

        from bot_app.portrait_consumer import PortraitConsumer
        from bot_app.portrait_delivery_state import DeliveryState
        from bot_app.updater import ServiceMute
        from portrait_consumer_fixtures import Evidence, make_plan, success
        from server_app.portraits.core_outcomes import CoreReader
        from server_app.portraits.reader import PortraitReader
        from server_app.portraits.runtime_ledger_reader import RuntimeLedgerReader
        from server_app.portraits.store import PortraitStore

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = Evidence(root)
            store = PortraitStore(root / "store.db", "i", "s")
            reader = PortraitReader(root / "store.db", "i", "s")
            state = DeliveryState(root / "bot.db", {"instance": "i", "source": "s"})
            calls = []

            class Sink:
                async def enqueue(self, **envelope):
                    calls.append(envelope)

            consumer = PortraitConsumer(
                state,
                CoreReader(evidence.core_path, scope=evidence.scope),
                RuntimeLedgerReader(evidence.ledger_path, "i", "s"),
                reader,
                Sink(),
                root,
                root,
                lambda: ["group"],
                ServiceMute(),
            )
            consumer.activate(root / "events")
            outcome = evidence.outcome()
            plan = make_plan(consumer.identity, outcome)
            pid = store.register_closed_plan(plan)["plan_id"]
            c = store.claim("worker", 0, 100, allowed_plan_ids=[pid])
            data = b"fixture accepted media bytes"
            sha = hashlib.sha256(data).hexdigest()
            (root / "artifacts").mkdir()
            (root / "artifacts" / f"{sha}.png").write_bytes(data)
            store.finish(
                c["job_id"], c["attempt"], success(c, sha), owner="worker", now=1
            )
            store.seal_plan(pid)
            evidence.mapping(
                {
                    "core_identity": consumer.identity,
                    "run_id": "a",
                    "disposition": "registered_origin",
                    "plan_id": pid,
                    "delivery_owner": {
                        "core_identity": consumer.identity,
                        "run_id": "a",
                    },
                    "origin_core": plan["origin_core"],
                    "registration": {
                        "database_id": reader.database_id,
                        "incarnation": reader.incarnation,
                        "path": str(reader.path),
                        "plan_id": pid,
                        "registration": 1,
                    },
                },
                {"manifest": plan},
            )
            await consumer.poll_once()
            await consumer.poll_once()
            self.assertEqual(
                ["text", "image", "text"], [c["messages"][0]["kind"] for c in calls]
            )
            self.assertEqual(
                "local_multipart", calls[1]["messages"][0]["image_delivery_mode"]
            )
            self.assertIn("立绘成功1", calls[2]["messages"][0]["text"])
            self.assertTrue(state.generation("a")["done"])
            state.close()


class CutoverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from bot_app.portrait_consumer import PortraitConsumer
        from bot_app.portrait_delivery_state import DeliveryState
        from bot_app.updater import ServiceMute
        from portrait_consumer_fixtures import Evidence
        from server_app.portraits.core_outcomes import CoreReader
        from server_app.portraits.reader import PortraitReader
        from server_app.portraits.runtime_ledger_reader import RuntimeLedgerReader
        from server_app.portraits.store import PortraitStore

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = Evidence(self.root)
        self.store = PortraitStore(self.root / "store.db", "i", "s")
        self.state = DeliveryState(
            self.root / "bot.db", {"instance": "i", "source": "s"}
        )
        self.addCleanup(self.state.close)
        self.calls = []
        calls = self.calls

        class Sink:
            async def enqueue(self, **envelope):
                calls.append(envelope)

        self.mute = ServiceMute()
        self.consumer = PortraitConsumer(
            self.state,
            CoreReader(self.evidence.core_path, scope=self.evidence.scope),
            RuntimeLedgerReader(self.evidence.ledger_path, "i", "s"),
            PortraitReader(self.root / "store.db", "i", "s"),
            Sink(),
            self.root,
            self.root,
            lambda: ["group"],
            self.mute,
        )

    async def test_baseline_failure_is_suppressed_but_recovers_mute(self):
        from bot_app.updater import UpdateEvent

        self.consumer.activate(self.root / "events")
        self.consumer.observe_lifecycle(
            UpdateEvent("start", 5, line_number=1, run_id="a")
        )
        self.evidence.outcome(phase="failed", eligible=False)
        await self.consumer.poll_once()
        self.assertEqual([], self.calls)
        self.assertFalse(self.mute.muted)

    async def test_historical_core_cannot_unmute_new_active_run(self):
        from bot_app.updater import UpdateEvent

        self.evidence.outcome(run="old")
        self.consumer.activate(self.root / "events")
        self.consumer.observe_lifecycle(
            UpdateEvent("start", 5, line_number=1, run_id="new")
        )
        await self.consumer.poll_once()
        self.assertTrue(self.mute.muted)
        self.assertEqual([], self.calls)

    async def test_source_pending_no_fake_zero_and_alias_ack_no_render_counts(self):
        self.consumer.activate(self.root / "events")
        self.evidence.outcome()
        await self.consumer.poll_once()
        self.assertEqual(1, len(self.calls))
        self.assertFalse(self.state.generation("a")["done"])
        value = {
            "core_identity": self.consumer.identity,
            "run_id": "a",
            "disposition": "alias_pending",
        }
        self.evidence.mapping(value)
        await self.consumer.poll_once()
        self.assertEqual(1, len(self.calls))
        self.evidence.mapping(dict(value, disposition="existing_plan_alias"))
        await self.consumer.poll_once()
        await self.consumer.poll_once()
        self.assertEqual(2, len(self.calls))
        self.assertNotIn("立绘成功", self.calls[-1]["messages"][0]["text"])

    async def test_card_private_snapshot_prefix_restart_source_mutation(self):
        from portrait_consumer_fixtures import cards

        evidence = cards(self.root, self.evidence.scope)
        self.consumer.activate(self.root / "events")
        self.consumer.groups = lambda: [str(i) for i in range(205)]
        self.evidence.outcome(cards=evidence)
        accepted = []
        attempts = []

        class PrefixSink:
            async def enqueue(self, **envelope):
                if envelope["messages"][0]["kind"] == "image":
                    attempts.append(envelope)
                    if len(attempts) == 2:
                        raise OSError("prefix durable, lost ACK")
                    accepted.extend(
                        Path(m["media_path"]).read_bytes() for m in envelope["messages"]
                    )

        self.consumer.sink = PrefixSink()
        with self.assertRaises(OSError):
            await self.consumer.poll_once()
        self.assertIsNone(self.state.generation("a")["barrier"])
        self.assertTrue((self.root / "outbox" / "5").exists())
        (self.root / "outbox" / "5" / "1.png").write_bytes(b"changed live output")
        await self.consumer.poll_once()
        await self.consumer.poll_once()
        self.assertEqual(attempts[1], attempts[2])
        self.assertEqual(205, len(accepted))
        self.assertEqual({b"card immutable fixture"}, set(accepted))
        self.assertIsNotNone(self.state.generation("a")["barrier"])
        # The changed live folder could belong to a newer same-version run.
        self.assertTrue((self.root / "outbox" / "5" / "1.png").exists())

    async def test_zero_plan_does_not_wait_for_nonexistent_images(self):
        from portrait_consumer_fixtures import make_plan

        self.consumer.activate(self.root / "events")
        outcome = self.evidence.outcome()
        plan = make_plan(self.consumer.identity, outcome, empty=True)
        pid = self.store.register_closed_plan(plan)["plan_id"]
        self.store.seal_plan(pid)
        reader = self.consumer.portraits
        self.evidence.mapping(
            {
                "core_identity": self.consumer.identity,
                "run_id": "a",
                "disposition": "registered_origin",
                "plan_id": pid,
                "delivery_owner": {
                    "core_identity": self.consumer.identity,
                    "run_id": "a",
                },
                "origin_core": plan["origin_core"],
                "registration": {
                    "database_id": reader.database_id,
                    "incarnation": reader.incarnation,
                    "path": str(reader.path),
                    "plan_id": pid,
                    "registration": 1,
                },
            },
            {"manifest": plan},
        )
        await self.consumer.poll_once()
        self.assertIn("立绘成功0", self.calls[-1]["messages"][0]["text"])
        self.assertTrue(self.state.generation("a")["done"])

    async def test_activation_replacement_refuses_without_creating_missing_db(self):
        from server_app.portraits.reader import PortraitReader
        from server_app.portraits.store import PortraitStore

        self.consumer.activate(self.root / "events")
        absent = self.root / "missing.db"
        with self.assertRaises(ValueError):
            PortraitReader(absent, "i", "s")
        self.assertFalse(absent.exists())
        replacement = self.root / "replacement.db"
        PortraitStore(replacement, "i", "s")
        replacement.replace(self.root / "store.db")
        self.consumer.portraits = PortraitReader(self.root / "store.db", "i", "s")
        with self.assertRaises(ValueError):
            self.consumer.activate(self.root / "events")
        self.assertEqual([], self.calls)

    async def test_missing_cards_quarantine_after_frozen_budget_not_no_card(self):
        from portrait_consumer_fixtures import cards

        evidence = cards(self.root, self.evidence.scope)
        (self.root / "outbox" / "5" / "1.png").unlink()
        self.consumer.activate(self.root / "events")
        self.evidence.outcome(cards=evidence)
        now = [0]
        self.consumer.clock = lambda: now[0]
        for value in (0, 1, 6):
            now[0] = value
            await self.consumer.poll_once()
            await self.consumer.poll_once()
        gen = self.state.generation("a")
        self.assertIsNone(gen["barrier"])
        self.assertIn("cards", gen["problems"])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(3, self.state.get("retry:a:cards")["attempts"])

    async def test_empty_cohort_disposes_missing_media_without_waiting_for_mapping(
        self,
    ):
        self.consumer.groups = list
        self.consumer.activate(self.root / "events")
        self.evidence.outcome()
        await self.consumer.poll_once()
        self.assertTrue(self.state.generation("a")["done"])
        self.assertEqual("empty_recipients", self.state.generation("a")["disposition"])
        self.assertEqual([], self.calls)

    async def test_postcutover_duplicate_start_cannot_reannounce_old_terminal(self):
        from bot_app.updater import UpdateEvent

        self.evidence.outcome()
        self.consumer.activate(self.root / "events")
        self.consumer.observe_lifecycle(
            UpdateEvent("start", 5, line_number=1, run_id="a")
        )
        await self.consumer.poll_once()
        self.assertEqual([], self.calls)
        self.assertTrue(self.state.generation("a")["done"])

    async def test_shutdown_cancels_pending_sink_without_fabricated_ack(self):
        import asyncio

        self.consumer.activate(self.root / "events")
        self.evidence.outcome(phase="failed")
        entered = asyncio.Event()

        class StalledSink:
            async def enqueue(self, **envelope):
                entered.set()
                await asyncio.Future()

        self.consumer.sink = StalledSink()
        stop = asyncio.Event()
        task = asyncio.create_task(self.consumer.run(stop))
        await asyncio.wait_for(entered.wait(), 1)
        stop.set()
        await asyncio.wait_for(task, 0.3)
        self.assertFalse(self.state.accepted("a", "final"))

    def register_success(self, *, manual=False, corrupt=False, cards_evidence=None):
        import hashlib

        from portrait_consumer_fixtures import make_plan, success

        outcome = (
            self.evidence.outcome(cards=cards_evidence)
            if not manual
            else {"run_id": "manual"}
        )
        plan = make_plan(self.consumer.identity, outcome)
        if manual:
            plan.update(
                kind="manual",
                request_id="request",
                delivery_disposition="manual_query_only",
            )
        pid = self.store.register_closed_plan(plan)["plan_id"]
        claimed = self.store.claim("worker", 0, 100, allowed_plan_ids=[pid])
        data = b"accepted"
        sha = hashlib.sha256(data).hexdigest()
        (self.root / "artifacts").mkdir()
        (self.root / "artifacts" / f"{sha}.png").write_bytes(
            b"corrupt" if corrupt else data
        )
        self.store.finish(
            claimed["job_id"],
            claimed["attempt"],
            success(claimed, sha),
            owner="worker",
            now=1,
        )
        self.store.seal_plan(pid)
        if not manual:
            reader = self.consumer.portraits
            self.evidence.mapping(
                {
                    "core_identity": self.consumer.identity,
                    "run_id": "a",
                    "disposition": "registered_origin",
                    "plan_id": pid,
                    "delivery_owner": {
                        "core_identity": self.consumer.identity,
                        "run_id": "a",
                    },
                    "origin_core": plan["origin_core"],
                    "registration": {
                        "database_id": reader.database_id,
                        "incarnation": reader.incarnation,
                        "path": str(reader.path),
                        "plan_id": pid,
                        "registration": 1,
                    },
                },
                {"manifest": plan},
            )
        return pid

    async def test_manual_query_only_disposition_advances_without_broadcast(self):
        self.consumer.activate(self.root / "events")
        self.register_success(manual=True)
        await self.consumer.poll_once()
        self.assertEqual([], self.calls)
        self.assertEqual("manual_query_only", self.state.get("event:1")["disposition"])
        self.assertEqual(1, self.state.get("cursor:events")["position"])

    async def test_corrupt_delivery_counts_problem_separately_from_renderer_success(
        self,
    ):
        self.consumer.activate(self.root / "events")
        self.register_success(corrupt=True)
        await self.consumer.poll_once()
        self.assertEqual(
            ["text", "text"], [c["messages"][0]["kind"] for c in self.calls]
        )
        final = self.calls[-1]["messages"][0]["text"]
        self.assertIn("立绘成功1", final)
        self.assertIn("发布问题1项", final)
        self.assertEqual(1, self.state.get("cursor:events")["position"])

    async def test_full_start_cards_portrait_final_order_and_cleanup(self):
        from portrait_consumer_fixtures import cards

        card_evidence = cards(self.root, self.evidence.scope)
        self.consumer.activate(self.root / "events")
        self.register_success(cards_evidence=card_evidence)
        await self.consumer.poll_once()
        await self.consumer.poll_once()
        self.assertEqual(
            ["text", "image", "image", "text"],
            [c["messages"][0]["kind"] for c in self.calls],
        )
        card = self.calls[1]["messages"][0]
        portrait = self.calls[2]["messages"][0]
        self.assertEqual(
            b"card immutable fixture", Path(card["media_path"]).read_bytes()
        )
        self.assertNotIn("image_delivery_mode", card)
        self.assertEqual("local_multipart", portrait["image_delivery_mode"])
        self.assertTrue((self.root / "outbox" / "5").exists())
        self.assertEqual("cleanup_deferred", self.state.get("cleanup:a")["disposition"])
        self.assertIsNotNone(self.state.generation("a")["barrier"])

    async def test_automation_enabled_mapping_and_default_off(self):
        from dataclasses import replace

        from bot_app.automation import build_automation_runtime
        from bot_app.config import (
            BilibiliConfig,
            BotConfig,
            Config,
            InstanceConfig,
            MessageConfig,
            NotificationConfig,
            TargetConfig,
            UploadConfig,
            WatchConfig,
        )

        config = Config(
            bot=BotConfig("x", "y"),
            watch=WatchConfig(
                str(self.root / "outbox"), 1, str(self.root / "automation")
            ),
            target=TargetConfig(["group"], False),
            upload=UploadConfig(""),
            message=MessageConfig(""),
            bilibili=BilibiliConfig(enabled=False),
            instance=InstanceConfig("i"),
            notification=NotificationConfig(
                portrait_roots={"i": {"s": str(self.root)}}
            ),
        )
        off = build_automation_runtime(config, notification_client=self.consumer.sink)
        self.assertEqual((), off.portrait_consumers)
        self.assertFalse(
            (self.root / "automation" / "portrait_delivery.sqlite").exists()
        )
        settings = {
            "enabled": True,
            "source_id": "s",
            "core_scope": self.evidence.scope,
            "core_journal": str(self.evidence.core_path),
            "runtime_ledger": str(self.evidence.ledger_path),
            "portrait_store": str(self.root / "store.db"),
            "artifact_root": str(self.root),
            "updater_root": str(self.root),
            "image_delivery_mode": "local_multipart",
        }
        enabled = build_automation_runtime(
            replace(config, portrait_delivery={"main": settings}),
            notification_client=self.consumer.sink,
        )
        self.assertEqual(1, len(enabled.portrait_consumers))
        self.assertIs(
            enabled.watchers[0].portrait_consumer, enabled.portrait_consumers[0]
        )
        enabled.portrait_consumers[0].state.close()

    async def test_source_link_before_parent_page_is_retained_and_truthful(self):
        from portrait_consumer_fixtures import failure_link

        self.consumer.activate(self.root / "events")
        for index in range(17):
            self.evidence.outcome(
                run=f"r{index:02}", cards={"kind": "handoff_unresolved"}
            )
        failure_link(self.evidence, "r16", 17)
        for _ in range(4):
            await self.consumer.poll_once()
        self.assertTrue(self.state.generation("r16")["done"])
        text = [
            c["messages"][0]["text"]
            for c in self.calls
            if "游戏数据更新完成" in c["messages"][0]["text"]
        ]
        self.assertEqual(1, len(text))
        self.assertIn("立绘未发布", text[0])
        self.assertNotIn("立绘成功0", text[0])
        self.assertIsNone(self.state.generation("r16")["barrier"])

    async def test_uncorrelated_cutover_text_only_never_unmutes_correlated_run(self):
        import json
        from types import SimpleNamespace

        from bot_app.watcher import Watcher

        path = self.root / "update_events.jsonl"
        path.write_text(
            json.dumps({"event": "start", "version": 1}) + "\n", encoding="utf-8"
        )
        self.consumer.activate(path)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps({"event": "start", "version": 5, "run_id": "active"}) + "\n"
            )
            stream.write(
                json.dumps({"event": "finish", "version": 5, "new_characters": 2})
                + "\n"
            )
        config = SimpleNamespace(
            watch=SimpleNamespace(
                outbox_dir=str(self.root / "outbox"),
                data_dir=str(self.root / "watcher"),
            ),
            instance=SimpleNamespace(instance_id="i"),
            target=SimpleNamespace(group_openids=["group"]),
        )
        watcher = Watcher(
            config,
            notification_client=self.consumer.sink,
            portrait_consumer=self.consumer,
            mute=self.mute,
        )
        await watcher._tick()
        await watcher._tick()
        self.assertEqual(1, len(self.calls))
        self.assertTrue(self.mute.muted)
        self.assertEqual(3, watcher.notice.consumed_events)
        self.assertTrue(
            any(
                q.get("reason") == "missing lifecycle correlation"
                for q in self.state.health()["quarantines"]
            )
        )

    async def test_same_version_alias_does_not_claim_late_original_portrait(self):
        self.consumer.activate(self.root / "events")
        self.register_success()
        self.evidence.outcome(run="b")
        self.evidence.mapping(
            {
                "core_identity": self.consumer.identity,
                "run_id": "b",
                "disposition": "existing_plan_alias",
            }
        )
        for _ in range(3):
            await self.consumer.poll_once()
        self.assertEqual(
            self.consumer.identity, self.state.generation("a")["core_identity"]
        )
        self.assertTrue(self.state.generation("a")["done"])
        self.assertTrue(self.state.generation("b")["done"])
        images = [c for c in self.calls if c["messages"][0]["kind"] == "image"]
        summaries = [
            c for c in self.calls if "立绘成功" in c["messages"][0].get("text", "")
        ]
        self.assertEqual(1, len(images))
        self.assertEqual(1, len(summaries))
        self.assertTrue(self.state.accepted("a", "portrait:1"))
        self.assertFalse(self.state.accepted("b", "portrait:1"))

    async def test_alias_with_card_quarantine_reports_delivery_failure(self):
        from portrait_consumer_fixtures import cards

        evidence = cards(self.root, self.evidence.scope)
        (self.root / "outbox" / "5" / "1.png").write_bytes(b"corrupt")
        self.consumer.activate(self.root / "events")
        self.evidence.outcome(cards=evidence)
        self.evidence.mapping(
            {
                "core_identity": self.consumer.identity,
                "run_id": "a",
                "disposition": "existing_plan_alias",
            }
        )
        await self.consumer.poll_once()
        final = self.calls[-1]["messages"][0]["text"]
        self.assertIn("图鉴交接/发布不可用", final)
        self.assertNotIn("立绘成功", final)

    async def test_disabled_legacy_more_than_128_lines_preserves_start_cards_finish(
        self,
    ):
        import json
        from types import SimpleNamespace

        from bot_app.watcher import Watcher

        folder = self.root / "outbox" / "5"
        folder.mkdir(parents=True)
        (folder / "1.png").write_bytes(b"card")
        (folder / "manifest.json").write_text(
            json.dumps(
                {
                    "version": "5",
                    "characters": [{"id": "1", "name": "A", "file_name": "1.png"}],
                }
            ),
            encoding="utf-8",
        )
        path = self.root / "update_events.jsonl"
        path.write_text(
            "{}\n" * 128
            + json.dumps({"event": "start", "version": 5})
            + "\n"
            + json.dumps({"event": "finish", "version": 5})
            + "\n",
            encoding="utf-8",
        )
        config = SimpleNamespace(
            watch=SimpleNamespace(
                outbox_dir=str(self.root / "outbox"), data_dir=str(self.root / "legacy")
            ),
            instance=SimpleNamespace(instance_id="i"),
            target=SimpleNamespace(group_openids=["group"]),
            message=SimpleNamespace(template=""),
        )
        watcher = Watcher(config, notification_client=self.consumer.sink)
        await watcher._tick()
        self.assertEqual(
            ["text", "image", "text"], [c["messages"][0]["kind"] for c in self.calls]
        )
        self.assertEqual(130, watcher.notice.consumed_events)
        await watcher._tick()
        self.assertEqual(3, len(self.calls))

    async def test_second_terminal_observed_during_blocked_cards_stop_and_restart(self):
        import asyncio
        import json
        from types import SimpleNamespace

        from bot_app.portrait_delivery_state import DeliveryState
        from bot_app.watcher import Watcher
        from portrait_consumer_fixtures import cards

        self.consumer.activate(self.root / "events")
        self.register_success(cards_evidence=cards(self.root, self.evidence.scope))
        entered = asyncio.Event()
        release = asyncio.Event()
        attempts = []
        accepted = []
        active = [0, 0]

        class Sink:
            async def enqueue(inner, **envelope):
                active[0] += 1
                active[1] = max(active)
                try:
                    if (
                        envelope["messages"][0]["kind"] == "image"
                        and "media_path" in envelope["messages"][0]
                    ):
                        attempts.append(envelope)
                        entered.set()
                        await release.wait()
                    accepted.append(envelope)
                finally:
                    active[0] -= 1

        self.consumer.sink = Sink()
        stop = asyncio.Event()
        task = asyncio.create_task(self.consumer.run(stop, interval=0.01))

        async def finish_task():
            stop.set()
            await asyncio.wait_for(task, 2)

        try:
            await asyncio.wait_for(entered.wait(), 2)
            config = SimpleNamespace(
                watch=SimpleNamespace(
                    outbox_dir=str(self.root / "outbox"),
                    data_dir=str(self.root / "watcher"),
                ),
                instance=SimpleNamespace(instance_id="i"),
                target=SimpleNamespace(group_openids=["group"]),
            )
            (self.root / "update_events.jsonl").write_text(
                json.dumps({"event": "start", "version": 5, "run_id": "b"}) + "\n",
                encoding="utf-8",
            )
            watcher = Watcher(
                config,
                notification_client=self.consumer.sink,
                portrait_consumer=self.consumer,
                mute=self.mute,
            )
            await watcher._tick()
            self.assertTrue(self.mute.muted)
            self.evidence.outcome(run="b")
            self.evidence.mapping(
                {
                    "core_identity": self.consumer.identity,
                    "run_id": "b",
                    "disposition": "existing_plan_alias",
                }
            )

            async def terminal():
                while not self.state.generation("b")["terminal"]:
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(terminal(), 1)
            self.assertFalse(self.mute.muted)
            self.assertTrue(self.state.generation("b")["deferred"])
            self.assertFalse(release.is_set())
            self.assertFalse(self.state.accepted("a", "cards"))
        finally:
            await finish_task()
        self.assertFalse(self.state.accepted("a", "cards"))
        old = self.consumer
        path = self.state.path
        self.state.close()
        self.state = DeliveryState(path, old.state.scope)
        self.addCleanup(self.state.close)
        self.consumer = type(old)(
            self.state,
            old.core,
            old.ledger,
            old.portraits,
            old.sink,
            self.root,
            self.root,
            old.groups,
            self.mute,
        )
        self.consumer.activate(self.root / "events")
        release.set()
        stop = asyncio.Event()
        task = asyncio.create_task(self.consumer.run(stop, interval=0.01))
        try:

            async def complete():
                while not all(self.state.generation(r)["done"] for r in ("a", "b")):
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(complete(), 3)
        finally:
            await finish_task()
        self.assertEqual(1, active[1])
        self.assertEqual(attempts[0], attempts[1])
        messages = [c["messages"][0] for c in accepted]
        self.assertEqual(6, len(messages))
        images = [m for m in messages if m["kind"] == "image"]
        self.assertEqual(2, len(images))
        self.assertIn("media_path", images[0])
        self.assertEqual("local_multipart", images[1]["image_delivery_mode"])
        summary = next(m for m in messages if "立绘成功" in m.get("text", ""))
        self.assertGreater(messages.index(summary), messages.index(images[1]))
        self.assertEqual(2, sum("更新完成" in m.get("text", "") for m in messages))
        self.assertTrue(all(self.state.generation(r)["barrier"] for r in ("a", "b")))
        self.assertTrue(all(self.state.accepted(r, "final") for r in ("a", "b")))

    async def test_observer_prioritizes_active_run_ahead_of_history_page(self):
        from bot_app.updater import UpdateEvent

        for index in range(17):
            self.evidence.outcome(run=f"history{index:02}")
        self.consumer.activate(self.root / "events")
        self.consumer.observe_lifecycle(
            UpdateEvent("start", 5, line_number=1, run_id="current")
        )
        self.evidence.outcome(run="current")
        self.consumer.observe_once()
        self.assertTrue(self.state.generation("current")["deferred"])
        self.assertFalse(self.mute.muted)
        self.assertEqual(16, self.state.get("cursor:outcomes")["sequence"])
        self.assertEqual([], self.calls)
