from pathlib import Path
from types import SimpleNamespace

import pytest

from app.features.preview.portrait_config import unity_scene


class FakeObject:
    def __init__(self, path_id, type_name, tree):
        self.path_id = path_id
        self.type = SimpleNamespace(name=type_name)
        self.tree = tree

    def read_typetree(self):
        return self.tree


class BrokenObject(FakeObject):
    def read_typetree(self):
        raise ValueError("synthetic typetree parse failure")


def _vec(x=0, y=0, z=0):
    return {"x": x, "y": y, "z": z}


def _ptr(path_id):
    return {"m_FileID": 0, "m_PathID": path_id}


def _scene_objects():
    return [
        FakeObject(1, "GameObject", {"m_Name": "root", "m_Component": [{"component": _ptr(2)}, {"component": _ptr(7)}]}),
        FakeObject(2, "Transform", {"m_GameObject": _ptr(1), "m_Father": _ptr(0),
                                     "m_Children": [{"m_PathID": 4}, {"m_PathID": 9}], "m_RootOrder": 0,
                                     "m_LocalPosition": _vec(10, 20), "m_LocalRotation": {"x": 0, "y": 0, "z": 0, "w": 1},
                                     "m_LocalScale": _vec(1, 1, 1)}),
        FakeObject(3, "GameObject", {"m_Name": "hero", "m_Component": [
            {"component": _ptr(4)}, {"component": _ptr(5)}, {"component": _ptr(6)}, {"component": _ptr(12)}]}),
        FakeObject(4, "Transform", {"m_GameObject": _ptr(3), "m_Father": _ptr(2),
                                     "m_Children": [], "m_RootOrder": 0,
                                     "m_LocalPosition": _vec(1, 2), "m_LocalRotation": {"x": 0, "y": 0, "z": 0, "w": 1},
                                     "m_LocalScale": _vec(1, 1, 1)}),
        FakeObject(5, "MonoBehaviour", {"m_GameObject": _ptr(3), "skeletonDataAsset": _ptr(50),
                                         "_animationName": "idle", "loop": True, "m_Enabled": True}),
        FakeObject(6, "MonoBehaviour", {"m_GameObject": _ptr(3), "skeletonDataAsset": _ptr(51),
                                         "_animationName": "touch_feedback_1", "loop": False, "m_Enabled": True}),
        FakeObject(7, "MonoBehaviour", {"m_GameObject": _ptr(1), "scriptName": "touch_cardspine",
            "eventAnimationEntries": [{"gesture": "CLICK", "requiredFingers": 1, "animationEntries": [
                {"animation": "touch_feedback_1", "skeletonAnimation": _ptr(6)}]}]}),
        FakeObject(8, "GameObject", {"m_Name": "z_bg", "m_Component": [
            {"component": _ptr(9)}, {"component": _ptr(10)}, {"component": _ptr(13)}]}),
        FakeObject(9, "Transform", {"m_GameObject": _ptr(8), "m_Father": _ptr(2), "m_Children": [], "m_RootOrder": 1,
            "m_LocalPosition": _vec(0, 0, -1), "m_LocalRotation": {"x": 0, "y": 0, "z": 0, "w": 1},
            "m_LocalScale": _vec(1, 1, 1)}),
        FakeObject(10, "SpriteRenderer", {"m_GameObject": _ptr(8), "m_Enabled": True, "m_SortingLayer": 0,
            "m_SortingLayerID": 7, "m_SortingOrder": 0, "m_Sprite": _ptr(11), "m_Materials": [],
            "m_FlipX": True, "m_FlipY": False, "m_Color": {"r": 0.5, "g": 1, "b": 1, "a": 0.75},
            "m_Size": {"x": 1.25, "y": 1.5}, "m_DrawMode": 0, "m_MaskInteraction": 1}),
        FakeObject(11, "Sprite", {"m_Name": "z_bg", "m_Rect": {"x": 3, "y": 4, "width": 50, "height": 60},
            "m_Pivot": {"x": 0.25, "y": 0.75}, "m_PixelsToUnits": 100,
            "m_RD": {"texture": _ptr(70)}}),
        FakeObject(12, "MeshRenderer", {"m_GameObject": _ptr(3), "m_Enabled": True, "m_SortingLayer": 0,
            "m_SortingOrder": 1, "m_Materials": [{"m_PathID": 72, "m_FileID": 0}]}),
        FakeObject(50, "MonoBehaviour", {"m_Name": "hero data", "skeletonJSON": _ptr(60), "atlasAssets": [_ptr(61)]}),
        FakeObject(51, "MonoBehaviour", {"m_Name": "hero touch data", "skeletonJSON": _ptr(62), "atlasAssets": [_ptr(63)]}),
        FakeObject(13, "SkinnedMeshRenderer", {"m_GameObject": _ptr(8), "m_Enabled": True}),
        FakeObject(70, "Texture2D", {"m_Name": "texture"}),
        FakeObject(72, "Material", {"m_Name": "material"}),
    ]


