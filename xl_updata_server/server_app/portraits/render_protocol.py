"""Bound stdlib protocol for candidate-only rendering."""

import math
from dataclasses import fields
from pathlib import Path

from .executor import ResourcePolicy
from .model import digest, validate_job
from .package_prepare import PreparedPackageRef
from .package_protocol import read_bound_file
from .source_worker import _encoded, _hex, _keys, _path

JOB_FIELDS = {
    "schema_version",
    "instance_id",
    "source_id",
    "version",
    "character_id",
    "skin_id",
    "render_key",
    "job_id",
    "profile",
    "reason",
    "attempt",
    "input_snapshot_digest",
    "tool_fingerprint",
    "source_capture_ref",
}


def bind_package(job, package):
    job = validate_job(job)
    if job["schema_version"] != 2 or "attempt" not in job:
        raise ValueError("explicit V2 attempt required")
    if type(package) is not PreparedPackageRef:
        raise TypeError("PreparedPackageRef required from live context")
    ref = job["source_capture_ref"]
    expected = dict(
        schema="portrait-prepared-package/v1",
        scope_key=digest([job["instance_id"], job["source_id"]]),
        pin_digest=ref["pin_digest"],
        version=job["version"],
        capture_sha256=ref["capture"]["sha256"],
        snapshot_digest=job["input_snapshot_digest"],
        tool_fingerprint=job["tool_fingerprint"],
        **{k: job[k] for k in ("character_id", "skin_id", "render_key")},
    )
    if any(getattr(package, k) != v for k, v in expected.items()):
        raise ValueError("package execution binding mismatch")
    return job


def package_wire(package):
    from dataclasses import asdict

    result = asdict(package)
    for k in ("package_path", "input_root"):
        result[k] = str(result[k])
    result["stage_evidence"] = list(result["stage_evidence"])
    return result


def package_from_wire(value):
    _keys(value, (f.name for f in fields(PreparedPackageRef)))
    value = dict(value)
    for k in ("package_path", "input_root"):
        value[k] = _path(value[k])
    if (
        not value["package_path"].is_relative_to(value["input_root"])
        or value["package_path"] == value["input_root"]
    ):
        raise ValueError("package outside input root")
    value["stage_evidence"] = tuple(value["stage_evidence"])
    return PreparedPackageRef(**value)


def read_package(package):
    return read_bound_file(
        package.input_root,
        {
            "relative_path": package.package_path.relative_to(
                package.input_root
            ).as_posix(),
            "size": package.manifest_size,
            "sha256": package.manifest_sha256,
        },
        cap=2 * 1024**2,
    )


def validate_request(value):
    _keys(
        value,
        {
            "schema",
            "operation_id",
            "job",
            "package",
            "attempt_root",
            "data_root",
            "runtime_manifest",
            "caller_uid",
            "namespace_tools",
            "python",
            "python_sha256",
            "deadline",
            "policy",
        },
    )
    if value["schema"] != "portrait-render/v1":
        raise ValueError("invalid render schema")
    _hex(value["operation_id"], 64)
    _keys(value["job"], JOB_FIELDS)
    package = package_from_wire(value["package"])
    job = bind_package(value["job"], package)
    attempt = _path(value["attempt_root"])
    data = _path(value["data_root"])
    roots = tuple(p.resolve() for p in (attempt, data, package.input_root))
    if any(
        a == b or a in b.parents or b in a.parents
        for i, a in enumerate(roots)
        for b in roots[i + 1 :]
    ):
        raise ValueError("render roots overlap")
    if type(value["deadline"]) not in (int, float) or not math.isfinite(
        value["deadline"]
    ):
        raise ValueError("invalid deadline")
    if type(value["caller_uid"]) is not int or value["caller_uid"] <= 0:
        raise ValueError("unprivileged caller required")
    _path(value["python"])
    _hex(value["python_sha256"], 64)
    _keys(value["namespace_tools"], {"/usr/bin/mount", "/usr/bin/sh"})
    for sha in value["namespace_tools"].values():
        _hex(sha, 64)
    raw = value["policy"]
    _keys(raw, (f.name for f in fields(ResourcePolicy)))
    raw = dict(raw)
    raw["lock_path"] = _path(raw["lock_path"])
    for k in ("updater_status_paths", "disk_roots"):
        if type(raw[k]) is not list or not 0 < len(raw[k]) <= 16:
            raise ValueError("invalid guard roots")
        raw[k] = tuple(_path(p) for p in raw[k])
    policy = ResourcePolicy(**raw)
    if (
        policy.admission_bytes < 640 * 1024**2
        or policy.abort_bytes < 128 * 1024**2
        or policy.disk_free_bytes < 1024**3
        or policy.poll_seconds > 0.1
    ):
        raise ValueError("weakened render policy")
    return job, package, attempt, data, policy


