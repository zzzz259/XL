from app.features.versions.version_data import compute_download_hashes, compute_version_delta_map


def test_compute_download_hashes_uses_nearest_previous_version():
    hashes = {100: ["old"], 200: ["old", "new"], 300: ["new", "latest"]}

    assert compute_download_hashes(300, hashes, hashes) == {"latest"}


def test_compute_version_delta_map_reports_added_removed_and_common():
    hashes = {100: ["a", "b"], 200: ["b", "c"]}

    assert compute_version_delta_map([100, 200], hashes) == {200: (1, 1, 1)}
