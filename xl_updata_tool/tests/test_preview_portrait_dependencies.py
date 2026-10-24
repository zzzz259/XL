from app.features.preview.portrait_config.asset_index import (
    AssetIndex,
    IndexedAsset,
    canonical_bundle_hash,
)
from app.features.preview.portrait_config.dependency_resolver import resolve_scene_assets
from app.features.preview.portrait_config.model import AssetRef
from app.features.preview.portrait_config.unity_scene import (
    UnityExternalFile,
    UnityObjectPointer,
    UnitySceneParseResult,
)

HASH_A = "a" * 32
HASH_B = "b" * 32


def test_canonical_bundle_hash_requires_a_single_exact_cab_hash():
    assert canonical_bundle_hash(f"archive:/CAB-{HASH_A}/CAB-{HASH_A}") == HASH_A
    assert canonical_bundle_hash(f"CAB-{HASH_A.upper()}") == HASH_A
    assert canonical_bundle_hash(f"archive:/CAB-{HASH_A}/CAB-{HASH_B}") is None
    assert canonical_bundle_hash("archive:/some-directory/asset.prefab") is None


def test_conflicting_external_name_and_path_are_ambiguous_not_path_preferred():
    pointer = UnityObjectPointer(
        AssetRef("local", 1), "m_Target", 1, 20,
        f"CAB-{HASH_A}", f"archive:/CAB-{HASH_B}/CAB-{HASH_B}",
    )
    scene = _scene(pointer, externals=(
        UnityExternalFile(1, f"CAB-{HASH_A}", f"archive:/CAB-{HASH_B}/CAB-{HASH_B}"),
    ))

    result = resolve_scene_assets(
        scene, AssetIndex(402, (_asset(HASH_A, 20), _asset(HASH_B, 20))),
    )

    assert result.complete is False
    assert result.references[0].status == "ambiguous_mapping"


def _asset(bundle_hash: str, path_id: int, *, dependencies=(), present=True) -> IndexedAsset:
    from pathlib import Path

    return IndexedAsset(
        name=f"asset-{path_id}", container="", bundle_hash=bundle_hash,
        path_id=path_id, dependencies=tuple(dependencies), version_timestamp=402,
        bundle_path=Path(bundle_hash), bundle_present=present,
        evidence=f"catalog#/{bundle_hash}/{path_id}",
    )


def _scene(*pointers, externals=()):
    return UnitySceneParseResult(
        "local", (), (), (), (), (), tuple(pointers), tuple(externals), 402,
    )


def test_external_file_id_resolves_by_exact_hash_and_path_id_not_name():
    index = AssetIndex(402, (
        _asset(HASH_A, 20),
        _asset(HASH_B, 20),
    ))
    pointer = UnityObjectPointer(
        AssetRef("local", 1), "m_SkeletonDataAsset", 1, 20, f"CAB-{HASH_A}",
        f"archive:/CAB-{HASH_A}/CAB-{HASH_A}",
    )
    scene = _scene(
        pointer,
        externals=(UnityExternalFile(1, f"CAB-{HASH_A}", f"archive:/CAB-{HASH_A}/CAB-{HASH_A}"),),
    )

    result = resolve_scene_assets(scene, index)

    assert result.complete is True
    assert result.references[0].target == AssetRef(HASH_A, 20)
    assert result.references[0].status == "resolved"


def test_invalid_external_file_id_is_explicit_and_incomplete():
    index = AssetIndex(402, (_asset(HASH_A, 20),))
    pointer = UnityObjectPointer(AssetRef("local", 1), "m_Mesh", 2, 20)

    result = resolve_scene_assets(_scene(pointer), index)

    assert result.complete is False
    assert result.references[0].status == "external_file_id_invalid"


def test_local_pointer_resolves_in_own_bundle_and_wrong_version_is_rejected():
    pointer = UnityObjectPointer(AssetRef(HASH_A, 9), "m_Local", 0, 20)
    index = AssetIndex(402, (_asset(HASH_A, 20),))
    local_scene = UnitySceneParseResult(
        HASH_A, (), (), (), (), (), (pointer,), (), 402,
    )
    assert resolve_scene_assets(local_scene, index).references[0].target == AssetRef(HASH_A, 20)

    stale_scene = UnitySceneParseResult(
        HASH_A, (), (), (), (), (), (pointer,), (), 401,
    )
    stale = resolve_scene_assets(stale_scene, index)
    assert stale.complete is False
    assert stale.references[0].status == "version_mismatch"


