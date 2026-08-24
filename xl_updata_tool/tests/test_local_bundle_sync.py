from app.platform import database as db
from app.features.versions.local_bundle_sync import sync_local_bundles


def test_sync_local_bundles_uses_disk_as_source_of_truth(tmp_path):
    db.init_db(str(tmp_path / "test.db"))
    db.save_version(
        100,
        {},
        {"data": [{"name": "catalog", "hash": "abc", "size": 10, "ver": 1}]},
    )
    db.save_sub_bundles(100, ["abc"])
    bundle_dir = tmp_path / "bundles" / "100"
    bundle_dir.mkdir(parents=True)
    bundle_path = bundle_dir / "abc.bundle"
    bundle_path.write_bytes(b"bundle")

    changed = sync_local_bundles(str(tmp_path / "bundles"), 100)

    assert changed == 1
    assert db.get_sub_bundles(100)[0][2] == str(bundle_path)


def test_sync_local_bundles_clears_missing_file_state(tmp_path):
    db.init_db(str(tmp_path / "test.db"))
    db.save_version(
        200,
        {},
        {"data": [{"name": "catalog", "hash": "missing", "size": 10, "ver": 1}]},
    )
    db.save_sub_bundles(200, ["missing"])
    connection = db.get_conn()
    connection.execute(
        "UPDATE sub_bundles SET local_path=?, downloadable=1 WHERE version_timestamp=?",
        (str(tmp_path / "missing.bundle"), 200),
    )
    connection.commit()
    connection.close()
    (tmp_path / "bundles" / "200").mkdir(parents=True)

    changed = sync_local_bundles(str(tmp_path / "bundles"), 200)

    assert changed == 1
    assert db.get_sub_bundles(200)[0][2] is None
