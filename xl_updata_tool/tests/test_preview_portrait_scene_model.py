from dataclasses import replace

from app.features.preview.portrait_config.model import (
    AssetRef,
    InteractionEvent,
    PortraitScene,
    RendererState,
    ScenePart,
    SpineState,
    SpriteState,
)


def _scene() -> PortraitScene:
    spine_a = ScenePart(
        part_id="spine-main",
        object_ref=AssetRef("bundle-a", 10),
        game_object_name="Character",
        hierarchy_path="Root/Character",
        renderer=RendererState(kind="spine", enabled=True, sorting_layer="Default", sorting_order=2),
        spine=SpineState(
            skeleton=AssetRef("bundle-a", 20),
            atlas=AssetRef("bundle-a", 21),
            default_animation="idle",
            loop=True,
            initial_skin="default",
            animations=("idle", "touch"),
        ),
    )
    sprite = ScenePart(
        part_id="sprite-fx",
        object_ref=AssetRef("bundle-a", 30),
        game_object_name="Effect",
        hierarchy_path="Root/Effect",
        renderer=RendererState(kind="sprite", enabled=True, sorting_layer="Default", sorting_order=3),
        sprite=SpriteState(sprite=AssetRef("bundle-a", 31), rect=(0, 0, 64, 64), pivot=(0.5, 0.5)),
    )
    spine_b = ScenePart(
        part_id="spine-bg",
        object_ref=AssetRef("bundle-b", 10),
        game_object_name="Character",
        hierarchy_path="Root/Background",
        renderer=RendererState(kind="spine", enabled=True, sorting_layer="Default", sorting_order=1),
        spine=SpineState(skeleton=AssetRef("bundle-b", 20), default_animation="idle"),
    )
    return PortraitScene(
        game_version="402",
        version_timestamp="20261012",
        entry_config_id="12002183",
        character_id="10118",
        skin_id="12002183",
        prefab=AssetRef("bundle-a", 1),
        source_bundles=("bundle-a", "bundle-b"),
        parts=(spine_a, sprite, spine_b),
        available_assets=(
            AssetRef("bundle-a", 1), AssetRef("bundle-a", 10), AssetRef("bundle-a", 20),
            AssetRef("bundle-a", 21), AssetRef("bundle-a", 30), AssetRef("bundle-a", 31),
            AssetRef("bundle-b", 10), AssetRef("bundle-b", 20),
        ),
        interactions=(
            InteractionEvent(
                gesture="click",
                finger_count=1,
                target=AssetRef("bundle-a", 10),
                animation="touch",
                callback="CardTouch.lua",
                fallback="none",
            ),
        ),
    )


def test_scene_round_trip_preserves_spine_sprite_order_and_interactions():
    scene = _scene()

    restored = PortraitScene.from_dict(scene.to_dict())

    assert restored == scene
    assert [part.part_id for part in restored.parts] == ["spine-main", "sprite-fx", "spine-bg"]
    assert restored.parts[1].renderer.sorting_order == 3
    assert restored.interactions[0].animation == "touch"


def test_duplicate_game_object_names_keep_distinct_unity_component_identities():
    scene = _scene()

    assert scene.parts[0].game_object_name == scene.parts[2].game_object_name
    assert scene.parts[0].object_ref != scene.parts[2].object_ref
    assert scene.validate() == ()


def test_missing_referenced_asset_produces_deterministic_diagnostic():
    scene = _scene()
    broken_part = ScenePart(
        part_id="broken-spine",
        object_ref=AssetRef("bundle-a", 40),
        game_object_name="Broken",
        renderer=RendererState(kind="spine", enabled=True),
        spine=SpineState(skeleton=AssetRef("bundle-a", 404)),
    )
    broken = replace(
        scene,
        parts=(*scene.parts, broken_part),
        available_assets=(*scene.available_assets, broken_part.object_ref),
    )

    diagnostics = broken.validate()

    assert [(item.code, item.part_id) for item in diagnostics] == [("missing_asset", "broken-spine")]


def test_duplicate_unity_object_references_are_reported_in_stable_order():
    scene = _scene()
    duplicate = ScenePart(
        part_id="duplicate-component",
        object_ref=AssetRef("bundle-a", 10),
        game_object_name="Other label",
        renderer=RendererState(kind="spine", enabled=True),
        spine=SpineState(skeleton=AssetRef("bundle-a", 20)),
    )
    invalid = replace(scene, parts=(*scene.parts, duplicate))

    diagnostics = invalid.validate()

    assert [(item.code, item.part_id) for item in diagnostics] == [
        ("duplicate_object_ref", "duplicate-component")
    ]


def test_from_dict_reports_unknown_fields_at_each_nested_boundary_deterministically():
    data = _scene().to_dict()
    data["parts"][0]["extra_part"] = True
    data["parts"][0]["renderer"]["extra_renderer"] = True
    data["parts"][0]["spine"]["extra_spine"] = True
    data["parts"][1]["sprite"]["extra_sprite"] = True
    data["interactions"][0]["extra_interaction"] = True
    data["diagnostics"] = [{"code": "source", "extra_diagnostic": True}]

    first = PortraitScene.from_dict(data)
    second = PortraitScene.from_dict(data)

    nested = [item.details for item in first.diagnostics if item.code == "unknown_field"]
    assert nested == [
        (("boundary", "diagnostic"), ("field", "extra_diagnostic")),
        (("boundary", "interaction"), ("field", "extra_interaction")),
        (("boundary", "part"), ("field", "extra_part"), ("part_id", "spine-main")),
        (("boundary", "renderer"), ("field", "extra_renderer"), ("part_id", "spine-main")),
        (("boundary", "spine"), ("field", "extra_spine"), ("part_id", "spine-main")),
        (("boundary", "sprite"), ("field", "extra_sprite"), ("part_id", "sprite-fx")),
    ]
    assert first.diagnostics == second.diagnostics


def test_validate_requires_prefab_and_interaction_refs_in_asset_inventory():
    scene = _scene()
    interaction = replace(
        scene.interactions[0],
        target=AssetRef("bundle-a", 900),
        collider=AssetRef("bundle-a", 901),
    )
    invalid = replace(scene, prefab=AssetRef("bundle-a", 999), interactions=(interaction,))

    diagnostics = invalid.validate()

    assert [(item.code, item.source) for item in diagnostics] == [
        ("missing_asset", AssetRef("bundle-a", 999)),
        ("missing_interaction_target", AssetRef("bundle-a", 900)),
        ("missing_asset", AssetRef("bundle-a", 900)),
        ("missing_asset", AssetRef("bundle-a", 901)),
    ]
