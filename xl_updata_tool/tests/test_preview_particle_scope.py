from pathlib import Path
from types import SimpleNamespace
from app.features.preview.portrait_config import unity_scene
from app.features.preview.portrait_config.builder import _scope_to_container
from app.features.preview.portrait_config.asset_index import AssetIndex, IndexedAsset
from test_preview_portrait_unity_scene import FakeObject, _scene_objects, _ptr


def test_exact_container_keeps_unknown_descendant_diagnostics_without_sibling_leak(monkeypatch):
    objects = _scene_objects()
    objects.append(FakeObject(90, "AssetBundle", {"m_Container": [("assets/target.prefab", {"asset": _ptr(1)})]}))
    monkeypatch.setattr(unity_scene, "_load_environment", lambda p: SimpleNamespace(objects=objects))
    parsed = unity_scene.parse_unity_scene(Path("x"), "a", 1)
    asset = IndexedAsset("texture", "assets/target.prefab", "a", 70, (), 1, Path("x"), True, "fixture", "Texture2D")
    selected = _scope_to_container(parsed, (asset,), AssetIndex(1, (asset,)))
    assert len(selected.parts) == 3
    assert any(d.code == "unsupported_enabled_renderer" and d.source.path_id == 13 for d in selected.diagnostics)


def test_inactive_ancestor_disables_descendant_renderers(monkeypatch):
    objects = _scene_objects()
    objects[0].tree["m_IsActive"] = False
    monkeypatch.setattr(unity_scene, "_load_environment", lambda p: SimpleNamespace(objects=objects))
    parsed = unity_scene.parse_unity_scene(Path("x"), "a", 1)
    assert not any(part.enabled for part in parsed.parts)
    assert not any(d.code == "unsupported_enabled_renderer" for d in parsed.diagnostics)


def test_exact_container_excludes_other_root_even_when_materials_are_shared(monkeypatch):
    objects = _scene_objects()
    objects.extend(
        [
            FakeObject(
                90,
                "AssetBundle",
                {
                    "m_Container": [
                        ("assets/target.prefab", {"asset": _ptr(1)}),
                        ("assets/other.prefab", {"asset": _ptr(100)}),
                    ]
                },
            ),
            FakeObject(
                100,
                "GameObject",
                {"m_Name": "other", "m_Component": [{"component": _ptr(101)}, {"component": _ptr(102)}]},
            ),
            FakeObject(101, "Transform", {"m_GameObject": _ptr(100), "m_Father": _ptr(0)}),
            FakeObject(
                102, "UnknownRenderer", {"m_GameObject": _ptr(100), "m_Enabled": True, "m_Materials": [_ptr(72)]}
            ),
        ]
    )
    monkeypatch.setattr(unity_scene, "_load_environment", lambda p: SimpleNamespace(objects=objects))
    parsed = unity_scene.parse_unity_scene(Path("x"), "a", 1)
    asset = IndexedAsset("texture", "assets/target.prefab", "a", 70, (), 1, Path("x"), True, "fixture", "Texture2D")
    selected = _scope_to_container(parsed, (asset,), AssetIndex(1, (asset,)))
    assert any(d.source and d.source.path_id == 13 for d in selected.diagnostics)
    assert not any(d.source and d.source.path_id == 102 for d in selected.diagnostics)
    assert not any(ref.path_id == 100 for ref, name in selected.game_objects)


def test_builder_preserves_unsupported_diagnostic_when_prefab_has_no_known_parts(tmp_path, monkeypatch):
    from dataclasses import replace
    from app.features.preview.portrait_config import builder
    from app.features.preview.portrait_config.model import SceneDiagnostic
    from test_preview_portrait_scene_builder import _root, _index, _parsed_scene, VERSION, PREFAB_HASH

    root = _root(tmp_path)
    asset = replace(_index(tmp_path).assets[0], name="exportable", container="assets/" + root.prefab_name)
    source = unity_scene.AssetRef(PREFAB_HASH, 99)
    selected = replace(
        _parsed_scene(),
        parts=(),
        diagnostics=(SceneDiagnostic("unsupported_enabled_renderer", "unknown renderer", source=source),),
        container_roots=((asset.container, unity_scene.AssetRef(PREFAB_HASH, 1)),),
        object_ancestry=((unity_scene.AssetRef(PREFAB_HASH, 1), (unity_scene.AssetRef(PREFAB_HASH, 1),)),),
        component_refs=((source, unity_scene.AssetRef(PREFAB_HASH, 1), "UnknownRenderer"),),
    )
    monkeypatch.setattr(builder, "parse_unity_scene", lambda *a: selected)
    scene = builder.build_portrait_scenes(VERSION, [root], AssetIndex(VERSION, (asset,)))[0]
    assert "unsupported_enabled_renderer" in {d.code for d in scene.diagnostics}


