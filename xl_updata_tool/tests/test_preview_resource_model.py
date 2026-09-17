from app.features.preview.resource_model import PreviewResourceCatalog, SpineSkinRecord, skin_key


def test_skin_key_distinguishes_same_name_with_different_attachment_fingerprints():
    first = SpineSkinRecord("10080", "a.skel", "a.atlas", "default", "hash-a", "默认", "ready")
    second = SpineSkinRecord("10080", "a.skel", "a.atlas", "default", "hash-b", "默认", "ready")

    assert skin_key(first) != skin_key(second)


def test_catalog_can_hold_unmatched_record_without_assigning_character():
    catalog = PreviewResourceCatalog.from_records(
        [SpineSkinRecord(None, "x.skel", "", "skin", "h", "skin", "unmatched")]
    )

    assert catalog.unmatched[0].character_id is None
    assert catalog.characters == {}


def test_skin_key_uses_identity_fallback_when_attachment_fingerprint_is_unavailable():
    first = SpineSkinRecord(
        "10080", "a.skel", "a.atlas", "default", "", "默认", "ready",
        identity_fingerprint="identity-a", fingerprint_kind="source_skin_identity",
    )
    second = SpineSkinRecord(
        "10080", "a.skel", "a.atlas", "default", "", "默认", "ready",
        identity_fingerprint="identity-b", fingerprint_kind="source_skin_identity",
    )

    assert skin_key(first) != skin_key(second)
