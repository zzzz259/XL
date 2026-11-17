"""Explicit, bounded leaderboard reads and a separate historical archive."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from gacha_core.gacha import SUPPORTED_APP_IDS, GachaError, parse_credentials
from gacha_core.leaderboards import collect_mode
from gacha_core.session import GameGatewaySession
from leaderboard_archive import LeaderboardArchive

MODES = ("guild_war", "illusion", "nightmare")
# 2070035 is the Starfall SDK application in the authorized test refresh state.
# Web/gacha callers retain their existing two-application default.
LEADERBOARD_APP_IDS = SUPPORTED_APP_IDS | {"2070035"}
SESSION_ERRORS = frozenset({
    "AUTH_REJECTED", "AUTH_ERROR", "AUTH_NETWORK_ERROR", "INVALID_AUTH_RESPONSE",
    "AUTH_RESPONSE_TOO_LARGE", "SERVER_MAINTENANCE", "NO_ALLOWED_GATEWAY",
    "ENTER_REJECTED", "ACTOR_NOT_FOUND", "SESSION_TIMEOUT", "RESPONSE_TIMEOUT",
    "GATEWAY_NETWORK_ERROR", "INVALID_FRAME", "RESPONSE_SIZE_LIMIT", "RESPONSE_LIMIT",
    "RESPONSE_MISMATCH",
})


def _failed(scope, mode, code):
    return {"scope": scope, "mode": mode, "season_id": None,
            "entity_kind": "guild" if mode == "guild_war" else "player",
            "availability": "UNKNOWN", "coverage": "PARTIAL",
            "settle_evidence": "LAST_OBSERVED", "collection_error": code,
            "collected_at_utc": datetime.now(UTC).isoformat(),
            "entries": [], "member_entries": []}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("dry-run", help="offline protocol and budget description; no login")
    for cmd in ("metadata", "smoke", "collect"):
        q = sub.add_parser(cmd)
        q.add_argument("--database", required=True, type=Path)
        q.add_argument("--scope", required=True, help="operator-verified server/auth context label")
        q.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
        q.add_argument("--run-id", default=None)
        q.add_argument("--code-version", default="development")
        q.add_argument("--max-pages", type=int, default=20)
        q.add_argument("--max-entries", type=int, default=200)
    q = sub.add_parser("list-seasons")
    q.add_argument("--database", required=True, type=Path)
    q = sub.add_parser("export")
    q.add_argument("--database", required=True, type=Path)
    q.add_argument("--snapshot-id", required=True, type=int)
    q.add_argument("--output", required=True, type=Path)
    return p


def _summary(result):
    return {key: result.get(key) for key in (
        "mode", "season_id", "availability", "coverage", "settle_evidence",
        "collection_error", "pages_fetched", "source_rpc",
    )} | {"entries_count": len(result.get("entries", [])),
         "member_entries_count": len(result.get("member_entries", []))}


def run(argv=None, *, input_stream=None, output=None):
    args = parser().parse_args(argv)
    stream = sys.stdin if input_stream is None else input_stream
    output = sys.stdout if output is None else output

    def emit(value):
        print(json.dumps(value, ensure_ascii=True), file=output, flush=True)

    try:
        if args.command == "dry-run":
            emit({"network": False, "evidence": "STATIC_CONFIRMED", "modes": list(MODES),
                  "smoke": {"max_requests": 7, "max_duration": 60, "request_delay": 2},
                  "collect": {"max_requests": 64, "max_duration": 180,
                              "max_pages": 20, "max_entries": 200},
                  "final_ranking_verified": False})
            return 0
        if args.command == "list-seasons":
            emit({"seasons": LeaderboardArchive(args.database).list_seasons()})
            return 0
        if args.command == "export":
            payload = LeaderboardArchive(args.database).export_snapshot(args.snapshot_id)
            # Refuse accidental replacement of a previous historical export.
            with args.output.open("x", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            emit({"result": "EXPORTED", "snapshot_id": args.snapshot_id})
            return 0
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", args.scope):
            raise GachaError("INVALID_SCOPE")
        if not 1 <= args.max_pages <= 20 or not 1 <= args.max_entries <= 200:
            raise GachaError("INVALID_BUDGET")
        modes = list(dict.fromkeys(args.modes))
        smoke = args.command in ("metadata", "smoke")
        run_id = args.run_id or uuid.uuid4().hex
        archive = LeaderboardArchive(args.database)
        raw = getpass.getpass("Credential (hidden): ") if stream.isatty() else stream.readline(65538)
        credentials = parse_credentials(raw, supported_app_ids=LEADERBOARD_APP_IDS)
        del raw
        results = []
        fatal = False
        pages, entries = args.max_pages, args.max_entries
        gateway = GameGatewaySession(credentials, max_requests=7 if smoke else 64,
                                     max_duration=60 if smoke else 180, request_delay=2)
        session = gateway
        try:
            with gateway as session:
                for mode in modes:
                    if pages < 1 or entries < 1:
                        fatal = True
                        results.append(_failed(args.scope, mode, "BUDGET_EXHAUSTED"))
                        break
                    result = collect_mode(session, mode, scope=args.scope,
                                          first_page_only=smoke,
                                          metadata_only=args.command == "metadata",
                                          max_pages=pages, max_entries=entries)
                    results.append(result)
                    pages -= result.get("pages_fetched", 0)
                    entries -= len(result.get("entries", [])) + len(result.get("member_entries", []))
                    if result.get("collection_error"):
                        fatal = True
                        break
        except Exception as error:  # noqa: BLE001 - CLI privacy boundary
            code = error.code if isinstance(error, GachaError) and error.code in SESSION_ERRORS else "COLLECTION_FAILED"
            results.append(_failed(args.scope, modes[min(len(results), len(modes)-1)], code))
            fatal = True
        finally:
            del credentials
        saved = archive.save_run(run_id, results, code_version=args.code_version)
        emit({"result": "FAILED" if fatal else "ARCHIVED", "run_id": run_id,
              "requests_used": session.requests_used,
              "results": [_summary(x) for x in results],
              "archive": {k: v for k, v in saved.items()
                          if k in ("run_id", "snapshot_ids", "attempts")}})
        if fatal:
            return 2
        return 3 if any(x.get("coverage") in ("PARTIAL", "INCONSISTENT") for x in results) else 0
    except GachaError as error:
        # GachaError codes originate in our allowlisted protocol implementation.
        emit({"result": "FAILED", "code": error.code})
        return 2
    except Exception:  # noqa: BLE001 - CLI privacy boundary
        emit({"result": "FAILED", "code": "COLLECT_OR_ARCHIVE_ERROR"})
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
