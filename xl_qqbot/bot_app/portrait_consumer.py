"""Bounded read-only Core/Runtime/Store consumer; notifier ACK is queue acceptance."""

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path

from .portrait_delivery_state import digest

LOG = logging.getLogger(__name__)


class PortraitConsumer:
    def __init__(
        self,
        state,
        core,
        ledger,
        portraits,
        sink,
        artifact_root,
        updater_root,
        groups,
        mute,
        *,
        label="",
        clock=time.time,
        card_template="",
    ):
        self.state, self.core, self.ledger, self.portraits, self.sink = (
            state,
            core,
            ledger,
            portraits,
            sink,
        )
        self.artifact_root = Path(artifact_root).resolve()
        self.updater_root = Path(updater_root).resolve()
        self.groups, self.mute, self.label, self.clock = groups, mute, label, clock
        self.identity = None
        self.card_template = card_template
        self._delivery_lock = asyncio.Lock()
        self._running = False

    def activate(self, events_path):
        # Capture actual returned witnesses; never manufacture a cursor at EOF.
        pages = {
            "outcomes": self.core.read_outcomes_page(limit=1),
            "source_links": self.core.read_source_links_page(limit=1),
            "events": self.portraits.read_events(limit=1),
            "plans": self.portraits.list_plans(limit=1),
        }
        identity = {
            k: pages["outcomes"]["cursor"][k]
            for k in ("scope", "journal_id", "file_id")
        }
        witnesses = {k: v["cursor"] for k, v in pages.items()}
        witnesses["ledger"] = self.ledger.read_fence()
        old = self.state.get("activation")
        if old:
            for name, cursor in witnesses.items():
                prior = old["witnesses"][name]
                keys = (
                    ("scope", "journal_id", "file_id")
                    if name in ("outcomes", "source_links")
                    else ("database_id", "file_id")
                    if name == "ledger"
                    else ("database_id", "incarnation")
                )
                if any(cursor[k] != prior[k] for k in keys):
                    raise ValueError(
                        "configured server database replaced; reconciliation required"
                    )
        else:
            lines = 0
            try:
                with Path(events_path).open("rb") as stream:
                    for _ in stream:
                        lines += 1
            except FileNotFoundError:
                pass
            self.state.put(
                "activation", {"physical_eof": lines, "witnesses": witnesses}
            )
        self.identity = identity
        self.mute.muted = self.state.get("active_run") is not None
        if old:
            self.core.read_outcomes_page(cursor=old["witnesses"]["outcomes"], limit=1)
            self.core.read_source_links_page(
                cursor=old["witnesses"]["source_links"], limit=1
            )
            self.portraits.read_events(cursor=old["witnesses"]["events"], limit=1)
            self.portraits.list_plans(cursor=old["witnesses"]["plans"], limit=1)
            before = old["witnesses"]["ledger"]
            after = witnesses["ledger"]
            if after["sequence"] < before["sequence"] or (
                after["sequence"] == before["sequence"]
                and after["prefix"] != before["prefix"]
            ):
                raise ValueError("runtime activation history rolled back")

    def observe_lifecycle(self, event):
        if event.event not in ("start", "finish"):
            self.state.quarantine(
                "line:" + str(event.line_number),
                {
                    "line": event.line_number,
                    "kind": event.event,
                    "hash": event.line_hash,
                    "reason": "unknown lifecycle kind",
                },
            )
            return
        if not event.run_id:
            self.state.quarantine(
                "line:" + str(event.line_number),
                {
                    "line": event.line_number,
                    "kind": event.event,
                    "hash": event.line_hash,
                    "reason": "missing lifecycle correlation",
                },
            )
            return
        cutover = self.state.get("activation")["physical_eof"]
        gen = self.state.freeze_generation(
            event.run_id,
            str(event.version),
            self.groups(),
            event.line_number > cutover and event.event == "start",
        )
        if event.event == "start" and gen["eligible"] and not gen["terminal"]:
            self.state.put("active_run", event.run_id)
            self.mute.muted = True
        if event.event == "finish":
            self.state.update_generation(event.run_id, finish_observed=True)

    def _terminal(self, outcome):
        run = outcome["run_id"]
        active = (
            outcome["sequence"]
            > self.state.get("activation")["witnesses"]["outcomes"]["high_water"]
        )
        gen = self.state.freeze_generation(
            run, outcome.get("version"), self.groups(), active
        )
        if (
            gen["version"] is not None
            and outcome.get("version") is not None
            and str(gen["version"]) != str(outcome["version"])
        ):
            raise ValueError("lifecycle/Core version disagreement")
        eligible = active and (
            outcome["announcement_eligible"]
            or (
                outcome["classification"] == "unknown"
                and outcome["phase"] in ("failed", "interrupted")
            )
        )
        if (
            outcome.get("classification") in ("bootstrap", "noop")
            or outcome.get("cards", {}).get("reason") == "baseline"
        ):
            eligible = False
        self.state.update_generation(
            run,
            terminal=outcome,
            eligible=eligible,
            deferred=True,
            core_identity=self.identity,
            source_link=self.state.get(
                "source_link:" + run, gen.get("source_link", {})
            ),
        )
        # Durable handoff above, before any notifier wait. No Gateway state access.
        if self.state.get("active_run") in (None, run):
            self.state.put("active_run", None)
            self.mute.muted = False

    def observe_once(self):
        """Bounded Core handoff only; never awaits a notification."""
        if self.identity is None:
            raise RuntimeError("consumer not activated")
        active = self.state.get("active_run")
        if active:
            outcome = self.core.read_run(active)
            if outcome:
                self._terminal(outcome)
        for stream, read in (
            ("outcomes", self.core.read_outcomes_page),
            ("source_links", self.core.read_source_links_page),
        ):
            page = read(
                cursor=self.state.get("cursor:" + stream), limit=16, max_bytes=262144
            )
            for item in page["items"]:
                if stream == "outcomes":
                    self._terminal(item)
                else:
                    self.state.put("source_link:" + item["run_id"], item)
                    gen = self.state.generation(item["run_id"])
                    if gen:
                        self.state.update_generation(item["run_id"], source_link=item)
            self.state.put("cursor:" + stream, page["cursor"])
        # Resume unresolved runs in a rotating bounded page, including missing lifecycle finish.
        pending = self.state.pending(self.state.get("observe_pending_after", ""))
        self.state.put("observe_pending_after", pending[-1][0] if pending else "")
        for run, gen in pending:
            if not gen["terminal"]:
                outcome = self.core.read_run(run)
                if outcome:
                    self._terminal(outcome)

    async def poll_once(self):
        """Single-step entry for an idle consumer; run() owns the live loops."""
        if self._running:
            raise RuntimeError("consumer run owns observation and delivery")
        self.observe_once()
        await self.deliver_once()

    async def deliver_once(self):
        """One serialized delivery pass owns plan/event cursors and chunk ACKs."""
        async with self._delivery_lock:
            await self._deliver_once()

    async def _deliver_once(self):
        pending = self.state.pending(self.state.get("pending_after", ""))
        self.state.put("pending_after", pending[-1][0] if pending else "")
        # Discover plans separately so a true zero-member registration can complete.
        plans = self.portraits.list_plans(
            cursor=self.state.get("cursor:plans"), limit=16
        )
        for plan in plans["plans"]:
            self.state.put("plan:" + plan["plan_id"], plan)
        self.state.put("cursor:plans", plans["cursor"])
        # Card/start work is independent of accepted-event cursor progress.
        for run, _ in pending:
            gen = self.state.generation(run)
            if gen["terminal"] and not gen["done"]:
                await self._generation(run)
        page = self.portraits.read_events(
            cursor=self.state.get("cursor:events"), limit=1, max_bytes=262144
        )
        for event in page["events"]:
            if not await self._event(event):
                return
        self.state.put("cursor:events", page["cursor"])
        for run, _ in pending:
            gen = self.state.generation(run)
            if gen["terminal"] and not gen["done"]:
                await self._generation(run)

    def _mapping(self, run):
        try:
            return self.ledger.read_run_mapping(self.identity, run)
        except KeyError:
            return None

    def _verify_owner(self, run, mapping):
        pid = mapping["plan_id"]
        manifest = self.portraits.read_manifest(pid)
        snapshot = self.portraits.read_plan_snapshot(pid)
        proof = mapping["registration"]
        expected = {
            "database_id": self.portraits.database_id,
            "incarnation": self.portraits.incarnation,
            "path": str(self.portraits.path),
            "plan_id": pid,
            "registration": snapshot["registration"],
        }
        if proof != expected or mapping["delivery_owner"] != {
            "core_identity": self.identity,
            "run_id": run,
        }:
            raise ValueError("registered plan owner/proof mismatch")
        if manifest.get("origin_core") != mapping[
            "origin_core"
        ] or manifest != self.ledger.read_intent(self.identity, run).get("manifest"):
            raise ValueError("immutable registered origin manifest mismatch")
        if mapping["origin_core"] != {
            "identity": self.identity,
            "outcome": self.state.generation(run)["terminal"],
        }:
            raise ValueError("registered owner Core outcome mismatch")
        return manifest, snapshot

    async def _text(self, run, name, text):
        self.state.freeze_intent(
            run, name, [{"kind": "text", "text": self.label + text}]
        )
        await self.state.submit(run, name, self.sink)

    async def _final(self, run, text):
        await self._text(run, "final", text)
        self.state.update_generation(run, done=True)

    def _problem(self, run, kind, reason):
        self.state.quarantine(
            run + ":" + kind, {"run": run, "kind": kind, "reason": str(reason)[:500]}
        )
        gen = self.state.generation(run)
        problems = dict(gen.get("problems", {}))
        problems[kind] = str(reason)[:500]
        self.state.update_generation(run, problems=problems)

    def _retry_missing(self, run, kind, error):
        key = "retry:" + run + ":" + kind
        now = self.clock()
        retry = self.state.get(
            key,
            {
                "attempts": 0,
                "deadline": now + 300,
                "retry_at": now,
                "max_attempts": 3,
                "delays": [1, 5],
            },
        )
        if now < retry["retry_at"]:
            return False
        retry["attempts"] += 1
        if retry["attempts"] >= 3 or now >= retry["deadline"]:
            self._problem(run, kind, error)
            self.state.put(key, retry)
            return True
        retry["retry_at"] = now + retry["delays"][retry["attempts"] - 1]
        self.state.put(key, retry)
        return False

    async def _cards(self, run):
        gen = self.state.generation(run)
        if gen["barrier"]:
            return True
        if "cards" in gen.get("problems", {}):
            return False
        outcome = gen["terminal"]
        evidence = outcome["cards"]
        if evidence["kind"] == "handoff_unresolved":
            evidence = gen.get("source_link", {}).get("cards", evidence)
        if evidence["kind"] == "none":
            self.state.update_generation(
                run, barrier={"evidence": evidence, "recipients": gen["recipients"]}
            )
            return True
        if evidence["kind"] != "batch":
            return False
        try:
            await self._card_batch(run, evidence)
        except FileNotFoundError as exc:
            self._retry_missing(run, "cards", exc)
            return False
        except ValueError as exc:
            self._problem(run, "cards", exc)
            return False
        self.state.update_generation(
            run, barrier={"digest": digest(evidence), "recipients": gen["recipients"]}
        )
        # The producer's version directory has no run-owned cleanup/CAS protocol.
        # Retain it: a hash-check then unlink can delete a newer same-version run.
        self.state.put(
            "cleanup:" + run,
            {
                "disposition": "cleanup_deferred",
                "reason": "live producer folder requires coordinated cleanup",
            },
        )
        return True

    async def _card_batch(self, run, evidence):
        from server_app.portraits.source_io import MetadataRef, read_metadata

        ref = MetadataRef(**evidence["manifest"])
        if ref.scope_key != self.identity["scope"]:
            raise ValueError("card metadata scope mismatch")
        raw = read_metadata(ref, data_root=self.updater_root / "portrait_sources")
        if hashlib.sha256(raw).hexdigest() != evidence["manifest_sha256"]:
            raise ValueError("card manifest mismatch")
        manifest = json.loads(raw)
        gen = self.state.generation(run)
        if str(manifest["version"]) != str(gen["version"]):
            raise ValueError("card manifest version mismatch")
        entries = manifest["characters"]
        if len(entries) != len(evidence["files"]):
            raise ValueError("card manifest membership mismatch")
        items = []
        for index, (entry, file) in enumerate(
            zip(entries, evidence["files"], strict=True)
        ):
            name = entry["filename"]
            relative = f"outbox/{gen['version']}/{name}"
            if (
                Path(name).name != name
                or file["path"] != relative
                or evidence["keys"][index]
                != f"game-card:{gen['version']}:{entry['character_id']}:{name}"
            ):
                raise ValueError("card immutable key/path mismatch")
            snapshot = self.state.path.parent / "card_snapshots" / file["sha256"]
            if snapshot.exists():
                data = snapshot.read_bytes()
            else:
                source = self.updater_root / relative
                if (
                    source.is_symlink()
                    or source.resolve().parent
                    != self.updater_root / "outbox" / str(gen["version"])
                ):
                    raise ValueError("card source path escape")
                with source.open("rb") as stream:
                    data = stream.read(20 * 1024 * 1024 + 1)
            if (
                len(data) > 20 * 1024 * 1024
                or hashlib.sha256(data).hexdigest() != file["sha256"]
            ):
                raise ValueError("card copied bytes mismatch")
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            if not snapshot.exists():
                with snapshot.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            items.append(
                {
                    "kind": "image",
                    "media_path": str(snapshot),
                    "text": self.card_template.format(
                        version=gen["version"], name=entry["name"], file_name=name
                    ),
                }
            )
        self.state.freeze_intent(run, "cards", items)
        await self.state.submit(run, "cards", self.sink)

    async def _generation(self, run):
        gen = self.state.generation(run)
        outcome = gen["terminal"]
        if not gen["recipients"]:
            self.state.update_generation(run, done=True, disposition="empty_recipients")
            return
        if not gen["eligible"]:
            self.state.update_generation(
                run, done=True, disposition="suppressed_history_or_baseline"
            )
            return
        if outcome["phase"] in ("failed", "interrupted"):
            self._problem(run, "cards", "Core未完成，图鉴及立绘未发布")
            await self._final(
                run,
                f"版本{gen['version']}更新失败，服务已恢复；新增角色{outcome['counts'].get('new_character_count', 0)}，立绘未发布。",
            )
            return
        await self._text(run, "start", "本次游戏数据已就绪，开始发布图鉴与立绘。")
        barrier = await self._cards(run)
        mapping = self._mapping(run)
        phase = mapping["disposition"] if mapping else "pending"
        source_failed = gen.get("source_link", {}).get("state") == "failed"
        unavailable = source_failed or phase in (
            "source_failed",
            "preparation_exhausted",
            "superseded_before_registration",
        )
        if not barrier and unavailable:
            self._problem(run, "cards", "图鉴交接/发布不可用，立绘未发布")
        if not barrier and "cards" not in self.state.generation(run).get(
            "problems", {}
        ):
            return
        if unavailable:
            self._problem(run, "portraits", "source/preparation unavailable: " + phase)
            reason = (
                "立绘源数据/准备不可用，立绘未发布。"
                if barrier
                else "图鉴交接/发布不可用，立绘未发布。"
            )
            await self._final(
                run,
                f"版本{gen['version']}游戏数据更新完成，新增角色{outcome['counts'].get('new_character_count', 0)}；{reason}",
            )
            return
        if phase == "existing_plan_alias":
            text = f"版本{gen['version']}更新完成，新增角色{outcome['counts'].get('new_character_count', 0)}。"
            if not barrier:
                text += "图鉴交接/发布不可用，本次无新增立绘发布。"
            await self._final(run, text)
            return
        if phase != "registered_origin":
            return
        manifest, snapshot = self._verify_owner(run, mapping)
        if (
            manifest.get("kind") == "manual"
            or manifest.get("delivery_disposition") == "manual_query_only"
        ):
            self.state.update_generation(
                run, done=True, disposition="manual_query_only"
            )
            return
        if not snapshot["sealed"]:
            return
        cursor = self.state.get("cursor:events", {})
        if cursor.get("position", 0) < snapshot["watermark"]:
            return
        counts = snapshot["counts"]
        gen = self.state.generation(run)
        text = f"版本{gen['version']}更新完成，新增角色{outcome['counts'].get('new_character_count', 0)}；立绘成功{counts['success']}、失败{counts['failed']}、阻塞{counts['blocked']}、取消{counts['cancelled']}。"
        if gen.get("problems"):
            text += f"发布问题{len(gen['problems'])}项，部分立绘未发布。"
            if "cards" in gen["problems"]:
                text += "图鉴交接/发布不可用，立绘未发布。"
        await self._final(run, text)

    async def _event(self, event):
        name = "portrait:" + str(event["id"])
        if event["provenance"] != "registered":
            self.state.put(
                "event:" + str(event["id"]), {"disposition": "legacy_lineage_unknown"}
            )
            return True
        manifest = self.portraits.read_manifest(event["plan_id"])
        if (
            manifest["kind"] == "manual"
            or manifest.get("delivery_disposition") == "manual_query_only"
        ):
            self.state.put(
                "event:" + str(event["id"]), {"disposition": "manual_query_only"}
            )
            return True
        origin = manifest.get("origin_core")
        if not origin or origin["identity"] != self.identity:
            raise ValueError("registered event origin identity mismatch")
        run = origin["outcome"]["run_id"]
        gen = self.state.generation(run)
        if not gen:
            outcome = self.core.read_run(run)
            if not outcome:
                return False
            self._terminal(outcome)
            gen = self.state.generation(run)
        if not gen["recipients"]:
            self.state.put(
                "event:" + str(event["id"]), {"disposition": "empty_recipients"}
            )
            return True
        if not gen["eligible"]:
            self.state.put(
                "event:" + str(event["id"]),
                {"disposition": "suppressed_history_or_baseline"},
            )
            return True
        mapping = self._mapping(run)
        if not mapping or mapping["disposition"] != "registered_origin":
            return False
        self._verify_owner(run, mapping)
        if mapping["plan_id"] != event["plan_id"]:
            raise ValueError("event plan owner mismatch")
        if event["result"]["status"] != "success":
            self.state.put(
                "event:" + str(event["id"]), {"disposition": "renderer_terminal"}
            )
            return True
        if not gen["barrier"]:
            if "cards" in gen.get("problems", {}):
                self._problem(
                    run, name, "card barrier unavailable; portrait not published"
                )
                return True
            return False
        result = event["result"]
        if self.state.accepted(run, name):
            return True
        try:
            sha = result["artifact_sha256"]
            relative = result["artifact_path"]
            if (
                relative != "artifacts/" + sha + ".png"
                or result["width"] * result["height"] > 8_388_608
            ):
                raise ValueError("invalid delivery artifact/budget")
            path = self.artifact_root / relative
            if (
                path.is_symlink()
                or path.resolve().parent != self.artifact_root / "artifacts"
            ):
                raise ValueError("artifact root escape")
            with path.open("rb") as stream:
                data = stream.read(20 * 1024 * 1024 + 1)
            if len(data) > 20 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != sha:
                raise ValueError("delivery digest/size mismatch")
        except FileNotFoundError as exc:
            return self._retry_missing(run, name, exc)
        except ValueError as exc:
            self._problem(run, name, exc)
            return True
        self.state.freeze_intent(
            run,
            name,
            [
                {
                    "kind": "image",
                    "image_delivery_mode": "local_multipart",
                    "source_id": self.state.scope["source"],
                    "artifact_path": relative,
                    "expected_sha256": sha,
                }
            ],
        )
        await self.state.submit(run, name, self.sink)
        return True

    async def _loop(self, role, stop_event, interval):
        while not stop_event.is_set():
            try:
                if role == "observer":
                    self.observe_once()
                else:
                    await self.deliver_once()
                error = None
            except Exception as exc:
                error = {"type": type(exc).__name__, "reason": str(exc)[:500]}
                LOG.exception("portrait consumer %s loop failed", role)
            if self.state.get("health_error:" + role) != error:
                self.state.put("health_error:" + role, error)
            errors = {
                name: self.state.get("health_error:" + name)
                for name in ("observer", "delivery")
            }
            combined = errors if any(errors.values()) else None
            if self.state.get("health_error") != combined:
                self.state.put("health_error", combined)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass

    async def run(self, stop_event, interval=1):
        """Own independent bounded observation and exactly one delivery worker."""
        if self._running:
            raise RuntimeError("consumer already running")
        self._running = True
        tasks = []
        try:
            tasks = [
                asyncio.create_task(self._loop(role, stop_event, interval))
                for role in ("observer", "delivery")
            ]
            stopped = asyncio.create_task(stop_event.wait())
            tasks.append(stopped)
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._running = False