def test_nested_transform_chain_and_same_gameobject_spine_components_are_distinct(monkeypatch):
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=_scene_objects()))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")

    spine_parts = [part for part in parsed.parts if part.component_type == "SkeletonAnimation"]
    assert [part.component_ref.path_id for part in spine_parts] == [5, 6]
    assert spine_parts[0].transform_chain[0].local_position == (10.0, 20.0, 0.0)
    assert spine_parts[0].transform_chain[-1].local_position == (1.0, 2.0, 0.0)
    assert spine_parts[0].world_position == (11.0, 22.0, 0.0)
    assert spine_parts[0].hierarchy_path == "root/hero"


def test_skeleton_atlas_assets_resolve_through_atlas_asset_to_text_asset(monkeypatch):
    objects = _scene_objects()
    skeleton_data = next(obj for obj in objects if obj.path_id == 50)
    skeleton_data.tree["atlasAssets"] = [_ptr(61)]
    objects.extend([
        FakeObject(60, "TextAsset", {"m_Name": "hero.skel"}),
        FakeObject(61, "MonoBehaviour", {"m_Name": "hero_Atlas", "atlasFile": _ptr(64)}),
        FakeObject(64, "TextAsset", {"m_Name": "hero.atlas"}),
    ])
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")

    spine = next(part for part in parsed.parts if part.component_ref.path_id == 5)
    assert [ref.path_id for ref in spine.atlas_refs] == [64]


def test_atlas_asset_without_atlas_file_is_not_mistaken_for_atlas_text(monkeypatch):
    objects = _scene_objects()
    skeleton_data = next(obj for obj in objects if obj.path_id == 50)
    skeleton_data.tree["atlasAssets"] = [_ptr(61)]
    objects.extend([
        FakeObject(60, "TextAsset", {"m_Name": "hero.skel"}),
        FakeObject(61, "MonoBehaviour", {"m_Name": "hero_Atlas"}),
    ])
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")

    spine = next(part for part in parsed.parts if part.component_ref.path_id == 5)
    assert spine.atlas_refs == ()
    assert any(item.code == "atlas_file_missing" for item in parsed.diagnostics)


@pytest.mark.parametrize(
    ("pointer", "diagnostic"),
    [(_ptr(999), "unresolved_pptr"),
     ({"m_FileID": 1, "m_PathID": 999}, "unresolved_external_pptr")],
)
def test_unresolved_atlas_asset_pointer_is_reported(monkeypatch, pointer, diagnostic):
    objects = _scene_objects()
    skeleton_data = next(obj for obj in objects if obj.path_id == 50)
    skeleton_data.tree["atlasAssets"] = [pointer]
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")

    item = next(
        item for item in parsed.diagnostics
        if item.code == diagnostic and dict(item.details).get("path_id") == "999"
    )
    assert dict(item.details)["field"] == "atlasAssets"


