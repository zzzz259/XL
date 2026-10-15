"""Validation for the repository's forward-only branch promotion flow."""

from __future__ import annotations

import os


LONG_LIVED_BRANCHES = frozenset({"debug", "test", "main"})
PROMOTION_SOURCE = {"test": "debug", "main": "test"}


def is_allowed_pull_request(head_ref: str, base_ref: str) -> bool:
    """Return whether a PR follows task → debug → test → main."""
    if not head_ref or not base_ref:
        return False
    if base_ref == "debug":
        return head_ref not in LONG_LIVED_BRANCHES
    return PROMOTION_SOURCE.get(base_ref) == head_ref


def main() -> int:
    head_ref = os.environ.get("HEAD_REF", "")
    base_ref = os.environ.get("BASE_REF", "")
    if not is_allowed_pull_request(head_ref, base_ref):
        print(
            f"Rejected PR flow {head_ref or '<empty>'} -> {base_ref or '<empty>'}. "
            "Allowed flows: task branch -> debug, debug -> test, test -> main."
        )
        return 1
    print(f"Allowed PR flow: {head_ref} -> {base_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
