from app.features.preview.resource_catalog import (
    discover_preview_resources,
    resolve_character_id,
)
from app.features.preview.spine_adapter import SkinQueryResult


class FakeQueryRunner:
    def __init__(self, result=None):
        self.result = result or SkinQueryResult(skin_names=("base",))
        self.calls = []

    def query_skins(self, skel_path, atlas_path):
        self.calls.append((skel_path, atlas_path))
        return self.result


def test_discovery_keeps_missing_atlas_as_invalid_record(tmp_path):
    skel = tmp_path / "assets" / "art" / "models" / "cardspine" / "cardspine_unknown.skel"
    skel.parent.mkdir(parents=True)
    skel.write_bytes(b"skel")
    runner = FakeQueryRunner()

    catalog = discover_preview_resources(tmp_path, query_runner=runner)

    assert catalog.unmatched[0].status == "invalid"
    assert catalog.unmatched[0].atlas_path.endswith("cardspine_unknown.atlas")
    assert runner.calls == []


def test_discovery_does_not_guess_character_id_for_unknown_stem(tmp_path):
    skel = tmp_path / "misc" / "unknown_model.skel"
    skel.parent.mkdir()
    skel.write_bytes(b"skel")
    (skel.parent / "unknown_model.atlas").write_text("atlas", encoding="utf-8")

    catalog = discover_preview_resources(tmp_path, query_runner=FakeQueryRunner())

    assert len(catalog.unmatched) == 1
    assert catalog.unmatched[0].character_id is None
    assert catalog.unmatched[0].source_skel.endswith("unknown_model.skel")


def test_discovery_uses_query_skins_and_distinguishes_source_fingerprints(tmp_path):
    first = tmp_path / "a" / "cardspine_10080_1.skel"
    second = tmp_path / "b" / "cardspine_10080_2.skel"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first attachment data")
    second.write_bytes(b"second attachment data")
    (first.parent / "cardspine_10080_1.atlas").write_text("atlas-a", encoding="utf-8")
    (second.parent / "cardspine_10080_2.atlas").write_text("atlas-b", encoding="utf-8")
    runner = FakeQueryRunner(SkinQueryResult(skin_names=("base",)))

    catalog = discover_preview_resources(tmp_path, query_runner=runner)

    assert set(catalog.characters) == {"10080"}
    records = catalog.characters["10080"]
    assert [record.skin_name for record in records] == ["base", "base"]
    assert len({record.attachment_fingerprint for record in records}) == 2
    assert len(runner.calls) == 2


def test_discovery_keeps_query_failure_as_invalid_record(tmp_path):
    skel = tmp_path / "cardspine_10080_1.skel"
    skel.write_bytes(b"skel")
    (tmp_path / "cardspine_10080_1.atlas").write_text("atlas", encoding="utf-8")
    result = SkinQueryResult(returncode=17, stderr="atlas parse failed")

    catalog = discover_preview_resources(tmp_path, query_runner=FakeQueryRunner(result))

    assert catalog.characters["10080"][0].status == "invalid"
    assert catalog.characters["10080"][0].skin_name == ""


def test_resolve_character_id_requires_reliable_identity():
    assert resolve_character_id("assets/cardspine_10080_4.skel", {}) == "10080"
    assert resolve_character_id("assets/unknown_model.skel", {}) is None
    assert resolve_character_id("assets/unknown_model.skel", {"character_id": "10080"}) == "10080"