def test_touch_event_resolves_exact_component_reference_not_gameobject_name(monkeypatch):
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=_scene_objects()))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")

    assert len(parsed.interactions) == 1
    assert parsed.interactions[0].gesture == "CLICK"
    assert parsed.interactions[0].animation == "touch_feedback_1"
    assert parsed.interactions[0].target_component.path_id == 6
    assert parsed.interactions[0].callback_script == "touch_cardspine"


def test_sprite_rect_pivot_and_sorting_come_from_serialized_data_not_names(monkeypatch):
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=_scene_objects()))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")

    sprite = next(part for part in parsed.parts if part.component_type == "SpriteRenderer")
    assert sprite.game_object_name == "z_bg"
    assert sprite.sprite_rect == (3.0, 4.0, 50.0, 60.0)
    assert sprite.sprite_pivot == (0.25, 0.75)
    assert sprite.pixels_to_units == 100.0
    assert sprite.sprite_texture_ref.path_id == 70
    assert sprite.flip_x is True
    assert sprite.renderer_color == (0.5, 1.0, 1.0, 0.75)
    assert sprite.renderer_size == (1.25, 1.5)
    assert sprite.sorting_layer_id == 7
    assert sprite.mask_interaction == 1
    spine = next(part for part in parsed.parts if part.component_type == "SkeletonAnimation")
    assert parsed.sort_key(sprite) < parsed.sort_key(spine)
    assert any(diagnostic.code == "unsupported_enabled_renderer" for diagnostic in parsed.diagnostics)


def test_sprite_material_blend_mode_is_classified_from_material_typetree(monkeypatch):
    objects = _scene_objects()
    renderer = next(obj for obj in objects if obj.path_id == 10)
    renderer.tree["m_Materials"] = [_ptr(72)]
    material = next(obj for obj in objects if obj.path_id == 72)
    material.tree["m_SavedProperties"] = {"m_Floats": {"_SrcBlend": 1.0, "_DstBlend": 10.0}}
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")

    sprite = next(part for part in parsed.parts if part.component_type == "SpriteRenderer")
    assert sprite.blend_mode == "normal"

    material.tree["m_SavedProperties"]["m_Floats"]["_DstBlend"] = 1.0
    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")
    sprite = next(part for part in parsed.parts if part.component_type == "SpriteRenderer")
    assert sprite.blend_mode == "unsupported"


def test_spine_screen_material_variant_is_rejected_until_renderer_supports_it(monkeypatch):
    objects = _scene_objects()
    renderer = next(obj for obj in objects if obj.path_id == 12)
    renderer.tree["m_Materials"] = [_ptr(72)]
    material = next(obj for obj in objects if obj.path_id == 72)
    material.tree.update({
        "m_Name": "eventcovers_0002_Material-Screen",
        "m_SavedProperties": {"m_Floats": [("_StencilRef", 0), ("_StencilComp", 8)]},
    })
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")

    spine = next(part for part in parsed.parts if part.component_type == "SkeletonAnimation")
    assert spine.blend_mode == "unsupported"


def test_spine_additive_material_with_game_stencil_reference_is_rejected(monkeypatch):
    objects = _scene_objects()
    renderer = next(obj for obj in objects if obj.path_id == 12)
    renderer.tree["m_Materials"] = [_ptr(72)]
    material = next(obj for obj in objects if obj.path_id == 72)
    material.tree.update({
        "m_Name": "eventcovers_0035_Material-Additive",
        "m_SavedProperties": {"m_Floats": [("_StencilRef", 1), ("_StencilComp", 8)]},
    })
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")

    spine = next(part for part in parsed.parts if part.component_type == "SkeletonAnimation")
    assert spine.blend_mode == "unsupported"


