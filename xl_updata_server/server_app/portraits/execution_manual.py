"""Explicit pinned historical requests; no automatic current promotion or delivery."""

import copy


def manual_request(runtime, intent, *, dry_run=False, now):
    from . import publisher
    from .model import _text, digest
    from .source_io import MetadataRef
    from .source_prepare import PreparedSourceRef, read_prepared_source
    from .store import PortraitStore

    c = runtime.config.metadata
    if not c.enabled:
        return {"enabled": False}
    first = runtime.metadata.store_reader.list_plans(limit=1)["plans"]
    if not first or not first[0]["sealed"] or first[0]["expected_count"] != 0:
        raise ValueError("automatic baseline required before manual history")
    baseline = runtime._manifest(first[0]["plan_id"])
    if not baseline["comparison_evidence"]["baseline"]:
        raise ValueError("automatic baseline required before manual history")
    if type(intent) is not dict or set(intent) != {
        "request_id",
        "version",
        "characters",
        "skins",
        "prepared_ref",
        "request_ref",
    }:
        raise ValueError("explicit manual request fields required")
    if not _text(intent["request_id"]) or not _text(intent["version"]):
        raise ValueError("explicit request/version required")
    for field in ("characters", "skins"):
        selector = intent[field]
        if selector != "all" and (
            type(selector) is not list
            or not selector
            or len(set(selector)) != len(selector)
            or not all(_text(x) for x in selector)
        ):
            raise ValueError("explicit selector or all required")
    runtime.metadata._guards()
    ready = MetadataRef(**intent["request_ref"])
    runtime.metadata.publication.read_request(ready)
    raw = copy.deepcopy(intent["prepared_ref"])
    raw["capture"] = MetadataRef(**raw["capture"])
    prepared = PreparedSourceRef(**raw)
    if (
        prepared.instance_id,
        prepared.source_id,
        prepared.version,
        prepared.pin_digest,
        prepared.tool_fingerprint,
    ) != (
        c.instance_id,
        c.source_id,
        intent["version"],
        ready.pin_digest,
        c.tool_fingerprint,
    ):
        raise ValueError("unknown tool snapshot or manual source identity mismatch")
    desired = read_prepared_source(prepared, data_root=c.prepared_source_root)[
        "catalog"
    ]
    rows = [
        r
        for r in desired["skins"]
        if (intent["characters"] == "all" or r["character_id"] in intent["characters"])
        and (intent["skins"] == "all" or r["skin_id"] in intent["skins"])
    ]
    for field, key in (("characters", "character_id"), ("skins", "skin_id")):
        if intent[field] != "all" and set(intent[field]) != {r[key] for r in rows}:
            raise ValueError("manual selection not configured")
    pid = digest([c.instance_id, c.source_id, "manual", intent["request_id"]])
    signature = digest(intent)
    summary = {
        "enabled": True,
        "plan_id": pid,
        "expected_jobs": len(rows),
        "selection": [
            {k: r[k] for k in ("character_id", "skin_id", "ordinal")} for r in rows
        ],
    }
    if dry_run:
        return summary
    reader = runtime.metadata.store_reader
    try:
        existing = reader.read_manifest(pid)
    except KeyError:
        existing = None
    if (
        existing is not None
        and existing["comparison_evidence"].get("manual_intent") != signature
    ):
        raise ValueError("manual request conflict")
    jobs = []
    for row in rows:
        job = {k: desired[k] for k in ("instance_id", "source_id", "version")}
        job.update(
            {
                k: copy.deepcopy(row[k])
                for k in ("character_id", "skin_id", "render_key", "profile")
            }
        )
        job.update(
            schema_version=2,
            reason="manual_backfill",
            input_snapshot_digest=prepared.snapshot_digest,
            tool_fingerprint=prepared.tool_fingerprint,
            source_capture_ref=intent["prepared_ref"],
        )
        job["job_id"] = digest(
            [
                job[k]
                for k in (
                    "instance_id",
                    "source_id",
                    "version",
                    "character_id",
                    "skin_id",
                    "render_key",
                )
            ]
        )
        try:
            job = reader.read_job(job["job_id"])
        except KeyError:
            pass
        jobs.append(job)
    with publisher.locked_mutation(c.portrait_root) as locked:
        current = publisher.catalog_token(c.portrait_root)
        version = publisher.version_catalog_token(c.portrait_root, prepared.version)
        staged = publisher.stage_desired_snapshot(
            desired, c.portrait_root, execution_plan_id=pid
        )
        manifest = existing or {
            "schema_version": 2,
            "instance_id": c.instance_id,
            "source_id": c.source_id,
            "version": prepared.version,
            "kind": "manual",
            "request_id": intent["request_id"],
            "input_snapshot_digest": prepared.snapshot_digest,
            "tool_fingerprint": prepared.tool_fingerprint,
            "source_capture_ref": intent["prepared_ref"],
            "core_publication_ref": intent["request_ref"],
            "desired_catalog_ref": staged["desired_catalog_ref"],
            "comparison_evidence": {"baseline": False, "manual_intent": signature},
            "recovery": {
                "max_crashes": 2,
                "deadline": now + c.plan_window_seconds,
                "backoff_seconds": 1,
            },
            "jobs": jobs,
            "expected_jobset_digest": digest(jobs),
            "delivery_disposition": "manual_query_only",
        }
        store = PortraitStore(c.store_path, c.instance_id, c.source_id)
        store.register_closed_plan(manifest)
        locked.install_manual_desired(
            desired,
            execution_plan_id=pid,
            expected_current=current,
            expected_version=version,
        )
        if not jobs:
            store.seal_plan(pid)
    return summary
