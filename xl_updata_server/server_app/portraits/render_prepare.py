"""Explicit candidate render using the caller's live package and ResourceSlot.

The caller owns authentication of PreparedPackageRef and keeps its context open.
This operation never claims, accepts, projects or notifies a job.
Callers should discard large source documents after deriving immutable references;
only idle allocator memory can be returned before starting the native child.
"""

import hashlib
import math
import os
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from .executor import (
    BoundaryError,
    CleanupError,
    Deferred,
    ResourceSlot,
    check_admission,
    verify_resource_boundary,
)
from .model import digest
from .package_prepare import PackageRuntime, _cleanup_attempt
from .render_protocol import (
    JOB_FIELDS,
    bind_package,
    package_wire,
    read_package,
    validate_request,
    validate_result,
)
from .source_prepare import (
    PreparationError,
    _bounded,
    _load,
    _runtime,
    _sha,
    check_stage,
)
from .source_worker import _encoded


def render_candidate(
    slot,
    job,
    prepared_package,
    *,
    runtime,
    package_runtime,
    data_root,
    work_root,
    stop_event,
    timeout_seconds=120,
):
    if type(slot) is not ResourceSlot:
        raise BoundaryError("caller ResourceSlot required")
    slot._require_owner()

    def owned():
        slot._require_owner()
        if slot.fd is None or slot.process is not None or slot.poisoned:
            raise CleanupError("live idle owned render slot required")
        os.fstat(slot.fd)

    owned()
    verify_resource_boundary(slot.policy)
    if any(
        m.split(".")[0] in ("PIL", "UnityPy", "numpy", "PyQt5", "PyQt6", "PySide6")
        for m in sys.modules
    ):
        raise BoundaryError("heavy parent import")
    if (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise PreparationError("finite render deadline required")
    deadline = time.monotonic() + timeout_seconds

    def guard():
        owned()
        if stop_event is not None and stop_event.is_set():
            raise Deferred("stop")
        if time.monotonic() >= deadline:
            raise PreparationError("render shared deadline")
        check_admission(slot.policy, running=True)

    guard()
    job = bind_package(job, prepared_package)
    job = {k: job[k] for k in JOB_FIELDS}
    read_package(prepared_package)
    env = _runtime(runtime, job["tool_fingerprint"])
    if type(package_runtime) is not PackageRuntime:
        raise BoundaryError("trusted package runtime required")
    raw = _bounded(package_runtime.manifest_path, 2 * 1024**2)
    if hashlib.sha256(raw).hexdigest() != package_runtime.manifest_sha256:
        raise BoundaryError("managed descriptor digest mismatch")
    native = _load(raw)
    pinned = dict(runtime.runtime_manifest)
    from app.platform.portrait_dynamic_profile import descriptor_files
    entries = descriptor_files(native)
    if any(pinned.get(e["path"]) != e["sha256"] for e in entries):
        raise BoundaryError("native outside trusted runtime")
    for filename in ("SpineViewerCLI.dll", "PortraitSceneCLI.dll"):
        actual = [
            e["sha256"] for e in native["app_files"] if Path(e["path"]).name == filename
        ]
        expected = [
            sha for name, sha in runtime.tool_manifest if Path(name).name == filename
        ]
        if len(actual) != 1 or actual != expected:
            raise BoundaryError("managed tool identity mismatch")
    for executable in ("/usr/bin/unshare", "/usr/bin/mount", "/usr/bin/sh"):
        if pinned.get(executable) != _sha(executable):
            raise BoundaryError("namespace identity mismatch")
    data_root, work_root = Path(data_root), Path(work_root)
    if not data_root.is_absolute() or not work_root.is_absolute():
        raise BoundaryError("absolute roots required")
    from .source_io import _directory

    with _directory(work_root):
        area = Path(tempfile.mkdtemp(prefix="render-candidate-", dir=work_root))
    try:
        attempt = area / "render"
        attempt.mkdir(mode=0o700)
        policy = asdict(slot.policy)
        policy["lock_path"] = str(policy["lock_path"])
        for key in ("updater_status_paths", "disk_roots"):
            policy[key] = list(map(str, policy[key]))
        request = {
            "schema": "portrait-render/v1",
            "operation_id": digest([area.name, job["job_id"], job["attempt"]]),
            "job": job,
            "package": package_wire(prepared_package),
            "attempt_root": str(attempt),
            "data_root": str(data_root),
            "runtime_manifest": {
                "relative_path": "runtime.json",
                "size": len(raw),
                "sha256": package_runtime.manifest_sha256,
            },
            "caller_uid": os.getuid(),
            "namespace_tools": {
                p: pinned[p] for p in ("/usr/bin/mount", "/usr/bin/sh")
            },
            "python": str(runtime.python),
            "python_sha256": pinned[str(runtime.python)],
            "deadline": deadline,
            "policy": policy,
        }
        validate_request(request)

        def save(path, data):
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())

        save(area / "runtime.json", raw)
        wire = _encoded(request)
        if len(wire) > 65536:
            raise ValueError("render request byte limit")
        save(area / "render-request.json", wire)
        guard()
        _release_idle_heap(slot)
        guard()
        result = slot.run_stage(
            [
                "/usr/bin/unshare",
                "--user",
                "--map-root-user",
                "--mount",
                "--pid",
                "--fork",
                "--mount-proc",
                str(runtime.python),
                "-m",
                "server_app.portraits.render_worker",
                "--request",
                str(area / "render-request.json"),
                "--result",
                str(attempt / "result.json"),
            ],
            cwd=area,
            env=env,
            timeout=min(slot.policy.stage_seconds, deadline - time.monotonic()),
            log_root=area / "logs",
            stop_event=stop_event,
        )
        check_stage(result, stage_name="render", log_root=area / "logs")
        guard()
        candidate = validate_result(
            _load(_bounded(attempt / "result.json", 65536)), request
        )
        guard()
        return candidate
    finally:
        _cleanup_attempt(slot, area)


def _release_idle_heap(slot):
    """Return unused owner heap to libc; never change live objects or limits.

    Return libc's boolean result, not a byte count. This protected Linux path
    requires an already-loaded malloc_trim symbol and fails closed otherwise.
    """
    if type(slot) is not ResourceSlot:
        raise BoundaryError("exact owner slot required for heap release")
    slot._require_owner()
    if slot.fd is None or slot.poisoned or slot.process is not None:
        raise BoundaryError("live idle owner required for heap release")
    try:
        os.fstat(slot.fd)
    except OSError as exc:
        raise BoundaryError("heap release lease unavailable") from exc
    if sys.platform != "linux":
        raise BoundaryError("Linux heap release required")
    import ctypes
    import gc

    try:
        trim = ctypes.CDLL(None).malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
    except (AttributeError, OSError) as exc:
        raise BoundaryError("loaded libc malloc_trim unavailable") from exc
    gc.collect()
    return trim(0)