def test_spine_material_with_unknown_blend_name_is_unsupported(monkeypatch):
    objects = _scene_objects()
    renderer = next(obj for obj in objects if obj.path_id == 12)
    renderer.tree["m_Materials"] = [_ptr(72)]
    material = next(obj for obj in objects if obj.path_id == 72)
    material.tree.update({
        "m_Name": "custom_spine_shader",
        "m_SavedProperties": {"m_Floats": [("_StencilRef", 0)]},
    })
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")

    spine = next(part for part in parsed.parts if part.component_type == "SkeletonAnimation")
    assert spine.blend_mode == "unsupported"


def test_skeleton_data_asset_and_renderer_component_are_linked_without_duplicate_mesh_part(monkeypatch):
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=_scene_objects()))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")

    spine_parts = [part for part in parsed.parts if part.component_type == "SkeletonAnimation"]
    assert len(spine_parts) == 2
    assert [part.skeleton_ref.path_id for part in spine_parts] == [50, 51]
    assert all(part.renderer_ref.path_id == 12 for part in spine_parts)
    assert not any(part.component_ref.path_id == 12 for part in parsed.parts)
    assert any(diagnostic.code == "unresolved_pptr" and dict(diagnostic.details).get("field") == "skeletonJSON"
               for diagnostic in parsed.diagnostics)


def test_world_transform_applies_parent_scale_and_rotation(monkeypatch):
    objects = _scene_objects()
    root_transform = next(obj for obj in objects if obj.path_id == 2)
    child_transform = next(obj for obj in objects if obj.path_id == 4)
    root_transform.tree["m_LocalScale"] = _vec(2, 2, 1)
    root_transform.tree["m_LocalRotation"] = {"x": 0, "y": 0, "z": 0.70710678118, "w": 0.70710678118}
    child_transform.tree["m_LocalPosition"] = _vec(1, 0)
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")
    hero = next(part for part in parsed.parts if part.component_type == "SkeletonAnimation")

    assert hero.world_position[0] == pytest.approx(10.0)
    assert hero.world_position[1] == pytest.approx(22.0)


def test_world_transform_uses_full_matrix_for_nested_rotation_and_nonuniform_scale(monkeypatch):
    objects = _scene_objects()
    root_transform = next(obj for obj in objects if obj.path_id == 2)
    hero_transform = next(obj for obj in objects if obj.path_id == 4)
    root_transform.tree["m_LocalScale"] = _vec(2, 3, 1)
    root_transform.tree["m_LocalRotation"] = {"x": 0, "y": 0, "z": 0.70710678118, "w": 0.70710678118}
    hero_transform.tree["m_LocalPosition"] = _vec(1, 0)
    hero_transform.tree["m_LocalRotation"] = {"x": 0, "y": 0, "z": 0.70710678118, "w": 0.70710678118}
    hero_transform.tree["m_Children"].append(_ptr(15))
    objects.extend([
        FakeObject(14, "GameObject", {"m_Name": "nested", "m_Component": [{"component": _ptr(15)}, {"component": _ptr(16)}]}),
        FakeObject(15, "Transform", {"m_GameObject": _ptr(14), "m_Father": _ptr(4), "m_Children": [], "m_RootOrder": 0,
            "m_LocalPosition": _vec(1, 0), "m_LocalRotation": {"x": 0, "y": 0, "z": 0, "w": 1},
            "m_LocalScale": _vec(1, 1, 1)}),
        FakeObject(16, "MeshRenderer", {"m_GameObject": _ptr(14), "m_Enabled": True, "m_SortingLayer": 0, "m_SortingOrder": 5}),
    ])
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")
    nested = next(part for part in parsed.parts if part.game_object_name == "nested")

    assert nested.world_position[0] == pytest.approx(7.0)
    assert nested.world_position[1] == pytest.approx(22.0)


def test_ambiguous_spine_to_renderer_mapping_is_diagnostic_not_first_match(monkeypatch):
    objects = _scene_objects()
    game_object = next(obj for obj in objects if obj.path_id == 3)
    game_object.tree["m_Component"].append({"component": _ptr(14)})
    objects.append(FakeObject(14, "MeshRenderer", {"m_GameObject": _ptr(3), "m_Enabled": True,
        "m_SortingLayer": 0, "m_SortingOrder": 2}))
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")
    spine_parts = [part for part in parsed.parts if part.component_type == "SkeletonAnimation"]

    assert all(part.renderer_ref is None for part in spine_parts)
    assert any(diagnostic.code == "ambiguous_spine_renderer_binding" for diagnostic in parsed.diagnostics)