def build_consumer(config, watcher, settings, sink):
    """Explicit deployment mapping; pure server packages must already be installed."""
    from .portrait_delivery_state import DeliveryState

    if type(settings) is not dict or type(settings.get("enabled", False)) is not bool:
        raise ValueError("portrait_delivery enabled must be boolean")
    if not settings.get("enabled", False):
        return None
    required = {
        "enabled",
        "source_id",
        "core_scope",
        "core_journal",
        "runtime_ledger",
        "portrait_store",
        "artifact_root",
        "updater_root",
        "image_delivery_mode",
    }
    if (
        set(settings) != required
        or settings["image_delivery_mode"] != "local_multipart"
    ):
        raise ValueError(
            "enabled portrait_delivery requires exact explicit mapping and local_multipart"
        )
    paths = {}
    for key in (
        "core_journal",
        "runtime_ledger",
        "portrait_store",
        "artifact_root",
        "updater_root",
    ):
        path = Path(settings[key])
        if (
            not path.is_absolute()
            or ".." in path.parts
            or any(p.is_symlink() for p in (path, *path.parents))
        ):
            raise ValueError(
                "portrait_delivery paths must be absolute and non-symlinked"
            )
        paths[key] = path.resolve()
    if Path(watcher.source.outbox_dir).resolve() != paths["updater_root"] / "outbox":
        raise ValueError("portrait_delivery updater/outbox mapping mismatch")
    instance = config.instance.instance_id
    source = settings["source_id"]
    mapped = config.notification.portrait_roots.get(instance, {}).get(source)
    if not mapped or Path(mapped).resolve() != paths["artifact_root"]:
        raise ValueError("portrait_delivery notifier root mismatch")
    from server_app.portraits.core_outcomes import CoreReader
    from server_app.portraits.reader import PortraitReader
    from server_app.portraits.runtime_ledger_reader import RuntimeLedgerReader

    core = CoreReader(paths["core_journal"], scope=settings["core_scope"])
    core.read_outcomes_page(limit=1)
    ledger = RuntimeLedgerReader(paths["runtime_ledger"], instance, source)
    portraits = PortraitReader(paths["portrait_store"], instance, source)
    scope = {
        "instance": instance,
        "source": source,
        "watch_source": watcher.source.name,
        "core_scope": settings["core_scope"],
        **{k: str(v) for k, v in paths.items()},
    }
    state = DeliveryState(Path(watcher.state_dir) / "portrait_delivery.sqlite", scope)
    consumer = PortraitConsumer(
        state,
        core,
        ledger,
        portraits,
        sink,
        paths["artifact_root"],
        paths["updater_root"],
        watcher._eligible_groups,
        watcher.mute,
        label="" if watcher.source.name == "main" else "[" + watcher.source.name + "]",
        card_template=config.message.template,
    )
    consumer.activate(paths["updater_root"] / "update_events.jsonl")
    return consumer
