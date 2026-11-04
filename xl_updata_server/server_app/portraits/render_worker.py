"""Guarded native renderer; immutable candidate production only."""

import os
import sys
import time
from pathlib import Path

from .executor import BoundaryError, Deferred
from .package_protocol import StopLatch, read_bound_file
from .render_protocol import read_package, validate_request, validate_result
from .source_prepare import PreparationError, _load
from .source_worker import _directory, _encoded, _guard, _load_request, _path, _publish


def run_request(value, *, request_path, result_path):
    job, package, attempt, data, policy = validate_request(value)
    if (
        _path(str(request_path)) != attempt.parent / "render-request.json"
        or _path(str(result_path)) != attempt / "result.json"
    ):
        raise ValueError("fixed render paths required")

    def guard():
        _guard(policy)
        if time.monotonic() >= value["deadline"]:
            raise PreparationError("render shared deadline")

    guard()
    if Path("/proc/self/uid_map").read_text().split() != [
        "0",
        str(value["caller_uid"]),
        "1",
    ]:
        raise BoundaryError("private user namespace required")
    with _directory(attempt, private=True) as directory:
        if os.listdir(directory):
            raise ValueError("empty private render attempt required")
        read_package(package)
        native = _load(
            read_bound_file(attempt.parent, value["runtime_manifest"], cap=2 * 1024**2)
        )
        from .private_namespace import prepare_namespace

        prepare_namespace(value, native, attempt, guard, value["deadline"])
        from app.platform.portrait_dynamic_profile import guarded_runtime

        runtime = guarded_runtime(native)
        del native
        from app.features.preview.portrait_package import load_portrait_package
        from app.platform.portrait_linux_runtime import (
            PrivateDisplay,
        )

        loaded = load_portrait_package(package.package_path)
        if (
            loaded.scene.version_timestamp,
            loaded.scene.character_id,
            loaded.scene.skin_id,
        ) != (job["version"], job["character_id"], job["skin_id"]):
            raise ValueError("loaded scene execution mismatch")
        del loaded
        for name in ("managed", "home", "cache", "output", "work"):
            (attempt / name).mkdir(mode=0o700)
        stop = StopLatch(guard)
        try:
            launch = runtime.prepare(
                attempt / "managed", deadline=value["deadline"], cancel_check=stop
            )
        finally:
            stop.raise_if_stopped()
        launch.env.update(
            HOME=str(attempt / "home"), XDG_CACHE_HOME=str(attempt / "cache")
        )
        from app.features.preview.portrait_headless import run_request as render

        request = dict(
            schema_version=1,
            **{
                k: job[k]
                for k in ("job_id", "version", "character_id", "skin_id", "profile")
            },
            input_root=str(package.input_root),
            package_path=str(package.package_path),
            output_root=str(attempt / "output"),
            work_root=str(attempt / "work"),
            png_working_rows=128,
        )
        try:
            with PrivateDisplay(launch):
                raw = render(request, launch=launch)
                stop.raise_if_stopped()
        finally:
            stop.raise_if_stopped()
        guard()
        if launch.display is not None:
            raise BoundaryError("display not quiescent")
        if (
            raw.get("schema_version"),
            raw.get("status"),
            raw.get("job_id"),
            raw.get("png_working_rows"),
            raw.get("resource_mode"),
        ) != (1, "success", job["job_id"], 128, "disk_rows"):
            raise ValueError(
                "native full render failed: " + str(raw.get("diagnostics"))
            )
        if raw.get("staged_path") != str(attempt / "output" / (job["job_id"] + ".png")):
            raise ValueError("unexpected staged output")
        # The trusted wrapper supplies V2 claims only after bound package execution.
        staged = {**raw, **job}
        from .publisher import publish_png

        candidate = publish_png(job, staged, data, staging_root=attempt / "output")
        guard()
        result = {
            "schema": "portrait-render-result/v1",
            "operation_id": value["operation_id"],
            "job_id": job["job_id"],
            "attempt": job["attempt"],
            "pin_digest": package.pin_digest,
            "capture_sha256": package.capture_sha256,
            "tool_fingerprint": package.tool_fingerprint,
            "package_sha256": package.manifest_sha256,
            "candidate": candidate,
        }
        validate_result(result, value)
        _publish(directory, "result.json", _encoded(result), 65536)
        return result


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        if len(args) != 4 or args[0] != "--request" or args[2] != "--result":
            raise ValueError("fixed request/result required")
        request, result = _path(args[1]), _path(args[3])
        run_request(_load_request(request), request_path=request, result_path=result)
        return 0
    except Exception as exc:  # noqa: BLE001 - fixed worker exit taxonomy
        print(
            "portrait render worker refused: " + type(exc).__name__ + ": " + str(exc),
            file=sys.stderr,
        )
        return (
            2
            if isinstance(exc, Deferred)
            else 3
            if isinstance(exc, BoundaryError)
            else 1
        )


if __name__ == "__main__":
    raise SystemExit(main())