def test_missing_and_cross_file_component_and_sprite_references_are_diagnostic(monkeypatch):
    objects = _scene_objects()
    root = next(obj for obj in objects if obj.path_id == 1)
    root.tree["m_Component"].append({"component": _ptr(999)})
    sprite_renderer = next(obj for obj in objects if obj.path_id == 10)
    sprite_renderer.tree["m_Sprite"] = {"m_FileID": 2, "m_PathID": 900}
    sprite = next(obj for obj in objects if obj.path_id == 11)
    sprite.tree["m_RD"]["texture"] = _ptr(999)
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")
    unresolved = [diagnostic for diagnostic in parsed.diagnostics if diagnostic.code.startswith("unresolved")]

    assert any(dict(item.details).get("field") == "m_Component" and dict(item.details).get("path_id") == "999"
               for item in unresolved)
    assert any(dict(item.details).get("field") == "m_Sprite" and dict(item.details).get("file_id") == "2"
               for item in unresolved)

    texture_objects = _scene_objects()
    sprite = next(obj for obj in texture_objects if obj.path_id == 11)
    sprite.tree["m_RD"]["texture"] = _ptr(999)
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=texture_objects))
    texture_result = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")
    assert any(dict(item.details).get("field") == "m_RD.texture" for item in texture_result.diagnostics)


def test_missing_transform_is_not_misreported_as_a_transform_cycle(monkeypatch):
    objects = [obj for obj in _scene_objects() if obj.path_id != 9]
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")
    missing = [item for item in parsed.diagnostics if item.code == "missing_transform"]

    assert any(item.source.path_id == 8 for item in missing)
    assert not any(item.code == "transform_cycle" for item in parsed.diagnostics)


def test_component_with_unreadable_typetree_is_linked_to_owning_gameobject(monkeypatch):
    objects = _scene_objects()
    game_object = next(obj for obj in objects if obj.path_id == 3)
    game_object.tree["m_Component"].append({"component": _ptr(14)})
    objects.append(BrokenObject(14, "MeshRenderer", {}))
    monkeypatch.setattr(unity_scene, "_load_environment", lambda _path: SimpleNamespace(objects=objects))

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash")
    diagnostic = next(item for item in parsed.diagnostics if item.code == "component_typetree_unavailable")

    assert diagnostic.source.path_id == 3
    assert dict(diagnostic.details)["component_path_id"] == "14"


def test_real_prefab_parser_compatibility_contract_is_preserved(monkeypatch):
    from app.features.preview import prefab_parser

    expected = object()
    monkeypatch.setattr(unity_scene, "parse_unity_scene", lambda *_args: expected)
    assert prefab_parser.parse_unity_scene(Path("fixture.bundle"), "bundle-hash") is expected
    assert callable(prefab_parser.parse_prefab)
    assert callable(prefab_parser.compute_pixel_offset)
    assert prefab_parser.compute_pixel_offset({
        "hero": {"name": "hero", "type": "character", "pos": (1.0, 2.0)},
        "backdrop": {"name": "backdrop", "type": "background", "pos": (3.0, -1.0)},
    }) == {"bg_name": "backdrop", "pixel_offset": (200.0, -300.0), "ppu": 100.0}


def test_scene_parser_binds_selected_version_to_result(monkeypatch):
    monkeypatch.setattr(
        unity_scene, "_load_environment",
        lambda _path: SimpleNamespace(objects=_scene_objects()),
    )

    parsed = unity_scene.parse_unity_scene(Path("fixture.bundle"), "bundle-hash", 402)

    assert parsed.version_timestamp == 402
