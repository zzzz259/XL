from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.features.preview.export_plan import ExportSettings
from app.features.preview.portrait_config.asset_index import AssetIndex
from app.features.preview.portrait_config.model import (
    AssetRef,
    PortraitScene,
    RendererState,
    ScenePart,
    SpineState,
    SpriteState,
    TransformState,
)
from app.features.preview.portrait_scene_export import (
    build_portrait_scene_export_job,
    build_portrait_scene_export_jobs,
    prepare_portrait_scene_export_job,
)
from app.features.preview.spine_adapter import build_spine_export_command


VERSION = 134351472190013976
BUNDLE_HASH = "a" * 32


def _scene(parts):
    refs = tuple(
        ref
        for part in parts
        for ref in (
            part.object_ref,
            *(node.object_ref for node in part.transform_chain),
            *((part.spine.skeleton_json, *part.spine.atlases) if part.spine else ()),
            *((part.sprite.sprite, part.sprite.texture) if part.sprite else ()),
        )
        if ref is not None
    )
    return PortraitScene(
        game_version="vA160",
        version_timestamp=str(VERSION),
        entry_config_id="12002183",
        character_id="10118",
        skin_id="12002183",
        prefab=None,
        source_bundles=(BUNDLE_HASH,),
        parts=tuple(parts),
        available_assets=refs,
        prefab_container="assets/art/models/cardspine/prefab/cardspine_10118_3.prefab",
    )


def _part(part_id, stem, *, order, animation, skin="", loop=False, path_start=1):
    skeleton = AssetRef(BUNDLE_HASH, path_start)
    atlas = AssetRef(BUNDLE_HASH, path_start + 1)
    return ScenePart(
        part_id=part_id,
        object_ref=AssetRef(BUNDLE_HASH, path_start + 2),
        game_object_name=stem,
        renderer=RendererState("Spine", True, sorting_order=order),
        spine=SpineState(
            skeleton=skeleton,
            default_animation=animation,
            animations=tuple(dict.fromkeys((animation, "idle", "bloom", "hover"))),
            skins=("default", "motion_stander", "alternate"),
            loop=loop,
            initial_skin=skin,
            skeleton_json=skeleton,
            atlases=(atlas,),
        ),
        sorting_layer_value=0,
        renderer_depth=float(order),
        stable_order=path_start,
    )


def _index(tmp_path, parts):
    bundle_root = tmp_path / "bundles" / str(VERSION)
    bundle_root.mkdir(parents=True)
    (bundle_root / f"{BUNDLE_HASH}.bundle").write_bytes(b"bundle")
    records = []
    material_root = tmp_path / "material"
    for part in parts:
        stem = part.game_object_name
        container = f"assets/art/models/cardspine/prefab/{stem}.prefab"
        for ref, name in (
            *(((part.spine.skeleton_json, f"{stem}.skel"),
               (part.spine.atlases[0], f"{stem}.atlas")) if part.spine else ()),
            *(((part.sprite.texture, f"{stem}.png"),) if part.sprite else ()),
        ):
            records.append({
                "Name": name,
                "Container": container,
                "Source": f"{BUNDLE_HASH}.bundle",
                "PathID": ref.path_id,
                "Type": "TextAsset",
            })
            output = material_root / container.rsplit("/", 1)[0] / name
            output.parent.mkdir(parents=True, exist_ok=True)
            if name.endswith(".png"):
                from PIL import Image
                Image.new("RGBA", (8, 8), (1, 2, 3, 0)).save(output)
            else:
                output.write_bytes(name.encode())
    catalog = tmp_path / "assets_map.json"
    catalog.write_text(json.dumps(records), encoding="utf-8")
    return AssetIndex.from_catalog(catalog, bundle_root, VERSION), material_root


