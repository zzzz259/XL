from app.ui.workers.import_as import ImportASWorker


def test_import_as_counts_unrepairable_bundle_as_failure(tmp_path):
    valid_bundle = tmp_path / "valid.bundle"
    invalid_bundle = tmp_path / "invalid.bundle"
    valid_bundle.write_bytes(b"UnityFS" + b"\x00" * 16)
    invalid_bundle.write_bytes(b"not a bundle")

    worker = ImportASWorker(
        [str(valid_bundle), str(invalid_bundle)],
        str(tmp_path),
        str(tmp_path / "material"),
        str(tmp_path / "AssetStudio.CLI.exe"),
    )

    success, fail = worker._stage_fix()

    assert (success, fail) == (1, 1)
