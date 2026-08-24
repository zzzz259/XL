from app.features.versions.version_download import calculate_missing_downloads


def test_calculate_missing_downloads_ignores_local_files_and_sorts():
    sub_bundles = [
        ("b", "", "b.bundle"),
        ("a", "", None),
        ("c", "", None),
    ]

    assert calculate_missing_downloads(sub_bundles, {"c", "b", "a"}) == ["a", "c"]