def test_scene_export_resolves_exact_refs_and_builds_one_ordered_merge(tmp_path):
    character = _part("character", "cardspine_10118_3", order=4, animation="idle", path_start=1)
    background = _part("background", "cardspine_10118_3_bg", order=2, animation="bloom", path_start=4)
    scene = _scene((background, character))
    index, material_root = _index(tmp_path, (character, background))

    job = build_portrait_scene_export_job(
        scene,
        index,
        material_root,
        tmp_path / "output",
        ExportSettings(),
    )

    assert [source.part_id for source in job.sources] == ["character", "background"]
    assert job.sources[0].animation == "idle"
    assert job.sources[1].animation == "bloom"
    assert job.sources[0].skeleton_path.name == "cardspine_10118_3.skel"
    assert job.sources[1].atlas_path.name == "cardspine_10118_3_bg.atlas"
    command = build_spine_export_command(job, "SpineViewerCLI.exe")
    assert command[1] == "merge"
    assert command[2:4] == [str(source.skeleton_path) for source in job.sources]
    assert command[command.index("--animations") + 1] == "idle/bloom"


def test_scene_export_three_parts_preserves_front_to_back_order_and_distinct_animations(tmp_path):
    front = _part("front", "cardspine_10118_front", order=8, animation="gesture", path_start=1)
    middle = _part("middle", "cardspine_10118_middle", order=4, animation="idle", path_start=4)
    back = _part("back", "cardspine_10118_back", order=1, animation="bloom", path_start=7)
    scene = _scene((back, middle, front))
    index, material_root = _index(tmp_path, (front, middle, back))

    job = build_portrait_scene_export_job(
        scene, index, material_root, tmp_path / "output", ExportSettings(),
    )
    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert [source.part_id for source in job.sources] == ["front", "middle", "back"]
    assert command[2:5] == [str(source.skeleton_path) for source in job.sources]
    assert command[command.index("--animations") + 1] == "gesture/idle/bloom"
    assert len([arg for arg in command if arg.startswith("--atlases=")]) == 3


def test_scene_export_interleaves_sprite_and_spine_in_single_merge(tmp_path):
    front = _part("front", "cardspine_10118_front", order=4, animation="idle", path_start=1)
    back = _part("back", "cardspine_10118_back", order=2, animation="bloom", path_start=4)
    sprite = ScenePart(
        part_id="sprite:middle",
        object_ref=AssetRef(BUNDLE_HASH, 20),
        game_object_name="static-middle",
        renderer=RendererState("SpriteRenderer", True, sorting_order=3),
        sprite=SpriteState(
            sprite=AssetRef(BUNDLE_HASH, 21),
            rect=(0, 0, 8, 8),
            pivot=(0.5, 0.5),
            texture=AssetRef(BUNDLE_HASH, 22),
            pixels_per_unit=100.0,
        ),
        transform_chain=(TransformState(
            AssetRef(BUNDLE_HASH, 23), (0.5, 1.0, 0.0), (0, 0, 0, 1), (1, 1, 1), 1,
        ),),
        renderer_depth=3.0,
        stable_order=1,
    )
    scene = _scene((back, sprite, front))
    index, material_root = _index(tmp_path, (front, back, sprite))

    job = build_portrait_scene_export_job(scene, index, material_root, tmp_path / "output", ExportSettings())
    prepared = prepare_portrait_scene_export_job(job, tmp_path / "temp")
    command = build_spine_export_command(prepared, "SpineViewerCLI.exe")

    assert [source.part_id for source in prepared.sources] == ["front", "sprite:middle", "back"]
    assert command[1] == "merge"
    assert len(command[2:5]) == 3
    assert command[command.index("--animations") + 1] == "idle/idle/bloom"
    assert all(Path(source).is_file() for source in command[2:5])
    assert len([arg for arg in command if arg.startswith("--atlases=")]) == 3