def test_unknown_bundle_and_known_bundle_missing_file_are_distinct():
    pointer = UnityObjectPointer(AssetRef("local", 1), "m_Target", 1, 20, f"CAB-{HASH_A}", "")
    index = AssetIndex(402, (_asset(HASH_A, 20, present=False),))
    missing = resolve_scene_assets(
        _scene(pointer, externals=(UnityExternalFile(1, f"CAB-{HASH_A}", ""),)), index
    )
    unknown = resolve_scene_assets(
        _scene(
            UnityObjectPointer(
                AssetRef("local", 1), "m_Target", 1, 20, "CAB-" + "c" * 32, "",
            ),
            externals=(UnityExternalFile(1, "CAB-" + "c" * 32, ""),),
        ), index
    )
    assert missing.references[0].status == "bundle_missing"
    assert unknown.references[0].status == "bundle_unknown"


def test_duplicate_exact_index_identity_is_ambiguous():
    pointer = UnityObjectPointer(AssetRef("local", 1), "m_Target", 1, 20, f"CAB-{HASH_A}", "")
    index = AssetIndex(402, (_asset(HASH_A, 20), _asset(HASH_A, 20)))
    result = resolve_scene_assets(
        _scene(pointer, externals=(UnityExternalFile(1, f"CAB-{HASH_A}", ""),)), index
    )
    assert result.complete is False
    assert result.references[0].status == "ambiguous_mapping"


def test_missing_path_id_does_not_fall_back_to_same_named_asset():
    index = AssetIndex(402, (_asset(HASH_A, 21),))
    pointer = UnityObjectPointer(AssetRef("local", 1), "m_Mesh", 1, 20, f"CAB-{HASH_A}", "")

    result = resolve_scene_assets(
        _scene(pointer, externals=(UnityExternalFile(1, f"CAB-{HASH_A}", ""),)), index
    )

    assert result.complete is False
    assert result.references[0].status == "path_id_missing"


def test_cycle_in_structured_catalog_dependencies_terminates_deterministically():
    index = AssetIndex(402, (
        _asset(HASH_A, 1, dependencies=({"bundle_hash": HASH_B, "path_id": 2},)),
        _asset(HASH_B, 2, dependencies=({"bundle_hash": HASH_A, "path_id": 1},)),
    ))
    pointer = UnityObjectPointer(AssetRef("local", 1), "m_Root", 1, 1, f"CAB-{HASH_A}", "")
    scene = _scene(pointer, externals=(UnityExternalFile(1, f"CAB-{HASH_A}", ""),))

    first = resolve_scene_assets(scene, index)
    second = resolve_scene_assets(scene, index)

    assert first.complete is True
    assert first.visited == (AssetRef(HASH_A, 1), AssetRef(HASH_B, 2))
    assert first.visited == second.visited


def test_unstructured_catalog_dependency_is_not_guessed():
    index = AssetIndex(402, (_asset(HASH_A, 1, dependencies=("looks-like-a-hash",)),))
    pointer = UnityObjectPointer(AssetRef("local", 1), "m_Root", 1, 1, f"CAB-{HASH_A}", "")

    result = resolve_scene_assets(
        _scene(pointer, externals=(UnityExternalFile(1, f"CAB-{HASH_A}", ""),)), index
    )

    assert result.complete is False
    assert any(ref.status == "unsupported_dependency_reference_encoding" for ref in result.references)


def test_unbound_scene_version_never_resolves_against_an_arbitrary_index():
    pointer = UnityObjectPointer(AssetRef(HASH_A, 9), "m_Local", 0, 20)
    unbound = UnitySceneParseResult(HASH_A, (), (), (), (), (), (pointer,))

    result = resolve_scene_assets(unbound, AssetIndex(402, (_asset(HASH_A, 20),)))

    assert result.complete is False
    assert result.references[0].status == "version_context_missing"


