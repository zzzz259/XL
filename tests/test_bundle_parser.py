from pathlib import Path

from app.core.bundle_parser import compute_delta, fix_bundle_inplace, needs_fix


def test_compute_delta_returns_added_removed_and_common_count():
    result = compute_delta(["a", "b"], ["b", "c"])

    assert result == {
        "added": ["c"],
        "removed": ["a"],
        "common": 1,
        "old_total": 2,
        "new_total": 2,
    }


def test_fix_bundle_inplace_trims_prefix_before_unityfs(tmp_path: Path):
    bundle = tmp_path / "sample.bundle"
    bundle.write_bytes(b"prefix" + b"UnityFS" + b"payload")

    assert needs_fix(str(bundle)) is True
    assert fix_bundle_inplace(str(bundle)) is True
    assert bundle.read_bytes() == b"UnityFSpayload"
    assert needs_fix(str(bundle)) is False