def test_scene_export_rejects_masked_sprite_with_structured_diagnostic(tmp_path):
    character = _part("character", "cardspine_10118_3", order=2, animation="idle")
    sprite = ScenePart(
        part_id="masked",
        object_ref=AssetRef(BUNDLE_HASH, 30),
        game_object_name="masked",
        renderer=RendererState("SpriteRenderer", True, mask_interaction=1),
        sprite=SpriteState(AssetRef(BUNDLE_HASH, 31), (0, 0, 8, 8), (0.5, 0.5), AssetRef(BUNDLE_HASH, 32)),
    )
    scene = _scene((character, sprite))
    index, material_root = _index(tmp_path, (character, sprite))

    with pytest.raises(ValueError, match="unsupported_sprite_mask"):
        build_portrait_scene_export_job(scene, index, material_root, tmp_path / "output", ExportSettings())


def test_scene_export_rejects_unsupported_spine_blend_before_cli(tmp_path):
    part = _part("character", "eventcovers_0035", order=1, animation="in")
    part = replace(part, renderer=replace(part.renderer, blend_mode="unsupported"))
    scene = replace(
        _scene((part,)), character_id="", skin_id="eventcovers_0035",
        prefab_container="assets/art/models/ui_spine/prefab/eventcovers/eventcovers_0035.prefab",
    )
    index, material_root = _index(tmp_path, (part,))

    with pytest.raises(ValueError, match="unsupported_spine_blend"):
        build_portrait_scene_export_job(
            scene, index, material_root, tmp_path / "output", ExportSettings()
        )


def test_scene_export_rejects_duplicate_exact_identity_mapping(tmp_path):
    part = _part("character", "cardspine_10118_3", order=4, animation="idle")
    scene = _scene((part,))
    index, material_root = _index(tmp_path, (part,))
    asset = index.find_exact(BUNDLE_HASH, part.spine.skeleton_json.path_id)[0]
    duplicate = replace(asset, name="other.skel")
    index = replace(index, assets=(*index.assets, duplicate))

    with pytest.raises(ValueError, match="ambiguous"):
        build_portrait_scene_export_job(
            scene, index, material_root, tmp_path / "output", ExportSettings()
        )


def test_scene_export_rejects_per_part_skin_not_supported_by_merge(tmp_path):
    character = _part("character", "cardspine_10118_3", order=4, animation="idle", skin="dress")
    background = _part("background", "cardspine_10118_3_bg", order=2, animation="idle", path_start=4)
    scene = _scene((character, background))
    index, material_root = _index(tmp_path, (character, background))

    with pytest.raises(ValueError, match="skin.*merge"):
        build_portrait_scene_export_job(
            scene, index, material_root, tmp_path / "output", ExportSettings()
        )


def test_builtin_scene_plan_keeps_cardspine_static_and_video_together(tmp_path):
    from app.features.preview.portrait_scene_export import build_portrait_scene_export_jobs

    part = _part("character", "cardspine_10118_3", order=4, animation="idle")
    scene = _scene((part,))
    index, material_root = _index(tmp_path, (part,))

    jobs = build_portrait_scene_export_jobs(scene, index, material_root, tmp_path / "output")

    assert [job.settings.format for job in jobs] == ["Png", "Mp4"]
    assert [job.output_path.name for job in jobs] == [
        "10118_角色立绘_3.png", "10118_角色立绘_3.mp4",
    ]
    assert all(job.sources[0].animation == "idle" for job in jobs)


def test_configured_eventcover_exports_one_merged_cover(tmp_path):
    backdrop = _part("backdrop", "eventcovers_0005_4", order=1, animation="idle", path_start=1)
    foreground = _part("foreground", "eventcovers_0005_1", order=0, animation="idle", path_start=4)
    scene = replace(
        _scene((foreground, backdrop)), character_id="", skin_id="eventcovers_0005",
        prefab_container="assets/art/models/ui_spine/prefab/eventcovers/eventcovers_0005.prefab",
    )
    index, material_root = _index(tmp_path, (foreground, backdrop))

    jobs = build_portrait_scene_export_jobs(scene, index, material_root, tmp_path / "output")

    assert [job.output_path.name for job in jobs] == ["eventcovers_活动封面_0005.png"]
    assert [source.skeleton_path.name for source in jobs[0].sources] == [
        "eventcovers_0005_4.skel", "eventcovers_0005_1.skel",
    ]


