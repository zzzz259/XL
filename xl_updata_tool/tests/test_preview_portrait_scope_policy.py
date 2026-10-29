from pathlib import Path
from types import SimpleNamespace

import pytest

from app.features.preview.portrait_config import unity_scene
from app.features.preview.portrait_config.asset_index import AssetIndex, IndexedAsset
from app.features.preview.portrait_config.dependency_resolver import resolve_scene_assets
from app.features.preview.portrait_config.model import (
    AssetDependency, AssetRef, PortraitScene, RendererState, SceneDiagnostic, ScenePart, SpineState, SpriteState,
)


def asset(bundle, pid, kind="Texture2D", name="same"):
    return IndexedAsset(name, "", bundle, pid, (), 402, Path(bundle), True, "test", kind)


def obj(pid, kind, cab, tree=None):
    return SimpleNamespace(path_id=pid, type=SimpleNamespace(name=kind),
                           assets_file=SimpleNamespace(name=f"CAB-{cab}", externals=()),
                           read_typetree=lambda: tree or {})


def test_external_cab_is_verified_against_owning_file_and_object_not_physical_filename(monkeypatch):
    cab, physical, collision = "c" * 32, "a" * 32, "b" * 32
    wanted, unrelated = asset(physical, -42), asset(collision, -42)
    objects = {physical: [obj(-42, "Texture2D", cab)],
               collision: [obj(-42, "Texture2D", "d" * 32)]}
    loads = []

    def load(path):
        loads.append(str(path))
        return SimpleNamespace(objects=objects[str(path)])

    monkeypatch.setattr(unity_scene, "_load_environment", load)
    index = AssetIndex(402, (unrelated, wanted))
    assert loads == []
    assert index.find_external(f"CAB-{cab}", -42) == (wanted,)
    assert index.find_external(f"archive:/CAB-{cab}/CAB-{cab}", -42) == (wanted,)
    assert len(loads) == 2
    assert index.find_external(f"CAB-{physical}", -42) == ()


def test_external_same_cab_and_path_in_two_physical_bundles_remains_ambiguous(monkeypatch):
    cab = "c" * 32
    first, second = asset("a" * 32, 42), asset("b" * 32, 42)
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _: SimpleNamespace(
        objects=[obj(42, "Texture2D", cab)]))
    index = AssetIndex(402, (first, second))
    assert index.find_external(f"CAB-{cab}", 42) == (first, second)


def test_external_catalog_object_must_exist_with_same_type_in_serialized_file(monkeypatch):
    cab = "c" * 32
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _: SimpleNamespace(
        objects=[obj(41, "Texture2D", cab), obj(42, "Material", cab)]))
    index = AssetIndex(402, (asset("a" * 32, 42), asset("b" * 32, 43)))
    assert index.find_external(f"CAB-{cab}", 42) == ()
    assert index.find_external(f"CAB-{cab}", 43) == ()


def test_resolver_uses_verified_physical_identity_and_expands_main_material_texture(monkeypatch):
    from app.features.preview.portrait_config.unity_scene import UnityObjectPointer, UnitySceneParseResult

    cab, physical = "c" * 32, "a" * 32
    material = asset(physical, 41, "Material")
    main, auxiliary = asset(physical, 42, name="main_n"), asset(physical, 43, name="ordinary")
    def pointer(pid):
        return {"m_FileID": 0, "m_PathID": pid}
    tree = {"m_SavedProperties": {"m_TexEnvs": [
        ("_MainTex", {"m_Texture": pointer(42)}),
        ("_BumpMap", {"m_Texture": pointer(43)}),
    ]}}
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _: SimpleNamespace(objects=[
        obj(41, "Material", cab, tree), obj(42, "Texture2D", cab), obj(43, "Texture2D", cab),
    ]))
    parsed = UnitySceneParseResult("local", (), (), (), (), (SceneDiagnostic(
        "unresolved_external_pptr", "Pending external material", source=AssetRef("local", 1),
        details=(("field", "m_Materials"), ("file_id", "1"), ("path_id", "41")),
    ),), (
        UnityObjectPointer(AssetRef("local", 1), "m_Materials[0]", 1, 41, f"CAB-{cab}"),
    ), (), 402)
    result = resolve_scene_assets(parsed, AssetIndex(402, (material, main, auxiliary)))
    assert result.complete
    assert result.visited == (AssetRef(physical, 41), AssetRef(physical, 42))
    assert result.references[0].target == AssetRef(physical, 41)
    assert "_MainTex" in result.references[1].field_path