def _two_prefabs():
    from dataclasses import replace
    from test_preview_portrait_scene_builder import _parsed_scene, PREFAB_HASH
    from app.features.preview.portrait_config.model import AssetRef

    target = _parsed_scene()
    root = AssetRef(PREFAB_HASH, 1)
    other_root = AssetRef(PREFAB_HASH, 100)
    sibling = replace(
        target.parts[0],
        game_object_ref=other_root,
        component_ref=AssetRef(PREFAB_HASH, 101),
        game_object_name="sibling",
        hierarchy_path="sibling",
        transform_chain=(),
    )
    return replace(
        target,
        parts=(*target.parts, sibling),
        container_roots=(("assets/target.prefab", root), ("assets/sibling.prefab", other_root)),
        object_ancestry=(
            (target.parts[0].game_object_ref, (root, target.parts[0].game_object_ref)),
            (root, (root,)),
            (other_root, (other_root,)),
        ),
        component_refs=(
            (target.parts[0].component_ref, target.parts[0].game_object_ref, "SkeletonAnimation"),
            (sibling.component_ref, other_root, "SkeletonAnimation"),
        ),
    )


def test_container_texture_and_textasset_entries_are_not_prefab_roots():
    from dataclasses import replace
    from app.features.preview.portrait_config.model import AssetRef
    from test_preview_portrait_scene_builder import PREFAB_HASH, VERSION

    parsed = _two_prefabs()
    root = parsed.container_roots[0]
    parsed = replace(parsed, container_roots=(*parsed.container_roots, (root[0], AssetRef(PREFAB_HASH, 999))))
    asset = IndexedAsset("skeleton", root[0], PREFAB_HASH, 999, (), VERSION, Path("x"), True, "fixture", "TextAsset")
    scoped = _scope_to_container(parsed, (asset,), AssetIndex(VERSION, (asset,)))
    assert len(scoped.parts) == 1
    assert not any(d.code == "ambiguous_prefab_root" for d in scoped.diagnostics)


def test_direct_prefab_name_scopes_actual_container_root(tmp_path, monkeypatch):
    from app.features.preview.portrait_config import builder
    from test_preview_portrait_scene_builder import _root, _index, VERSION

    parsed = _two_prefabs()
    monkeypatch.setattr(builder, "parse_unity_scene", lambda *args: parsed)
    scene = builder.build_portrait_scenes(VERSION, [_root(tmp_path)], _index(tmp_path))[0]
    assert [p.game_object_name for p in scene.parts] == ["cardspine_10118_3"]
    assert not [d for d in scene.diagnostics if d.severity == "error"]


def test_touch_event_target_reference_does_not_establish_prefab_ownership():
    from dataclasses import replace
    from app.features.preview.portrait_config.unity_scene import UnityInteraction
    from app.features.preview.portrait_config.model import AssetRef
    from test_preview_portrait_scene_builder import PREFAB_HASH, VERSION

    parsed = _two_prefabs()
    target = parsed.parts[0].component_ref
    owned = UnityInteraction(AssetRef(PREFAB_HASH, 200), parsed.parts[0].game_object_ref, "CLICK", "touch", target)
    foreign = UnityInteraction(AssetRef(PREFAB_HASH, 201), parsed.parts[1].game_object_ref, "CLICK", "touch", target)
    parsed = replace(parsed, interactions=(owned, foreign))
    asset = IndexedAsset("export", "assets/target.prefab", PREFAB_HASH, 21, (), VERSION, Path("x"), True, "fixture")
    scoped = _scope_to_container(parsed, (asset,), AssetIndex(VERSION, (asset,)))
    assert scoped.interactions == (owned,)


def test_assetbundle_container_resource_rows_are_not_prefab_root_candidates(monkeypatch):
    objects = _scene_objects()
    objects.append(
        FakeObject(
            90,
            "AssetBundle",
            {
                "m_Container": [
                    ("assets/target.prefab", {"asset": _ptr(1)}),
                    ("assets/target.prefab", {"asset": _ptr(70)}),
                ]
            },
        )
    )
    monkeypatch.setattr(unity_scene, "_load_environment", lambda p: SimpleNamespace(objects=objects))
    parsed = unity_scene.parse_unity_scene(Path("x"), "a", 1)
    assert parsed.container_roots == (("assets/target.prefab", unity_scene.AssetRef("a", 1)),)