def test_builtin_scene_uses_preset_animation_when_one_part_has_no_prefab_animation(tmp_path):
    from app.features.preview.portrait_scene_export import build_portrait_scene_export_jobs

    character = _part("character", "cardspine_10124_3", order=2, animation="idle", path_start=1)
    background = _part("background", "cardspine_10124_3_bg", order=1, animation="", path_start=4)
    scene = _scene((character, background))
    index, material_root = _index(tmp_path, (character, background))

    jobs = build_portrait_scene_export_jobs(scene, index, material_root, tmp_path / "output")

    assert [job.settings.format for job in jobs] == ["Png", "Mp4"]
    assert [[source.animation for source in job.sources] for job in jobs] == [
        ["idle", "idle"],
        ["idle", "idle"],
    ]


def test_scene_export_rejects_renderer_animation_missing_from_skeleton(tmp_path):
    part = _part("character", "cardspine_10118_3", order=4, animation="missing")
    part = replace(part, spine=replace(part.spine, animations=("idle", "hover")))
    scene = _scene((part,))
    index, material_root = _index(tmp_path, (part,))

    with pytest.raises(ValueError, match="animation is unavailable"):
        build_portrait_scene_export_job(
            scene, index, material_root, tmp_path / "output", ExportSettings()
        )


def test_builtin_battlespine_scene_selects_motion_stander_for_single_skeleton(tmp_path):
    from app.features.preview.portrait_scene_export import build_portrait_scene_export_jobs

    part = _part("battle", "battlespine_10118_2", order=4, animation="idle")
    scene = replace(
        _scene((part,)),
        prefab_container="assets/art/models/battlespine/prefab/battlespine_10118_2.prefab",
    )
    index, material_root = _index(tmp_path, (part,))

    job = build_portrait_scene_export_jobs(scene, index, material_root, tmp_path / "output")[0]
    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert job.settings.scale == 1
    assert job.settings.format == "Png"
    assert command[command.index("--skins") + 1] == "motion_stander"


def test_custom_scene_settings_override_manifest_animation_skin_and_loop(tmp_path):
    part = _part("battle", "battlespine_10118_2", order=4, animation="idle", skin="motion_stander")
    scene = replace(
        _scene((part,)),
        prefab_container="assets/art/models/battlespine/prefab/battlespine_10118_2.prefab",
    )
    index, material_root = _index(tmp_path, (part,))

    job = build_portrait_scene_export_job(
        scene,
        index,
        material_root,
        tmp_path / "output",
        ExportSettings(
            animation="hover", loop=True, static=False, format="Mp4",
            skins=("alternate",),
        ),
        export_mode="custom",
    )
    command = build_spine_export_command(job, "SpineViewerCLI.exe")

    assert job.sources[0].animation == "hover"
    assert job.sources[0].skin == "alternate"
    assert job.sources[0].loop is True
    assert command[command.index("--animations") + 1] == "hover"
    assert command[command.index("--skins") + 1] == "alternate"
    assert "--loop" in command


def test_custom_scene_rejects_multiple_internal_skins_for_one_output(tmp_path):
    part = _part("battle", "battlespine_10118_2", order=4, animation="idle")
    scene = replace(
        _scene((part,)),
        prefab_container="assets/art/models/battlespine/prefab/battlespine_10118_2.prefab",
    )
    index, material_root = _index(tmp_path, (part,))

    with pytest.raises(ValueError, match="exactly one internal skin"):
        build_portrait_scene_export_job(
            scene,
            index,
            material_root,
            tmp_path / "output",
            ExportSettings(skins=("default", "alternate")),
            export_mode="custom",
        )