def test_required_assets_keep_primary_images_and_skip_auxiliary_and_unrelated_graph():
    from app.features.preview.portrait_config.scope_policy import required_scene_assets

    def ref(pid):
        return AssetRef("bundle", pid)
    records = tuple(asset("bundle", pid, kind, name) for pid, kind, name in [
        (1, "MonoBehaviour", "data"), (2, "TextAsset", "hero.skel"),
        (3, "TextAsset", "hero.atlas"), (4, "Material", "mat"),
        (5, "Texture2D", "main_n"), (6, "Texture2D", "innocent"),
        (7, "Sprite", "sprite"), (8, "Texture2D", "sfx_main"),
        (9, "Material", "smoke"), (10, "Texture2D", "particle"),
    ])
    scene = PortraitScene("v", "402", "cfg", "role", "skin", None, ("bundle",), (
        ScenePart("spine", ref(20), "hero", renderer=RendererState("MeshRenderer", True, materials=(ref(4),)),
                  spine=SpineState(ref(1), atlas=ref(3), skeleton_json=ref(2))),
        ScenePart("sprite", ref(21), "background", sprite=SpriteState(ref(7), texture=ref(8))),
    ), asset_dependencies=(
        AssetDependency(ref(4), "m_SavedProperties.m_TexEnvs._MainTex.m_Texture", ref(5)),
        AssetDependency(ref(4), "m_SavedProperties.m_TexEnvs._BumpMap.m_Texture", ref(6)),
        AssetDependency(ref(1), "unrelatedParticleMaterial", ref(9)),
        AssetDependency(ref(9), "m_SavedProperties.m_TexEnvs._MainTex.m_Texture", ref(10)),
    ))
    assert required_scene_assets(scene, AssetIndex(402, records)) == (ref(2), ref(3), ref(5), ref(8))


def test_parser_records_real_material_property_and_drops_control_textures():
    def pointer(pid):
        return {"m_FileID": 0, "m_PathID": pid}
    textures = [(name, {"m_Texture": pointer(pid)}) for pid, name in enumerate(
        ("_MainTex", "_BumpMap", "_EmissionMap", "_noise_tex", "_FlowMap", "_MaskTex"), 2)]
    tree = {"m_SavedProperties": {"m_TexEnvs": textures}}
    records = unity_scene._pointer_records({1: tree}, {1: obj(1, "Material", "a" * 32)}, "bundle")
    assert [(p.path_id, p.field_path) for p in records] == [
        (2, "m_SavedProperties.m_TexEnvs._MainTex.m_Texture")]


def test_missing_primary_image_cannot_silently_produce_an_empty_selection():
    from app.features.preview.portrait_config.scope_policy import required_scene_assets

    scene = PortraitScene("v", "402", "cfg", "role", "skin", None, (), (
        ScenePart("bg", AssetRef("bundle", 1), "bg", sprite=SpriteState(
            AssetRef("bundle", 2), texture=AssetRef("bundle", 3))),
    ))
    with pytest.raises(ValueError, match="required portrait asset missing"):
        required_scene_assets(scene, AssetIndex(402, ()))


def test_structured_dependencies_preserve_negative_unity_path_ids():
    from app.features.preview.portrait_config.unity_scene import UnityObjectPointer, UnitySceneParseResult

    first = IndexedAsset("first", "", "bundle", 1, ({"bundle_hash": "bundle", "path_id": -2},),
                         402, Path("bundle"), True, "test", "TextAsset")
    parsed = UnitySceneParseResult("bundle", (), (), (), (), (), (
        UnityObjectPointer(AssetRef("bundle", 10), "skeletonJSON", 0, 1),
    ), (), 402)
    result = resolve_scene_assets(parsed, AssetIndex(402, (first, asset("bundle", -2))))
    assert result.complete
    assert result.visited == (AssetRef("bundle", 1), AssetRef("bundle", -2))
