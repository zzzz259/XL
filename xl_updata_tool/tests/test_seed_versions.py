from pathlib import Path

from app.platform import database as db
from app.features.versions import seed_versions


def test_repair_missing_sub_bundles_uses_local_current_catalogs(monkeypatch, tmp_path):
    db.init_db(str(tmp_path / "versions.db"))
    timestamp = 123
    versions_data = {
        "timestamp": timestamp,
        "data": [
            {"name": "Arts", "hash": "a" * 32, "size": 10, "ver": 1},
            {"name": "Data", "hash": "b" * 32, "size": 20, "ver": 2},
        ],
    }
    db.save_version(timestamp, {"latestVersion": "1.0", "file": "versions.json"}, versions_data)
    current_dir = tmp_path / "bundles" / "current"
    current_dir.mkdir(parents=True)
    arts_path = current_dir / f"arts_{'a' * 32}.json"
    data_path = current_dir / f"data_{'b' * 32}.json"
    arts_path.write_bytes(b"arts")
    data_path.write_bytes(b"data")

    seen = []

    def fake_extract(path):
        seen.append(path)
        return {f"sub-{Path(path).stem.split('_', 1)[0]}"}

    monkeypatch.setattr(seed_versions, "extract_manifest_hashes", fake_extract)

    repaired = seed_versions.repair_missing_sub_bundles(str(tmp_path / "bundles"))

    assert repaired == {timestamp: 2}
    assert db.get_sub_bundles(timestamp) == [
        ("sub-arts", 1, None),
        ("sub-data", 1, None),
    ]
    assert seen == [str(arts_path), str(data_path)]
