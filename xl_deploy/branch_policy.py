"""Validation for PRs in the repository's fast-forward branch flow."""

LONG_LIVED_BRANCHES = frozenset({"debug", "test", "main"})


def is_allowed_pull_request(head_ref: str, base_ref: str) -> bool:
    """Allow authored work into debug; stage promotions must fast-forward."""
    if not head_ref or not base_ref:
        return False
    return base_ref == "debug" and head_ref not in LONG_LIVED_BRANCHES
