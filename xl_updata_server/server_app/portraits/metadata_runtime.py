"""Opt-in automatic portrait metadata coordination; never executes portrait jobs."""

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MetadataConfig:
    enabled: bool = False
    instance_id: str = ""
    source_id: str = ""
    cdn_base: str = ""
    updater_data_root: Path | None = None
    prepared_source_root: Path | None = None
    portrait_root: Path | None = None
    work_root: Path | None = None
    ledger_path: Path | None = None
    store_path: Path | None = None
    runtime: object = None
    tool_fingerprint: str = ""
    resource_policy: object = None
    publication_guards: tuple = ()
    legacy_status_paths: tuple = ()
    plan_window_seconds: float = 604800

    def __post_init__(self):
        import math

        value = self.plan_window_seconds
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 300 <= value <= 31536000
        ):
            raise ValueError("plan window must be finite in [300, 31536000]")


class MetadataRuntime:
    def __init__(self, config):
        if type(config) is not MetadataConfig or type(config.enabled) is not bool:
            raise ValueError("explicit metadata configuration required")
        self.config = config
        self.ledger = None
        self._scan = None
        self._advance_next = False
        self._discovery_stream = 0
        self._tick_mutex = threading.Lock()
        if config.enabled:
            self._validate_config()

    def _validate_config(self, *, require_store=True):
        from .core_outcomes import CoreJournal, CoreReader
        from .executor import ResourcePolicy
        from .publication import Publication
        from .reader import PortraitReader
        from .runtime_ledger_reader import hash_value, path_check, text
        from .source_prepare import _runtime

        c = self.config
        text(c.instance_id)
        text(c.source_id)
        from urllib.parse import urlsplit

        url = urlsplit(c.cdn_base)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("explicit HTTPS CDN required")
        roots = [
            path_check(p)
            for p in (
                c.updater_data_root,
                c.prepared_source_root,
                c.portrait_root,
                c.work_root,
            )
        ]
        roots += [path_check(c.ledger_path).parent, path_check(c.store_path).parent]
        for index, root in enumerate(roots):
            if any(
                root == other or root in other.parents or other in root.parents
                for other in roots[index + 1 :]
            ):
                raise ValueError("metadata storage roles overlap")
        if type(c.resource_policy) is not ResourcePolicy:
            raise ValueError("actual ResourcePolicy required")
        if (c.resource_policy.admission_bytes < 640 * 1024**2
                or c.resource_policy.abort_bytes < 128 * 1024**2):
            raise ValueError("metadata memory safety floor")
        hash_value(c.tool_fingerprint)
        _runtime(c.runtime, c.tool_fingerprint)
        for root in (
            c.runtime.server_root,
            c.runtime.tool_root,
            *c.runtime.dependency_roots,
        ):
            if (
                c.work_root == root
                or c.work_root in root.parents
                or root in c.work_root.parents
            ):
                raise ValueError("work overlaps immutable runtime")
        self.publication = Publication(
            c.updater_data_root,
            instance_id=c.instance_id,
            source_id=c.source_id,
            cdn_base=c.cdn_base,
        )
        required = (self.publication.guard_path, self.publication.scope)
        if required not in c.publication_guards or len(
            dict(c.publication_guards)
        ) != len(c.publication_guards):
            raise ValueError("own Publication guard scope required")
        statuses = tuple(p for p, _ in c.publication_guards) + c.legacy_status_paths
        if len(set(statuses)) != len(statuses) or set(statuses) != set(
            c.resource_policy.updater_status_paths
        ):
            raise ValueError("every updater status requires explicit role")
        for path, scope in c.publication_guards:
            path_check(path)
            hash_value(scope)
        for path in c.legacy_status_paths:
            path_check(path)
        self._guards(require_idle=False)
        self.core = CoreReader(
            CoreJournal(self.publication).path, scope=self.publication.scope
        )
        self.core.identity()
        if require_store or c.store_path.exists():
            self.store_reader = PortraitReader(c.store_path, c.instance_id, c.source_id)
            self._check_missing_ledger()

    def _check_missing_ledger(self):
        if self.config.ledger_path.exists():
            return
        if (
            Path(str(self.config.ledger_path) + ".tick.lock").exists()
            or self.store_reader.list_plans(limit=1)["plans"]
        ):
            raise ValueError("runtime ledger missing despite existing state")

    def _guards(self, *, require_idle=True):
        from .executor import Deferred, _read_status

        for path, scope in self.config.publication_guards:
            # _read_status authenticates the regular non-symlink bounded file.
            busy = _read_status(path)
            from .source_io import _directory, _pairs, _read_file

            with (
                _directory(path.parent) as directory,
                _read_file(directory, path.name) as stream,
            ):
                raw = stream.read(16385)
            if len(raw) > 16384:
                raise ValueError("oversized Publication guard")
            value = json.loads(raw, object_pairs_hook=_pairs)
            if (
                set(value) != {"schema", "scope", "updating"}
                or type(value["schema"]) is not int
                or value["schema"] != 1
                or value["scope"] != scope
                or type(value["updating"]) is not bool
            ):
                raise ValueError("Publication guard scope/schema mismatch")
            if require_idle and (busy or value["updating"]):
                raise Deferred("update")
        for path in self.config.legacy_status_paths:
            if _read_status(path) and require_idle:
                raise Deferred("update")

    def health(self):
        if not self.config.enabled:
            return {"enabled": False}
        self._guards(require_idle=False)
        from .source_prepare import _runtime

        _runtime(self.config.runtime, self.config.tool_fingerprint)
        self.core.read_outcomes_page(limit=1)
        self.store_reader.list_plans(limit=1)
        if not self.config.ledger_path.exists():
            if self.ledger is not None:
                raise ValueError("runtime ledger disappeared")
            return {"enabled": True, "status": "uninitialized"}
        from .runtime_ledger_reader import RuntimeLedgerReader

        reader = self.ledger or RuntimeLedgerReader(
            self.config.ledger_path, self.config.instance_id, self.config.source_id
        )
        for stream in ("outcomes", "source_links"):
            getattr(self.core, "read_" + stream + "_page")(
                cursor=reader.read_cursor(stream), limit=1
            )
        page = reader.read_runs_page(limit=1)
        for mapping in page["items"]:
            self.core.read_outcomes_page(cursor=mapping["observation_cursor"], limit=1)
            self._proof(mapping, ledger=reader)
        return dict(reader.health(), status="existing", sampled_runs=len(page["items"]))

    def tick(self, *, now, stop_event=None):
        if not self.config.enabled:
            return {"enabled": False}
        if not self._tick_mutex.acquire(blocking=False):
            return {"enabled": True, "status": "busy"}
        try:
            return self._tick(now=now, stop_event=stop_event)
        finally:
            self._tick_mutex.release()

    def _tick(self, *, now, stop_event):
        from .executor import Deferred
        from .runtime_ledger import RuntimeLedger
        from .runtime_ledger_reader import number

        number(now)
        if stop_event is not None and stop_event.is_set():
            return {"enabled": True, "status": "stop"}
        try:
            self._guards()
            if self.ledger is None:
                self._check_missing_ledger()
                c = self.config
                self.ledger = RuntimeLedger(
                    c.ledger_path, c.instance_id, c.source_id, enabled=True
                )
            with self.ledger.tick_owned() as owned:
                if not owned:
                    return {"enabled": True, "status": "busy"}
                for stream in ("outcomes", "source_links"):
                    getattr(self.core, "read_" + stream + "_page")(
                        cursor=self.ledger.read_cursor(stream), limit=1
                    )
                if not self._reconcile():
                    return {"enabled": True, "status": "reconciling"}
                scan = self._scan
                if scan["repair"]:
                    key = min(
                        scan["repair"].values(), key=lambda value: value["promotion"]
                    )
                    status = self._commit(self._mapping(key))
                    return {
                        "enabled": True,
                        "status": "repaired" if status == "committed" else status,
                    }
                pending = scan["pending"]
                if not (pending and self._advance_next) and self._discover(now):
                    self._advance_next = True
                    return {"enabled": True, "status": "observed"}
                if pending:
                    self._advance_next = False
                    key = min(pending.values(), key=lambda value: value["promotion"])
                    return {
                        "enabled": True,
                        "status": self._advance(
                            self._mapping(key),
                            now,
                            stop_event,
                            scan["comparison"],
                        ),
                    }
                return {"enabled": True, "status": "idle"}
        except Deferred as error:
            return {"enabled": True, "status": "deferred", "reason": str(error)}

    @staticmethod
    def _key(mapping):
        return {
            "identity": mapping["core_identity"],
            "run_id": mapping["run_id"],
            "promotion": mapping["original_observed"]["promotion"],
        }

    def _mapping(self, key):
        return self.ledger.read_run_mapping(key["identity"], key["run_id"])

    def _proof(self, mapping, *, ledger=None):
        """Read actual original Store evidence without changing the scan anchor."""
        from .runtime_ledger_reader import MAX_DOCUMENT, encode

        ledger = ledger or self.ledger
        origin = mapping["origin_core"]
        original = ledger.read_run_mapping(
            origin["identity"], origin["outcome"]["run_id"]
        )
        pid = original.get("frozen_plan_id")
        if pid is None:
            return False
        try:
            manifest = self.store_reader.read_manifest(pid)
        except KeyError:
            if original["registration"] is not None:
                raise ValueError("registered plan disappeared") from None
            return False
        frozen = ledger.read_intent(original["core_identity"], original["run_id"])
        if "manifest" not in frozen or encode(manifest, MAX_DOCUMENT) != encode(
            frozen["manifest"], MAX_DOCUMENT
        ):
            raise ValueError("registered manifest conflicts with runtime intent")
        snapshot = self.store_reader.read_plan_snapshot(pid)
        proof = {
            "database_id": self.store_reader.database_id,
            "incarnation": self.store_reader.incarnation,
            "path": str(self.store_reader.path),
            "plan_id": pid,
            "registration": snapshot["registration"],
        }
        if original["registration"] is not None and original["registration"] != proof:
            raise ValueError("registered Store incarnation changed")
        return True

    def _reconcile(self):
        if self._scan is None:
            self._scan = {
                "cursor": None,
                "store_cursor": None,
                "audit_cursor": None,
                "ready": False,
                "repair": {},
                "pending": {},
                "comparison": None,
            }
        state = self._scan
        changes = self.ledger.read_changes_page(cursor=state["cursor"], limit=16)
        for mapping in changes["items"]:
            self._reconcile_mapping(mapping)
        state["cursor"] = changes["cursor"]
        plans = self.store_reader.list_plans(cursor=state["store_cursor"], limit=16)
        for plan in plans["plans"]:
            manifest = self.store_reader.read_manifest(plan["plan_id"])
            if manifest["kind"] != "automatic":
                continue
            origin = manifest.get("origin_core")
            if origin is None:
                raise ValueError("automatic Store plan has no runtime origin")
            try:
                mapping = self.ledger.read_run_mapping(
                    origin["identity"], origin["outcome"]["run_id"]
                )
            except KeyError as error:
                raise ValueError("orphan registered automatic plan") from error
            if mapping.get("frozen_plan_id") != plan["plan_id"] or not self._proof(
                mapping
            ):
                raise ValueError("orphan Store registration")
            self._reconcile_mapping(mapping)
        state["store_cursor"] = plans["cursor"]
        state["ready"] = (
            changes["caught_up"]
            and plans["cursor"]["position"] == plans["cursor"]["highwater"]
        )
        # Continue checking retained payloads even when no legitimate history
        # entry announces their alteration. Appends cannot restart this audit;
        # insertions behind its keyset are independently covered by history.
        audit = self.ledger.read_runs_page(
            cursor=state["audit_cursor"], limit=1, live=True
        )
        for mapping in audit["items"]:
            self._reconcile_mapping(mapping)
        state["audit_cursor"] = audit["cursor"] if audit["items"] else None
        return state["ready"]

    def _reconcile_mapping(self, mapping):
        state = self._scan
        self.core.read_outcomes_page(cursor=mapping["observation_cursor"], limit=1)
        if self.core.read_run(mapping["run_id"]) != mapping["original_observed"]:
            raise ValueError("observed Core outcome changed")
        registered = self._proof(mapping)
        phase = mapping["metadata_phase"]
        key = self._key(mapping)
        run = mapping["run_id"]
        state["pending"].pop(run, None)
        state["repair"].pop(run, None)
        if registered and phase in ("acked", "existing_plan_alias"):
            if (
                state["comparison"] is None
                or key["promotion"] > state["comparison"]["promotion"]
            ):
                state["comparison"] = key
        elif registered:
            state["repair"][run] = key
        elif phase not in {
            "acked",
            "existing_plan_alias",
            "ineligible_original_run",
            "superseded_before_registration",
            "source_failed",
            "preparation_exhausted",
        }:
            state["pending"][run] = key

    def execution_ready(self):
        """Fresh latest-promotion proof, never a cached boolean execution permit."""
        if self._scan is None or not self._scan["ready"] or self._scan["repair"]:
            return False
        latest = self.core.latest_promotion()
        if latest is None:
            return not self._scan["pending"]
        cursor = self.ledger.read_cursor("outcomes")
        if cursor is None:
            return False
        identity = {key: cursor[key] for key in ("scope", "journal_id", "file_id")}
        try:
            mapping = self.ledger.read_run_mapping(identity, latest["run_id"])
        except KeyError:
            return False
        self._reconcile_mapping(mapping)
        if mapping["metadata_phase"] in ("acked", "existing_plan_alias"):
            return self._proof(mapping)
        return mapping["metadata_phase"] in ("source_failed", "preparation_exhausted")

    def _discover(self, now):
        streams = ("outcomes", "source_links")
        for offset in range(2):
            position = (self._discovery_stream + offset) % 2
            stream = streams[position]
            previous = self.ledger.read_cursor(stream)
            page = getattr(self.core, "read_" + stream + "_page")(
                cursor=previous, limit=1
            )
            if not page["items"]:
                continue
            item = page["items"][0]
            identity = {
                k: page["cursor"][k] for k in ("scope", "journal_id", "file_id")
            }
            outcome = (
                item if stream == "outcomes" else self.core.read_run(item["run_id"])
            )
            if stream == "source_links":
                try:
                    self.ledger.read_run_mapping(identity, item["run_id"])
                except KeyError:
                    continue  # Persist the original outcome before its independent link.
            self.ledger.observe(
                identity,
                outcome,
                previous_cursor=previous,
                cursor=page["cursor"],
                now=now,
                stream=stream,
                source_link=None if stream == "outcomes" else item,
            )
            self._discovery_stream = 1 - position
            return True
        return False

    def _latest(self, mapping):
        self.core.read_outcomes_page(cursor=mapping["observation_cursor"], limit=1)
        return self.core.latest_promotion() == mapping["original_observed"]

    def _source(self, mapping, now):
        from .core_outcomes import CoreJournal

        identity, run = mapping["core_identity"], mapping["run_id"]
        phase = mapping["metadata_phase"]
        if not self.publication.acquire_run():
            return "raw_busy"
        try:
            self._guards()
            journal = CoreJournal(self.publication)
            existing = journal.resolve_pending_source(
                run, outcomes_cursor=mapping["observation_cursor"], existing_only=True
            )
            if existing["status"] in ("ready", "failed"):
                self._record_resolution(identity, run, existing, recovered=True)
                return "source_resolved"
            if phase in ("source_resolution_exhausted", "fail_link_pending"):
                try:
                    journal.fail_source(run, code="source_resolution_exhausted")
                    self.ledger.source_failed(
                        identity, run, "source_resolution_exhausted", recovered=True
                    )
                except OSError:
                    self.ledger.fail_link_pending(identity, run)
                return "source_resolution_exhausted"
            if not self._latest(mapping):
                self.ledger.supersede_unregistered(identity, run, self.store_reader)
                return "superseded"
            if phase == "source_started":
                self.ledger.retry_source(identity, run, now=now)
                return "recovered_attempt"
            if not self.ledger.start_source(identity, run, now=now):
                return "source_wait"
            try:
                result = journal.resolve_pending_source(
                    run, outcomes_cursor=mapping["observation_cursor"]
                )
                if result["status"] in ("ready", "failed"):
                    self._record_resolution(identity, run, result, recovered=False)
                elif result["status"] == "superseded":
                    self.ledger.supersede_unregistered(identity, run, self.store_reader)
                else:
                    self.ledger.retry_source(identity, run, now=now)
            except OSError:
                self.ledger.retry_source(identity, run, now=now)
                raise
            return "source_resolved"
        finally:
            self.publication.release_run()

    def _record_resolution(self, identity, run, result, *, recovered):
        if result["status"] == "ready":
            request = self.publication.read_request(result["ref"])
            pin = self.publication.load_pin(request["version"])
            self.ledger.source_ready(
                identity,
                run,
                request_ref=asdict(result["ref"]),
                pin={"version": pin.version, "pin_digest": pin.pin_digest},
                recovered=recovered,
            )
        else:
            self.ledger.source_failed(
                identity, run, result["code"], recovered=recovered
            )

    def _advance(self, mapping, now, stop_event, comparison):
        from . import source_prepare as prepare
        from . import source_transfer as transfer
        from .executor import Deferred, ResourceSlot
        from .source_io import MetadataRef

        identity, run = mapping["core_identity"], mapping["run_id"]
        phase = mapping["metadata_phase"]
        if phase in ("manifest_frozen", "registered"):
            return self._commit(mapping)
        if phase in (
            "source_pending",
            "source_started",
            "source_resolution_exhausted",
            "fail_link_pending",
        ):
            return self._source(mapping, now)
        if not self._latest(mapping):
            self.ledger.supersede_unregistered(identity, run, self.store_reader)
            return "superseded"
        if phase == "prepare_started":
            self.ledger.retry_prepare(identity, run, now=now)
            return "recovered_attempt"
        if phase in ("source_ready", "prepare_pending"):
            c = self.config
            policy = mapping.get(
                "prepare_policy",
                {
                    "max_fetches": 9,
                    "max_fetches_per_replay": 3,
                    "max_wire_bytes": 3 * 64 * 1024**2,
                    "attempt_timeout": 120,
                },
            )
            self.ledger.freeze_prepare_policy(identity, run, policy)
            self.publication.read_request(MetadataRef(**mapping["request_ref"]))
            pin = self.publication.load_pin(mapping["pin"]["version"])
            c.work_root.mkdir(parents=True, exist_ok=True)
            with ResourceSlot(c.resource_policy) as slot:
                if not self.ledger.start_prepare(identity, run, now=now):
                    return "prepare_wait"
                started = time.monotonic()
                try:
                    timeout = min(
                        policy["attempt_timeout"],
                        mapping["prepare_budget"]["deadline"] - now,
                    )

                    def fetch_reserved(fixed, fetch, remaining):
                        transfer.validate_fetch(fixed, fetch)
                        if fetch.provenance not in ("category", "Dataowner"):
                            raise ValueError(
                                "metadata runtime cannot fetch Arts packages"
                            )
                        if not self.ledger.reserve_prepare_fetch(
                            identity, run, wire_size=fetch.size
                        ):
                            raise prepare.PreparationError(
                                "persistent metadata transfer budget exhausted"
                            )
                        return transfer.fetch_once(
                            slot,
                            fixed,
                            fetch,
                            data_root=c.prepared_source_root,
                            work_root=c.work_root,
                            runtime=c.runtime,
                            tool_fingerprint=c.tool_fingerprint,
                            stop_event=stop_event,
                            timeout_seconds=remaining,
                        )

                    ref = transfer.replay(
                        pin,
                        lambda fixed, remaining: prepare.prepare_source(
                            slot,
                            fixed,
                            data_root=c.prepared_source_root,
                            work_root=c.work_root,
                            runtime=c.runtime,
                            tool_fingerprint=c.tool_fingerprint,
                            open_remote=transfer.miss_transport(),
                            stop_event=stop_event,
                            timeout_seconds=remaining,
                        ),
                        fetch_reserved,
                        max_fetches=policy["max_fetches_per_replay"],
                        max_wire_bytes=policy["max_wire_bytes"],
                        timeout_seconds=timeout,
                        stop_event=stop_event,
                    )
                    self.ledger.prepared(identity, run, asdict(ref))
                except Deferred:
                    self.ledger.retry_prepare(
                        identity,
                        run,
                        now=now + time.monotonic() - started,
                        cooperative=True,
                    )
                    raise
                except prepare.PreparationError:
                    self.ledger.retry_prepare(
                        identity, run, now=now + time.monotonic() - started
                    )
                    return "prepare_retry"
            return "prepared"
        if phase == "prepared":
            from . import publisher
            from .model import digest
            from .planning import plan_changes

            p = mapping["prepared_ref"]
            ref = prepare.PreparedSourceRef(
                **dict(p, capture=MetadataRef(**p["capture"]))
            )
            desired = prepare.read_prepared_source(
                ref, data_root=self.config.prepared_source_root
            )["catalog"]
            pid = digest(
                [
                    self.config.instance_id,
                    self.config.source_id,
                    "automatic",
                    p["version"],
                    p["snapshot_digest"],
                    p["tool_fingerprint"],
                ]
            )
            try:
                existing = self.store_reader.read_manifest(pid)
            except KeyError:
                existing = None
            if existing is not None:
                origin = existing["origin_core"]
                original = self.ledger.read_run_mapping(
                    origin["identity"], origin["outcome"]["run_id"]
                )
                if not self._proof(original):
                    raise ValueError("alias original is not registered")
                frozen = self.ledger.read_intent(
                    original["core_identity"], original["run_id"]
                )
                if desired != frozen["desired"] or p != original["prepared_ref"]:
                    raise ValueError("automatic identity collision")
                with publisher.locked_mutation(self.config.portrait_root) as mutation:
                    if not self.publication.acquire_run():
                        return "raw_busy"
                    try:
                        self._guards()
                        if not self._latest(mapping):
                            self.ledger.supersede_unregistered(
                                identity, run, self.store_reader
                            )
                            return "superseded"
                        self.ledger.alias(
                            identity,
                            run,
                            original["core_identity"],
                            original["run_id"],
                            prepared_ref=p,
                            desired=desired,
                            reader=self.store_reader,
                            pending=True,
                        )
                        mutation.publish_desired(
                            frozen["desired"],
                            expected_current=publisher.catalog_token(
                                self.config.portrait_root
                            ),
                            execution_plan_id=pid,
                        )
                        self.ledger.ack_alias(identity, run, self.store_reader)
                    finally:
                        self.publication.release_run()
                return "aliased"
            comparator = None
            evidence = {"baseline": comparison is None}
            if comparison is not None:
                previous = self._mapping(comparison)
                if not self._proof(previous):
                    raise ValueError("comparison registration disappeared")
                origin = previous["origin_core"]
                comparator = self.ledger.read_intent(
                    origin["identity"], origin["outcome"]["run_id"]
                )["desired"]
                evidence.update(
                    effective_core=comparison,
                    origin_core=origin,
                    registration=previous["registration"],
                    accepted=[],
                )
                for version in (None, comparator["version"]):
                    try:
                        accepted = publisher.read_catalog(
                            self.config.portrait_root, version
                        )
                    except FileNotFoundError:
                        continue
                    evidence["accepted"].append(
                        {"version": version, "sha256": digest(accepted)}
                    )
                    comparator = publisher.carry_forward(comparator, accepted)
            jobs = plan_changes(comparator, desired)
            for job in jobs:
                job.update(
                    schema_version=2,
                    input_snapshot_digest=p["snapshot_digest"],
                    tool_fingerprint=p["tool_fingerprint"],
                    source_capture_ref=p,
                )
            self.ledger.freeze_intent(
                identity,
                run,
                desired=desired,
                comparator=comparator,
                comparison_evidence=evidence,
                jobs=jobs,
                recovery={
                    "max_crashes": 2,
                    "deadline": now + self.config.plan_window_seconds,
                    "backoff_seconds": 1,
                },
            )
            return "intent_frozen"
        if phase == "intent_frozen":
            self._stage(mapping)
            return "manifest_frozen"
        return phase

    def _stage(self, mapping):
        from .model import digest
        from .publisher import stage_desired_snapshot

        identity, run = mapping["core_identity"], mapping["run_id"]
        frozen = self.ledger.read_intent(identity, run)
        p = mapping["prepared_ref"]
        desired_ref = stage_desired_snapshot(
            frozen["desired"],
            self.config.portrait_root,
            execution_plan_id=mapping["frozen_plan_id"],
        )["desired_catalog_ref"]
        manifest = {
            "schema_version": 2,
            "instance_id": self.config.instance_id,
            "source_id": self.config.source_id,
            "version": p["version"],
            "kind": "automatic",
            "input_snapshot_digest": p["snapshot_digest"],
            "tool_fingerprint": p["tool_fingerprint"],
            "source_capture_ref": p,
            "core_publication_ref": mapping["request_ref"],
            "desired_catalog_ref": desired_ref,
            "comparison_evidence": frozen["comparison_evidence"],
            "recovery": frozen["recovery"],
            "jobs": frozen["jobs"],
            "expected_jobset_digest": digest(frozen["jobs"]),
            "origin_core": mapping["origin_core"],
            "delivery_disposition": "registered_origin",
        }
        self.ledger.freeze_manifest(identity, run, manifest)

    def _commit(self, mapping):
        from . import publisher
        from .store import PortraitStore

        if mapping["metadata_phase"] == "alias_registered":
            return self._commit_alias(mapping)
        identity, run = mapping["core_identity"], mapping["run_id"]
        frozen = self.ledger.read_intent(identity, run)
        registered = self._proof(mapping)
        with publisher.locked_mutation(self.config.portrait_root) as mutation:
            if not self.publication.acquire_run():
                return "raw_busy"
            try:
                self._guards()
                latest = self._latest(mapping)
                if not latest and not registered:
                    self.ledger.supersede_unregistered(identity, run, self.store_reader)
                    return "superseded"
                store = PortraitStore(
                    self.config.store_path,
                    self.config.instance_id,
                    self.config.source_id,
                )
                result = store.register_closed_plan(frozen["manifest"])
                if result["plan_id"] != mapping["frozen_plan_id"]:
                    raise ValueError("Store plan identity mismatch")
                self.ledger.prove_registered(identity, run, self.store_reader)
                if latest:
                    mutation.publish_desired(
                        frozen["desired"],
                        expected_current=publisher.catalog_token(
                            self.config.portrait_root
                        ),
                        execution_plan_id=mapping["frozen_plan_id"],
                    )
                if not frozen["jobs"]:
                    store.seal_plan(mapping["frozen_plan_id"])
                self.ledger.ack(identity, run)
                return "committed"
            finally:
                self.publication.release_run()

    def _commit_alias(self, mapping):
        from . import publisher

        if not self._proof(mapping):
            raise ValueError("alias registration disappeared")
        identity, run = mapping["core_identity"], mapping["run_id"]
        origin = mapping["origin_core"]
        frozen = self.ledger.read_intent(
            origin["identity"], origin["outcome"]["run_id"]
        )
        with publisher.locked_mutation(self.config.portrait_root) as mutation:
            if not self.publication.acquire_run():
                return "raw_busy"
            try:
                self._guards()
                if self._latest(mapping):
                    mutation.publish_desired(
                        frozen["desired"],
                        expected_current=publisher.catalog_token(
                            self.config.portrait_root
                        ),
                        execution_plan_id=mapping["plan_id"],
                    )
                self.ledger.ack_alias(identity, run, self.store_reader)
                return "committed"
            finally:
                self.publication.release_run()