def validate_result(value, request):
    _keys(
        value,
        {
            "schema",
            "operation_id",
            "job_id",
            "attempt",
            "pin_digest",
            "capture_sha256",
            "tool_fingerprint",
            "package_sha256",
            "candidate",
        },
    )
    job = request["job"]
    p = request["package"]
    expected = {
        "schema": "portrait-render-result/v1",
        "operation_id": request["operation_id"],
        "job_id": job["job_id"],
        "attempt": job["attempt"],
        "pin_digest": p["pin_digest"],
        "capture_sha256": p["capture_sha256"],
        "tool_fingerprint": p["tool_fingerprint"],
        "package_sha256": p["manifest_sha256"],
    }
    if any(
        type(value[k]) is not type(v) or _encoded(value[k]) != _encoded(v)
        for k, v in expected.items()
    ):
        raise ValueError("render result binding mismatch")
    candidate = value["candidate"]
    from .publisher import _validate_media

    extra = {
        "status",
        "source_version",
        "archive_path",
        "archive_sha256",
        "archive_profile",
        "archive_width",
        "archive_height",
        "artifact_path",
        "artifact_sha256",
        "width",
        "height",
        "delivery_profile",
        "diagnostics",
        "artifact_render_key",
        "artifact_tool_fingerprint",
    }
    _keys(candidate, JOB_FIELDS | extra)
    validate_job(candidate)
    if (
        any(_encoded(candidate[k]) != _encoded(job[k]) for k in JOB_FIELDS)
        or candidate["status"] != "success"
    ):
        raise ValueError("candidate identity mismatch")
    if (
        candidate["artifact_render_key"] != job["render_key"]
        or candidate["artifact_tool_fingerprint"] != job["tool_fingerprint"]
    ):
        raise ValueError("candidate provenance mismatch")
    _validate_media(candidate)
    validate_candidate_semantics(candidate)
    if candidate["width"] * candidate["height"] > 8_388_608:
        raise ValueError("delivery pixel budget")
    root = Path(request["data_root"]).resolve()
    for key, sha, cap in (
        ("artifact_path", "artifact_sha256", 20 * 1024**2),
        ("archive_path", "archive_sha256", 512 * 1024**2),
    ):
        path = root / candidate[key]
        if not path.resolve().is_relative_to(root):
            raise ValueError("candidate media path mismatch")
        dims = (
            (candidate["width"], candidate["height"])
            if key == "artifact_path"
            else (candidate["archive_width"], candidate["archive_height"])
        )
        verify_media_header(path, candidate[sha], dims, cap)
    return candidate


def verify_media_header(path, sha, dimensions, cap):
    """Verify bounded encoded bytes and PNG IHDR; never decode in the owner."""
    import struct

    from .publisher import _hash_file

    if _hash_file(path, cap) != sha:
        raise ValueError("candidate media digest mismatch")
    with path.open("rb") as stream:
        header = stream.read(29)
    if (
        len(header) != 29
        or header[:16] != b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"
        or struct.unpack(">II", header[16:24]) != dimensions
    ):
        raise ValueError("candidate PNG header mismatch")


def validate_candidate_semantics(candidate):
    diagnostics = candidate["diagnostics"]
    if (
        type(diagnostics) is not list
        or len(diagnostics) > 32
        or any(type(d) is not str or len(d) > 4096 for d in diagnostics)
    ):
        raise ValueError("invalid candidate diagnostics")
    if candidate["archive_width"] * candidate["archive_height"] > 64 * 1024**2:
        raise ValueError("archive pixel limit")
    if candidate["delivery_profile"] == "original" and (
        candidate["width"] != candidate["archive_width"]
        or candidate["height"] != candidate["archive_height"]
        or candidate["artifact_sha256"] != candidate["archive_sha256"]
    ):
        raise ValueError("original delivery identity mismatch")
