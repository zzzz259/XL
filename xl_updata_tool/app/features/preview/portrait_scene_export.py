"""Resolve manifest Spine parts to exact extracted files for one scene export."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from .character_names import CharacterNameResolver, display_resource_family, display_skin_number
from .export_plan import ExportSettings
from .portrait_config.asset_index import AssetIndex
from .portrait_config.model import AssetRef, PortraitScene


@dataclass(frozen=True, slots=True)
class SpineSource:
    part_id: str
    skeleton_path: Path
    atlas_path: Path
    animation: str
    loop: bool
    skin: str
    time_scale: float
    unity_order: tuple[int, int, float, int]
    skeleton_ref: AssetRef | None = None
    atlas_refs: tuple[AssetRef, ...] = ()
    source_kind: str = "spine"
    sprite_ref: AssetRef | None = None
    texture_ref: AssetRef | None = None


@dataclass(frozen=True, slots=True)
class SpriteSource:
    part_id: str
    texture_path: Path
    rect: tuple[float, ...]
    pivot: tuple[float, ...]
    pixels_per_unit: float
    transform_chain: tuple
    color: tuple[float, ...]
    flip_x: bool
    flip_y: bool
    mask_interaction: int
    blend_mode: str
    unity_order: tuple[int, int, float, int]
    sprite_ref: AssetRef
    texture_ref: AssetRef


@dataclass(frozen=True, slots=True)
class PortraitSceneExportJob:
    scene: PortraitScene
    sources: tuple[SpineSource, ...]
    output_path: Path
    settings: ExportSettings
    export_mode: str = "builtin"
    sprite_sources: tuple[SpriteSource, ...] = ()

    @property
    def resource_family(self) -> str:
        return scene_resource_family(self.scene)


def prepare_portrait_scene_export_job(
    job: PortraitSceneExportJob, work_dir: str | Path,
) -> PortraitSceneExportJob:
    """Materialize static Sprite inputs and order all renderers for one CLI merge."""
    if not job.sprite_sources:
        return job
    from .portrait_config.static_layer_renderer import (
        StaticLayerSpec, generate_static_layer, order_render_sources,
    )

    generated_sources = []
    for source in job.sprite_sources:
        generated = generate_static_layer(StaticLayerSpec(
            part_id=source.part_id,
            texture_path=source.texture_path,
            rect=source.rect,
            pivot=source.pivot,
            pixels_per_unit=source.pixels_per_unit,
            transform_chain=source.transform_chain,
            unity_order=source.unity_order,
            color=source.color,
            flip_x=source.flip_x,
            flip_y=source.flip_y,
            mask_interaction=source.mask_interaction,
            blend_mode=source.blend_mode,
        ), work_dir)
        generated_sources.append(SpineSource(
            part_id=generated.part_id,
            skeleton_path=generated.skeleton_path,
            atlas_path=generated.atlas_path,
            animation="idle",
            loop=False,
            skin="default",
            time_scale=1.0,
            unity_order=generated.unity_order,
            source_kind="static_sprite",
            sprite_ref=source.sprite_ref,
            texture_ref=source.texture_ref,
        ))
    ordered = order_render_sources((*job.sources, *generated_sources))
    return replace(job, sources=ordered, sprite_sources=())


def _resolve_asset_path(index: AssetIndex, material_root: Path, reference: AssetRef) -> Path:
    matches = index.find_exact(reference.bundle_hash, reference.path_id)
    if not matches:
        raise ValueError(
            f"missing exact AssetStudio identity {reference.bundle_hash}:{reference.path_id}"
        )
    if len(matches) != 1:
        raise ValueError(
            f"ambiguous AssetStudio identity {reference.bundle_hash}:{reference.path_id}"
        )
    asset = matches[0]
    if asset.version_timestamp != index.version_timestamp:
        raise ValueError("AssetStudio identity belongs to another game version")
    if not asset.bundle_present:
        raise ValueError(f"source bundle is missing: {asset.bundle_hash}")
    if not asset.name or not asset.container:
        raise ValueError(f"AssetStudio identity has no export path: {reference}")
    same_export_path = tuple(
        item for item in index.assets
        if item.version_timestamp == index.version_timestamp
        and item.container.casefold() == asset.container.casefold()
        and item.name.casefold() == asset.name.casefold()
    )
    if len({(item.bundle_hash, item.path_id) for item in same_export_path}) > 1:
        raise ValueError(f"multiple Unity identities share one extracted output path: {asset.container}/{asset.name}")

    container = PurePosixPath(asset.container)
    asset_name = PurePosixPath(asset.name)
    if container.is_absolute() or asset_name.is_absolute() or ".." in container.parts or ".." in asset_name.parts:
        raise ValueError(f"unsafe AssetStudio export path for {reference}")
    relative = container.parent / asset_name
    root = material_root.resolve()
    candidate = (root / Path(*relative.parts)).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"AssetStudio export path escapes material root: {reference}")
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        raise ValueError(f"resolved AssetStudio output is missing or empty: {candidate}")
    return candidate


def build_portrait_scene_export_job(
    scene: PortraitScene,
    asset_index: AssetIndex,
    material_root: str | Path,
    output_dir: str | Path,
    settings: ExportSettings,
    *,
    export_mode: str = "builtin",
) -> PortraitSceneExportJob:
    """Build a single scene job from version-bound Unity references.

    The scene's renderer order is sent front-to-back because SpineViewerCLI's
    merge command draws its first skeleton in front of later skeletons.
    Unsupported mixed renderers or merge settings fail before process launch.
    """
    try:
        version_timestamp = int(scene.version_timestamp)
    except (TypeError, ValueError) as error:
        raise ValueError("portrait scene has no numeric version identity") from error
    if version_timestamp != asset_index.version_timestamp:
        raise ValueError(
            f"portrait scene version {version_timestamp} does not match AssetStudio index "
            f"{asset_index.version_timestamp}"
        )
    if export_mode not in {"builtin", "custom"}:
        raise ValueError(f"unsupported portrait scene export mode: {export_mode}")
    selected_skins = tuple(str(skin).strip() for skin in settings.skins if str(skin).strip())
    if export_mode == "custom" and len(selected_skins) != 1:
        raise ValueError("custom scene export requires exactly one internal skin per output")

    scene_errors = tuple(item for item in scene.validate() if item.severity == "error")
    if scene_errors:
        details = "; ".join(f"{item.code}: {item.message}" for item in scene_errors[:5])
        raise ValueError(f"portrait scene is incomplete: {details}")

    active_parts = tuple(
        part for part in scene.parts
        if part.renderer is None or part.renderer.enabled
    )
    sources = []
    sprite_sources = []
    for part in active_parts:
        spine = part.spine
        if spine is None and part.sprite is None:
            continue
        renderer = part.renderer
        unity_order = (
            int(part.sorting_layer_value),
            int(renderer.sorting_order if renderer else 0),
            float(part.renderer_depth),
            int(part.stable_order),
        )
        if spine is None:
            sprite = part.sprite
            if sprite.texture is None:
                raise ValueError(f"scene part {part.part_id} has no exact Sprite texture reference")
            sprite_sources.append(SpriteSource(
                part_id=part.part_id,
                texture_path=_resolve_asset_path(asset_index, Path(material_root), sprite.texture),
                rect=tuple(map(float, sprite.rect)),
                pivot=tuple(map(float, sprite.pivot)),
                pixels_per_unit=float(sprite.pixels_per_unit),
                transform_chain=part.transform_chain,
                color=tuple(map(float, renderer.color if renderer else (1.0, 1.0, 1.0, 1.0))),
                flip_x=bool(renderer.flip_x) if renderer else False,
                flip_y=bool(renderer.flip_y) if renderer else False,
                mask_interaction=int(renderer.mask_interaction) if renderer else 0,
                blend_mode=str(renderer.blend_mode if renderer else "normal"),
                unity_order=unity_order,
                sprite_ref=sprite.sprite,
                texture_ref=sprite.texture,
            ))
            continue
        if spine.skeleton_json is None:
            raise ValueError(f"scene part {part.part_id} has no exact Spine skeleton TextAsset reference")
        atlas_refs = tuple(spine.atlases or ((spine.atlas,) if spine.atlas else ()))
        if len(atlas_refs) != 1:
            raise ValueError(
                f"scene part {part.part_id} has {len(atlas_refs)} atlas references; exactly one is required"
            )
        animation = str(spine.default_animation or "").strip()
        if export_mode == "custom":
            animation = str(settings.animation or "").strip()
        if not animation:
            raise ValueError(f"scene part {part.part_id} has no configured animation")
        if spine.animations and animation not in spine.animations:
            raise ValueError(
                f"scene part {part.part_id} animation is unavailable: {animation}"
            )
        sources.append(SpineSource(
            part_id=part.part_id,
            skeleton_path=_resolve_asset_path(asset_index, Path(material_root), spine.skeleton_json),
            atlas_path=_resolve_asset_path(asset_index, Path(material_root), atlas_refs[0]),
            animation=animation,
            loop=bool(settings.loop) if export_mode == "custom" else bool(spine.loop),
            skin=(selected_skins[0] if export_mode == "custom" else str(spine.initial_skin or "").strip()),
            time_scale=float(spine.time_scale),
            unity_order=unity_order,
            skeleton_ref=spine.skeleton_json,
            atlas_refs=atlas_refs,
        ))
    if not sources:
        raise ValueError("portrait scene has no enabled Spine renderers")

    sources.sort(key=lambda source: source.unity_order, reverse=True)
    if len(sources) > 1:
        non_default_skins = [source.part_id for source in sources if source.skin.casefold() not in {"", "default"}]
        if non_default_skins:
            raise ValueError(
                "per-part skin selection is unsupported by SpineViewerCLI merge; scene parts: "
                + ", ".join(non_default_skins)
            )
        if len({source.loop for source in sources}) > 1:
            raise ValueError("SpineViewerCLI merge cannot represent different per-part loop settings")

    extension = str(settings.format).casefold()
    if extension not in {"png", "mp4", "gif"}:
        raise ValueError("Portrait scene export supports PNG, MP4, or GIF")
    family = scene_resource_family(scene)
    family_label = display_resource_family(family)
    skin_number = display_skin_number(scene.prefab_container, scene.character_id, family)
    output_stem = f"{scene.character_id or 'eventcovers'}_{family_label}_{skin_number}"
    output_stem = "".join("_" if char in '<>:"/\\|?*' else char for char in output_stem).strip(" .")
    if not output_stem:
        raise ValueError("portrait scene output name is empty after sanitization")
    output_path = Path(output_dir) / f"{output_stem}.{extension}"
    return PortraitSceneExportJob(
        scene, tuple(sources), output_path, settings, export_mode, tuple(sprite_sources),
    )


def scene_resource_family(scene: PortraitScene) -> str:
    """Classify a scene from its exact Prefab container, not loose filenames."""
    container = str(scene.prefab_container).casefold().replace("\\", "/")
    if "/eventcovers/" in container:
        return "eventcovers"
    if "/battlespine/" in container or "battlespine_" in container:
        return "battlespine"
    if "/cardspine/" in container or "cardspine_" in container:
        return "cardspine"
    return "spine"


def build_portrait_scene_export_jobs(
    scene: PortraitScene,
    asset_index: AssetIndex,
    material_root: str | Path,
    output_root: str | Path,
    name_resolver: CharacterNameResolver | None = None,
    settings: ExportSettings | None = None,
) -> tuple[PortraitSceneExportJob, ...]:
    """Create the scene's built-in output matrix, or one custom output job."""
    from .export_presets import default_export_settings

    family = scene_resource_family(scene)
    character_id = str(scene.character_id or "eventcovers")
    character_name = name_resolver.resolve(scene.character_id) if name_resolver else None
    directory_name = f"{character_id}_{character_name}" if character_name else character_id
    output_dir = Path(output_root) / directory_name
    if settings is not None:
        return (
            build_portrait_scene_export_job(
                scene, asset_index, material_root, output_dir, settings,
                export_mode="custom",
            ),
        )

    output_formats = ("Png", "Mp4") if family == "cardspine" else ("Png",)
    return tuple(
        build_portrait_scene_export_job(
            scene,
            asset_index,
            material_root,
            output_dir,
            default_export_settings(family, output_format),
        )
        for output_format in output_formats
    )
