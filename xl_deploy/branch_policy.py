"""Validation for the repository's forward-only branch promotion flow."""

LONG_LIVED_BRANCHES = frozenset({"debug", "test", "main"})
PROMOTION_SOURCE = {"test": "debug", "main": "test"}


def is_allowed_pull_request(head_ref: str, base_ref: str) -> bool:
    """Return whether a PR follows task → debug → test → main."""
    if not head_ref or not base_ref:
        return False
    if base_ref == "debug":
        return head_ref not in LONG_LIVED_BRANCHES
    return PROMOTION_SOURCE.get(base_ref) == head_ref
