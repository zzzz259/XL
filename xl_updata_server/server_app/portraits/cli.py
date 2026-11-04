"""Explicit opt-in local portrait operations; help/disabled mode is stdlib only."""

import argparse
import json
import sys
from pathlib import Path


def read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("configuration exceeds byte bound")
    from .bundle import strict_json

    return strict_json(raw, canonical_required=False)


def load_config(path):
    import os
    import re

    from .bundle import resolve_bundle
    from .execution_runtime import ExecutionConfig
    from .executor import ResourcePolicy
    from .metadata_runtime import MetadataConfig

    value = read_json(path)
    metadata = dict(value.pop("metadata"))
    if "package_runtime" in value or {"runtime", "tool_fingerprint"} & metadata.keys():
        raise ValueError("hand-built runtime configuration is forbidden")
    bundle_config = value.pop("bundle")
    secret_name = value.pop("decrypt_key_env")
    if (
        not isinstance(secret_name, str)
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", secret_name) is None
    ):
        raise ValueError("invalid decrypt secret environment name")
    secret = os.environ.get(secret_name, "")
    if re.fullmatch(r"[0-9a-f]{32}", secret) is None:
        raise ValueError("decrypt secret is missing or invalid")
    bundle = resolve_bundle(bundle_config, decrypt_key_hex=secret)
    metadata["runtime"] = bundle.source_runtime
    metadata["tool_fingerprint"] = bundle.tool_fingerprint
    metadata["enabled"] = True
    for key in (
        "updater_data_root",
        "prepared_source_root",
        "portrait_root",
        "work_root",
        "ledger_path",
        "store_path",
    ):
        metadata[key] = Path(metadata[key])
    policy = metadata["resource_policy"]
    policy["lock_path"] = Path(policy["lock_path"])
    for key in ("updater_status_paths", "disk_roots"):
        policy[key] = tuple(Path(p) for p in policy[key])
    metadata["resource_policy"] = ResourcePolicy(**policy)
    metadata["publication_guards"] = tuple(
        (Path(p), scope) for p, scope in metadata["publication_guards"]
    )
    metadata["legacy_status_paths"] = tuple(
        Path(p) for p in metadata.get("legacy_status_paths", ())
    )
    return ExecutionConfig(
        metadata=MetadataConfig(**metadata),
        package_runtime=bundle.package_runtime,
        **value,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Default-off fenced portrait runtime")
    parser.add_argument(
        "command", choices=("tick", "daemon", "health", "init-store", "manual")
    )
    parser.add_argument(
        "--enable",
        action="store_true",
        help="explicitly authorize configured local runtime",
    )
    parser.add_argument("--config", help="explicit operational JSON configuration")
    parser.add_argument("--request", help="manual pinned request JSON")
    parser.add_argument(
        "--max-jobs",
        type=int,
        help="manual drain bound (1-10000); defaults to selected members",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview existing authenticated metadata; no downloads or writes",
    )
    args = parser.parse_args(argv)
    if not args.enable:
        print(json.dumps({"enabled": False}))
        return 0
    try:
        if args.max_jobs is not None and (
            args.command != "manual" or not 1 <= args.max_jobs <= 10000
        ):
            raise ValueError("--max-jobs requires manual and an integer in [1, 10000]")
        if not args.config:
            raise ValueError("--config required with --enable")
        config = load_config(args.config)
        from .execution_runtime import PortraitRuntime, initialize_store

        if args.command == "init-store":
            output = {"status": initialize_store(config.metadata)}
        else:
            runtime = PortraitRuntime(config)
            if args.command == "health":
                output = runtime.health()
            elif args.command == "manual":
                if not args.request:
                    raise ValueError("--request required")
                import time

                from .execution_manual import manual_request

                request = read_json(args.request)
                output = manual_request(
                    runtime, request, dry_run=args.dry_run, now=time.time()
                )
                if not args.dry_run:
                    output["execution"] = runtime.drain_manual(
                        output["plan_id"], request["request_id"], max_jobs=args.max_jobs
                    )
            elif args.command == "daemon":
                try:
                    while not runtime.stop_event.is_set():
                        runtime.tick()
                        runtime.stop_event.wait(1)
                except KeyboardInterrupt:
                    runtime.stop_event.set()
                output = {"enabled": True, "status": "stop"}
            else:
                output = runtime.tick()
        print(json.dumps(output))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(
            json.dumps({"error": type(exc).__name__, "diagnostic": str(exc)[:512]}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
