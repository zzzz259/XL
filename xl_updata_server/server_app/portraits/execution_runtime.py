"""Opt-in serial execution of authenticated closed portrait plans."""

import threading
import time
from dataclasses import dataclass, field

from .execution_lease import Cancellation, LeaseRenewer
from .metadata_runtime import MetadataConfig, MetadataRuntime


def initialize_store(config):
    """Explicit fresh install; never reconstruct a missing historical database."""
    import os
    from dataclasses import replace

    from .executor import ResourceSlot
    from .store import PortraitStore

    if type(config) is not MetadataConfig or not config.enabled:
        return "disabled"
    validator = MetadataRuntime(replace(config, enabled=False))
    validator.config = config
    validator._validate_config(require_store=False)
    validator._guards()
    if config.store_path.exists():
        return "existing"
    with ResourceSlot(config.resource_policy):
        if config.store_path.exists():
            return "existing"
        if (
            config.ledger_path.parent.exists()
            and any(config.ledger_path.parent.iterdir())
        ) or (
            config.store_path.parent.exists()
            and any(config.store_path.parent.iterdir())
        ):
            raise ValueError("missing Store despite existing runtime evidence")
        config.store_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(
            config.store_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
        )
        os.close(fd)
        PortraitStore(config.store_path, config.instance_id, config.source_id)
    return "initialized"


@dataclass(frozen=True)
class ExecutionConfig:
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    package_runtime: object = None
    owner: str = "portrait-runtime"
    lease_seconds: float = 60
    renew_interval: float = 10
    job_seconds: float = 300

    def __post_init__(self):
        import math

        if type(self.owner) is not str or not self.owner.strip():
            raise ValueError("nonempty execution owner required")
        for value in (self.lease_seconds, self.renew_interval, self.job_seconds):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 < value <= 300
            ):
                raise ValueError("finite bounded execution policy required")
        if self.renew_interval >= self.lease_seconds / 2:
            raise ValueError("renew interval must leave lease margin")


def failure(claim, diagnostic, *, failure_class=None):
    fields = (
        "schema_version",
        "instance_id",
        "source_id",
        "version",
        "character_id",
        "skin_id",
        "job_id",
        "render_key",
        "profile",
        "attempt",
        "input_snapshot_digest",
        "tool_fingerprint",
        "source_capture_ref",
    )
    value = {key: claim[key] for key in fields}
    value.update(status="failed", diagnostics=[diagnostic])
    if failure_class is not None:
        value["failure_class"] = failure_class
    return value


def finish_claim(
    store, claim, slot, render, *, clock, lease_seconds, renew_interval, stop_event
):
    """The owner alone runs/reaps work; each renew uses Store's short connection."""
    from .executor import CleanupError, Deferred

    owner, job_id, attempt = claim["owner"], claim["job_id"], claim["attempt"]
    if claim["recovery_required"]:
        result = failure(
            claim, claim["failure_class"], failure_class=claim["failure_class"]
        )
        return (
            "finished"
            if store.finish(job_id, attempt, result, owner=owner, now=clock())
            else "ownership_lost"
        )
    helper = LeaseRenewer(
        lambda: store.renew(job_id, attempt, owner, clock(), lease_seconds),
        interval=renew_interval,
    )
    cancelled = Cancellation(stop_event, helper.lost)
    helper.start()
    try:
        try:
            result = render(cancelled)
        except CleanupError:
            raise
        except Deferred as exc:
            helper.stop()
            if slot.poisoned or slot.process is not None:
                raise CleanupError("unaccounted owned children") from exc
            if helper.lost.is_set():
                return "ownership_lost"
            reason = (
                "stop"
                if stop_event.is_set()
                else ("updater" if "update" in str(exc) else "resource")
            )
            return (
                "deferred"
                if store.defer(job_id, attempt, owner, clock(), reason)
                else "ownership_lost"
            )
        except (ValueError, OSError, RuntimeError) as exc:
            result = failure(claim, type(exc).__name__ + ": " + str(exc)[:512])
        if slot.poisoned or slot.process is not None:
            raise CleanupError("unaccounted owned children")
        if not helper.final_fence():
            return "ownership_lost"
        if stop_event.is_set():
            return (
                "deferred"
                if store.defer(job_id, attempt, owner, clock(), "stop")
                else "ownership_lost"
            )
        return (
            "finished"
            if store.finish(job_id, attempt, result, owner=owner, now=clock())
            else "ownership_lost"
        )
    finally:
        helper.stop()


