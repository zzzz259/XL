from app.platform import database as db
from app.features.versions.version_cleanup import count_downloaded_bundles, delete_downloaded_bundles


def test_delete_downloaded_bundles_removes_files_and_clears_state(tmp_path):
    db.init_db(str(tmp_path / "test.db"))
    db.save_version(100, {}, {"data": []})
    db.save_sub_bundles(100, ["known"])
    bundle = tmp_path / "known.bundle"
    bundle.write_bytes(b"bundle")
    connection = db.get_conn()
    connection.execute(
        "UPDATE sub_bundles SET local_path=?, downloadable=1 WHERE version_timestamp=?",
        (str(bundle), 100),
    )
    connection.commit()
    connection.close()

    sub_bundles = db.get_sub_bundles(100)
    assert count_downloaded_bundles(sub_bundles) == 1
    assert delete_downloaded_bundles(100, sub_bundles) == 1
    assert not bundle.exists()
    assert db.get_sub_bundles(100)[0][2] is None