def test_empty_scene_cannot_bypass_missing_or_mismatched_version_context():
    unbound = UnitySceneParseResult(HASH_A, (), (), (), (), ())
    mismatched = UnitySceneParseResult(HASH_A, (), (), (), (), (), version_timestamp=401)
    index = AssetIndex(402, ())

    no_version = resolve_scene_assets(unbound, index)
    wrong_version = resolve_scene_assets(mismatched, index)

    assert no_version.complete is False
    assert wrong_version.complete is False


def test_parse_error_diagnostic_blocks_complete_even_when_refs_resolve():
    from app.features.preview.portrait_config.model import SceneDiagnostic

    scene = UnitySceneParseResult(
        HASH_A, (), (), (), (), (SceneDiagnostic("typetree_read_failed", "unreadable"),),
        (UnityObjectPointer(AssetRef(HASH_A, 9), "m_Local", 0, 20),), (), 402,
    )

    result = resolve_scene_assets(scene, AssetIndex(402, (_asset(HASH_A, 20),)))

    assert result.references[0].status == "resolved"
    assert result.complete is False


def test_scene_level_external_table_cannot_guess_which_serialized_file_owns_file_id():
    pointer = UnityObjectPointer(AssetRef("local", 1), "m_Target", 1, 20)
    index = AssetIndex(402, (_asset(HASH_A, 20),))

    result = resolve_scene_assets(
        _scene(pointer, externals=(UnityExternalFile(1, f"CAB-{HASH_A}", ""),)), index
    )

    assert result.complete is False
    assert result.references[0].status == "external_file_id_invalid"


def test_reused_file_id_resolves_only_with_owning_serialized_file_key():
    pointer = UnityObjectPointer(
        AssetRef("local", 1), "m_Target", 1, 20, serialized_file_key="serialized-A",
    )
    scene = _scene(pointer, externals=(
        UnityExternalFile(1, f"CAB-{HASH_A}", "", "serialized-A"),
        UnityExternalFile(1, f"CAB-{HASH_B}", "", "serialized-B"),
    ))

    result = resolve_scene_assets(
        scene, AssetIndex(402, (_asset(HASH_A, 20), _asset(HASH_B, 20))),
    )

    assert result.complete is True
    assert result.references[0].target == AssetRef(HASH_A, 20)


def test_conflicting_external_entries_for_one_serialized_file_are_ambiguous():
    pointer = UnityObjectPointer(
        AssetRef("local", 1), "m_Target", 1, 20, serialized_file_key="serialized-A",
    )
    scene = _scene(pointer, externals=(
        UnityExternalFile(1, f"CAB-{HASH_A}", "", "serialized-A"),
        UnityExternalFile(1, f"CAB-{HASH_B}", "", "serialized-A"),
    ))

    result = resolve_scene_assets(scene, AssetIndex(402, (_asset(HASH_A, 20),)))

    assert result.complete is False
    assert result.references[0].status == "ambiguous_mapping"


def test_container_style_catalog_dependency_is_reported_not_guessed():
    index = AssetIndex(402, (
        _asset(HASH_A, 1, dependencies=({"container": "Assets/Textures/icon.png"},)),
    ))
    pointer = UnityObjectPointer(AssetRef("local", 1), "m_Root", 1, 1, f"CAB-{HASH_A}", "")

    result = resolve_scene_assets(
        _scene(pointer, externals=(UnityExternalFile(1, f"CAB-{HASH_A}", ""),)), index
    )

    assert result.complete is False
    assert result.references[-1].status == "unsupported_dependency_reference_encoding"


def test_fractional_and_boolean_path_ids_are_not_coerced_to_exact_references():
    pointer = UnityObjectPointer(
        AssetRef("local", 1), "m_Root", 1, 1, f"CAB-{HASH_A}", "",
    )
    index_with_bad_edges = AssetIndex(
        402,
        (
            _asset(HASH_A, 1, dependencies=(
                {"bundle_hash": HASH_A, "path_id": 20.9},
                {"bundle_hash": HASH_A, "path_id": True},
            )),
            _asset(HASH_A, 20),
        ),
    )

    result = resolve_scene_assets(
        _scene(pointer, externals=(UnityExternalFile(1, f"CAB-{HASH_A}", ""),)),
        index_with_bad_edges,
    )

    assert result.complete is False
    assert [item.status for item in result.references[1:]] == [
        "unsupported_dependency_reference_encoding",
        "unsupported_dependency_reference_encoding",
    ]