class PortraitRuntime:
    def __init__(self, config):
        if type(config) is not ExecutionConfig:
            raise ValueError("explicit execution configuration required")
        self.config = config
        self.metadata = MetadataRuntime(config.metadata)
        self.stop_event = threading.Event()
        self._projection_cursor = None
        self._execution_mutex = threading.Lock()
        self._poisoned_slot = None
        self._attempts = 0
        self._plan_cursor = None

    def health(self):
        value = self.metadata.health()
        if not self.config.metadata.enabled:
            return value
        cursor = self._read_checkpoint()
        self.metadata.store_reader.read_events(cursor=cursor, limit=1)
        plans = self.metadata.store_reader.list_plans(limit=16, sealed=False)["plans"]
        value["execution"] = {
            "totals": self.metadata.store_reader.read_status(),
            "event_cursor": cursor,
            "cleanup_unknown": self._poisoned_slot is not None,
            "sampled_pending_plans": [
                self.metadata.store_reader.read_plan_snapshot(p["plan_id"])
                for p in plans
            ],
            "sample_limit": 16,
        }
        if self._poisoned_slot is not None:
            value["status"] = "cleanup_unknown"
        return value

    def tick(self):
        if not self.config.metadata.enabled:
            return {"enabled": False}
        result = self.metadata.tick(now=time.time(), stop_event=self.stop_event)
        if result.get("status") != "idle" and (
            result.get("status") not in ("observed", "prepared", "committed", "repaired", "prepare_retry")
            or not self.metadata.execution_ready()
        ):
            return result
        page = self.metadata.store_reader.list_plans(
            cursor=self._plan_cursor, limit=1, sealed=False
        )
        self._plan_cursor = page["cursor"] if page["plans"] else None
        if not page["plans"]:
            self._project()
            return result
        pid = page["plans"][0]["plan_id"]
        if self.metadata.store_reader.read_manifest(pid)["kind"] != "automatic":
            return result
        return self.execute_tick([pid])

    def _manifest(self, plan_id, manual_request_id=None):
        from .runtime_ledger_reader import RuntimeLedgerReader

        c = self.config.metadata
        manifest = self.metadata.store_reader.read_manifest(plan_id)
        if manifest["kind"] == "manual":
            from .model import _hash, digest

            if (
                not manual_request_id
                or manifest["request_id"] != manual_request_id
                or plan_id
                != digest([c.instance_id, c.source_id, "manual", manual_request_id])
                or manifest.get("delivery_disposition") != "manual_query_only"
                or not _hash(manifest["comparison_evidence"].get("manual_intent"))
            ):
                raise ValueError("explicit manual authorization required")
            return manifest
        origin = manifest["origin_core"]
        ledger = RuntimeLedgerReader(c.ledger_path, c.instance_id, c.source_id)
        mapping = ledger.read_run_mapping(
            origin["identity"], origin["outcome"]["run_id"]
        )
        if mapping["frozen_plan_id"] != plan_id or not self.metadata._proof(
            mapping, ledger=ledger
        ):
            raise ValueError("plan lacks durable runtime registration")
        return manifest

    def _project(self):
        from .publisher import project_accepted

        page = project_accepted(
            self.metadata.store_reader,
            self.config.metadata.portrait_root,
            cursor=self._read_checkpoint(),
        )
        self._write_checkpoint(page["cursor"])
        return len(page["events"])

    def _read_checkpoint(self):
        import json

        from .source_io import _directory, _read_file

        c = self.config.metadata
        try:
            with (
                _directory(c.ledger_path.parent) as directory,
                _read_file(
                    directory, c.ledger_path.name + ".projection.json"
                ) as stream,
            ):
                raw = stream.read(8193)
        except FileNotFoundError:
            return None
        if len(raw) > 8192:
            raise ValueError("projection checkpoint exceeds bound")
        value = json.loads(raw)
        if (
            type(value) is not dict
            or set(value) != {"schema", "cursor"}
            or value["schema"] != 1
        ):
            raise ValueError("invalid projection checkpoint")
        self.metadata.store_reader.read_events(cursor=value["cursor"], limit=1)
        return value["cursor"]

    def _write_checkpoint(self, cursor):
        import json
        import os

        from .source_io import _directory, _partial, _sync_directory

        c = self.config.metadata
        raw = json.dumps({"schema": 1, "cursor": cursor}, sort_keys=True).encode()
        if len(raw) > 8192:
            raise ValueError("projection checkpoint exceeds bound")
        with (
            _directory(c.ledger_path.parent) as directory,
            _partial(directory) as (name, stream),
        ):
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(
                name,
                c.ledger_path.name + ".projection.json",
                src_dir_fd=directory[1],
                dst_dir_fd=directory[1],
            )
            _sync_directory(directory)

    def drain_manual(self, plan_id, request_id, *, max_jobs=None, now=None):
        """Drain only the explicit closed manual selection, with finite progress."""
        if not self.config.metadata.enabled:
            return {"enabled": False}
        if max_jobs is not None and (
            type(max_jobs) is not int or not 1 <= max_jobs <= 10000
        ):
            raise ValueError("max_jobs must be an integer in [1, 10000]")
        manifest = self._manifest(plan_id, request_id)
        if manifest["kind"] != "manual":
            raise ValueError("manual plan required")
        limit = min(
            max_jobs if max_jobs is not None else 10000, max(1, len(manifest["jobs"]))
        )
        del manifest
        now = time.time if now is None else now
        reader = self.metadata.store_reader
        snapshot = reader.read_plan_snapshot(plan_id)
        started = self._attempts
        status = "bound"
        for _ in range(limit):
            if snapshot["sealed"]:
                break
            if self.stop_event.is_set():
                status = "stop"
                break
            before = snapshot
            result = self.execute_tick([plan_id], now=now, manual_request_id=request_id)
            snapshot = reader.read_plan_snapshot(plan_id)
            if result["status"] != "finished":
                status = result["status"]
                break
            if snapshot == before:
                status = "no_progress"
                break
        if snapshot["sealed"]:
            status = "sealed"
        elif self.stop_event.is_set():
            status = "stop"
        remaining = sum(snapshot["counts"].get(k, 0) for k in ("pending", "running"))
        return {
            "enabled": True,
            "plan_id": plan_id,
            "status": status,
            "terminal": snapshot["sealed"],
            "pending": remaining > 0,
            "remaining": remaining,
            "counts": snapshot["counts"],
            "attempted": self._attempts - started,
            "member_bound": limit,
        }

    def execute_tick(self, allowed_plan_ids, *, now=time.time, manual_request_id=None):
        from .executor import CleanupError, Deferred, ResourceSlot
        from .model import _hash
        from .source_io import MetadataRef
        from .store import PortraitStore

        c = self.config.metadata
        if not c.enabled:
            return {"enabled": False}
        if (
            type(allowed_plan_ids) not in (list, tuple)
            or not 1 <= len(allowed_plan_ids) <= 100
            or not all(_hash(p) for p in allowed_plan_ids)
        ):
            raise ValueError("explicit nonempty registered plan selection required")
        if self._poisoned_slot is not None:
            raise CleanupError("prior cleanup unknown; resource lease retained")
        if not self._execution_mutex.acquire(blocking=False):
            return {"enabled": True, "status": "busy"}
        try:
            self.metadata._guards()
            for pid in allowed_plan_ids:
                self._manifest(pid, manual_request_id)
            self._project()
            if self.stop_event.is_set():
                return {"enabled": True, "status": "stop"}
            slot = ResourceSlot(c.resource_policy)
            try:
                with slot:
                    store = PortraitStore(c.store_path, c.instance_id, c.source_id)
                    claim = store.claim(
                        self.config.owner,
                        now(),
                        self.config.lease_seconds,
                        allowed_plan_ids=allowed_plan_ids,
                    )
                    if claim is None:
                        for pid in allowed_plan_ids:
                            store.seal_plan(pid)
                        return {"enabled": True, "status": "idle"}
                    manifest = self._manifest(claim["plan_id"], manual_request_id)
                    ready = MetadataRef(**manifest["core_publication_ref"])
                    del manifest
                    self._attempts += 1
                    status = finish_claim(
                        store,
                        claim,
                        slot,
                        lambda cancel: self._render(slot, claim, ready, cancel),
                        clock=now,
                        lease_seconds=self.config.lease_seconds,
                        renew_interval=self.config.renew_interval,
                        stop_event=self.stop_event,
                    )
                    if status == "finished":
                        self._project()
                        store.seal_plan(claim["plan_id"])
            except CleanupError:
                self._poisoned_slot = slot
                raise
            if self._attempts % 5 == 0:
                self.stop_event.wait(3)
            return {
                "enabled": True,
                "status": status,
                "plan_id": claim["plan_id"],
                "job_id": claim["job_id"],
            }
        except Deferred as exc:
            return {"enabled": True, "status": "deferred", "reason": str(exc)}
        finally:
            self._execution_mutex.release()

    def _render(self, slot, claim, ready, cancel):
        from .package_prepare import prepare_lazy_package
        from .render_prepare import render_candidate
        from .source_io import MetadataRef
        from .source_prepare import PreparedSourceRef, read_prepared_source
        from .source_transfer import fetch_once, miss_transport, replay

        c = self.config.metadata
        if claim["tool_fingerprint"] != c.tool_fingerprint:
            raise ValueError("unknown tool snapshot")
        self.metadata.publication.read_request(ready)
        pin = self.metadata.publication.load_pin(claim["version"])
        raw = dict(claim["source_capture_ref"])
        raw["capture"] = MetadataRef(**raw["capture"])
        prepared = PreparedSourceRef(**raw)
        document = read_prepared_source(prepared, data_root=c.prepared_source_root)
        row = next(
            r
            for r in document["catalog"]["skins"]
            if (r["character_id"], r["skin_id"])
            == (claim["character_id"], claim["skin_id"])
        )
        if row["render_key"] != claim["render_key"]:
            raise ValueError("frozen job/source render key mismatch")
        if row["state"] == "blocked":
            return dict(
                failure(claim, "configured source is blocked"), status="blocked"
            )
        lazy = next(
            r["lazy_input"]
            for r in document["source_snapshot"]["skins"]
            if (r["character_id"], r["skin_id"])
            == (claim["character_id"], claim["skin_id"])
        )
        del document, row
        remaining = min(
            self.config.job_seconds, claim["recovery"]["deadline"] - time.time()
        )
        deadline = time.monotonic() + remaining

        def prepare(same_pin, remaining):
            if same_pin != pin:
                raise ValueError("replay pin changed")
            with prepare_lazy_package(
                slot,
                ready,
                prepared,
                lazy,
                publication=self.metadata.publication,
                data_root=c.prepared_source_root,
                work_root=c.work_root,
                runtime=c.runtime,
                package_runtime=self.config.package_runtime,
                open_remote=miss_transport(),
                stop_event=cancel,
                timeout_seconds=deadline - time.monotonic(),
            ) as package:
                return render_candidate(
                    slot,
                    claim,
                    package,
                    runtime=c.runtime,
                    package_runtime=self.config.package_runtime,
                    data_root=c.portrait_root,
                    work_root=c.work_root,
                    stop_event=cancel,
                    timeout_seconds=min(120, deadline - time.monotonic()),
                )

        return replay(
            pin,
            prepare,
            lambda same_pin, fetch, remaining: fetch_once(
                slot,
                same_pin,
                fetch,
                data_root=c.prepared_source_root,
                work_root=c.work_root,
                runtime=c.runtime,
                tool_fingerprint=c.tool_fingerprint,
                stop_event=cancel,
                timeout_seconds=deadline - time.monotonic(),
            ),
            max_fetches=8,
            max_wire_bytes=192 * 1024**2,
            timeout_seconds=remaining,
            stop_event=cancel,
        )
