"""Resolve manifest Spine parts to exact extracted files for one scene export."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
import hashlib
import json

from .character_names import CharacterNameResolver, display_resource_family, display_skin_number
from .export_plan import ExportSettings
from .portrait_config.asset_index import AssetIndex
from .portrait_config.model import AnimationStep, AssetRef, PortraitScene
from .portrait_config.scene_geometry import part_affine, scene_origin


@dataclass(frozen=True, slots=True)
class AnimationTrack:
    track: int
    sequence: tuple[AnimationStep, ...]


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
    affine: tuple[float, ...] = (1., 0., 0., 1., 0., 0.)
    color: tuple[float, ...] = (1., 1., 1., 1.)
    sequence: tuple[AnimationStep, ...] = ()
    fade_in: float = 0.0
    tracks: tuple[AnimationTrack, ...] = ()


@dataclass(frozen=True, slots=True)
class ParticleSource:
    part_id: str
    texture_path: Path
    profile: str
    config: dict
    affine: tuple[float, ...]
    unity_order: tuple[int, int, float, int]
    system_ref: AssetRef | None = None
    material_ref: AssetRef | None = None
    texture_ref: AssetRef | None = None
    color: tuple[float, ...] = (1., 1., 1., 1.)


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
    particle_sources: tuple[ParticleSource, ...] = ()
    interaction_id: str = ''
    render_scope: str = 'full'

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

    origin = scene_origin(job.scene.parts)
    generated_sources = []
    for source in job.sprite_sources:
        generated = generate_static_layer(StaticLayerSpec(
            part_id=source.part_id,
            texture_path=source.texture_path,
            rect=source.rect,
            pivot=source.pivot,
            pixels_per_unit=source.pixels_per_unit,
            transform_chain=(),
            unity_order=source.unity_order,
            color=source.color,
            flip_x=source.flip_x,
            flip_y=source.flip_y,
            mask_interaction=source.mask_interaction,
            blend_mode=source.blend_mode,
            spine_transform_chain=(),
            skeleton_scale=0.01,
            pma=bool(job.settings.pma),
        ), work_dir)
        generated_sources.append(SpineSource(
            part_id=generated.part_id,
            skeleton_path=generated.skeleton_path,
            atlas_path=generated.atlas_path,
            animation="idle",
            # A zero-duration static layer is invariant under the scene clock.
            # Inherit it so it doesn't manufacture a merge loop/speed conflict.
            loop=job.sources[0].loop if job.sources else False,
            skin="default",
            time_scale=job.sources[0].time_scale if job.sources else 1.0,
            unity_order=generated.unity_order,
            source_kind="static_sprite",
            sprite_ref=source.sprite_ref,
            texture_ref=source.texture_ref,
            affine=part_affine(source.transform_chain, origin=origin),
            fade_in=float(dict(job.scene.config_metadata).get('runtime_fade_in_seconds', '0'))
                    if job.export_mode == 'builtin' else 0.0,
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
    from .portrait_config.extracted_paths import identity_output_path
    root = material_root.resolve()
    exact = (root / Path(*identity_output_path(asset).parts)).resolve()
    if root not in exact.parents:
        raise ValueError(f"exact Unity asset path escapes material root: {reference}")
    if exact.is_file() and exact.stat().st_size > 0:
        return exact
    same_export_path = tuple(
        item for item in index.assets
        if item.version_timestamp == index.version_timestamp
        and item.container.casefold() == asset.container.casefold()
        and item.name.casefold() == asset.name.casefold()
        and (not item.asset_type or not asset.asset_type
             or item.asset_type.casefold() == asset.asset_type.casefold())
    )
    if len({(item.bundle_hash, item.path_id) for item in same_export_path}) > 1:
        raise ValueError(f"multiple Unity identities share one extracted output path: {asset.container}/{asset.name}")

    container = PurePosixPath(asset.container)
    asset_name = PurePosixPath(asset.name)
    if container.is_absolute() or asset_name.is_absolute() or ".." in container.parts or ".." in asset_name.parts:
        raise ValueError(f"unsafe AssetStudio export path for {reference}")
    root = material_root.resolve()
    names = [asset_name]
    # The map records Unity names, while Convert exports physical encodings.
    # Keep the exact identity/container; only account for AS transport suffixes.
    if asset.asset_type.casefold() == "texture2d" and asset_name.suffix.casefold() != ".png":
        names.append(PurePosixPath(str(asset_name) + ".png"))
    elif asset.asset_type.casefold() == "textasset" and asset_name.suffix.casefold() in {".skel", ".atlas"}:
        names.extend(PurePosixPath(str(asset_name) + suffix) for suffix in (".prefab", ".bytes", ".txt"))
    candidates = []
    for name in names:
        relative = container.parent / name
        candidate = (root / Path(*relative.parts)).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"AssetStudio export path escapes material root: {reference}")
        if candidate.is_file() and candidate.stat().st_size > 0:
            candidates.append(candidate)
    if len(candidates) > 1:
        raise ValueError(f"ambiguous AssetStudio physical outputs for {reference}: {candidates}")
    if not candidates:
        raise ValueError(f"resolved AssetStudio output is missing or empty: {container.parent / asset_name}")
    return candidates[0]


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

    from .scene_capabilities import scene_render_diagnostics
    scene_errors = tuple(item for item in scene_render_diagnostics(scene) if item.severity == "error")
    if scene_errors:
        details = "; ".join(f"{item.code}: {item.message}" for item in scene_errors[:5])
        raise ValueError(f"portrait scene is incomplete: {details}")

    active_parts = tuple(
        part for part in scene.parts
        if part.renderer is None or part.renderer.enabled
    )
    sources = []
    sprite_sources = []
    particle_sources = []
    origin = scene_origin(active_parts)
    for part in active_parts:
        spine = part.spine
        if spine is None and part.sprite is None and part.particle is None:
            continue
        renderer = part.renderer
        unity_order = (
            int(part.sorting_layer_value),
            int(renderer.sorting_order if renderer else 0),
            float(part.renderer_depth),
            int(part.stable_order),
        )
        if part.particle is not None:
            particle = part.particle
            if particle.texture is None or particle.profile not in {'additive_v1', 'alpha_tint_v1'}:
                raise ValueError(f'unsupported or missing particle material: {part.part_id}')
            particle_sources.append(ParticleSource(
                part.part_id, _resolve_asset_path(asset_index, Path(material_root), particle.texture),
                particle.profile, json.loads(particle.config_json),
                part_affine(part.transform_chain, 1.0, origin=origin), unity_order,
                particle.system, particle.material, particle.texture,
                tuple(renderer.color) if renderer else (1., 1., 1., 1.),
            ))
            continue
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
        apply_animation = export_mode == "custom" and (
            not spine.animations or settings.animation in spine.animations
        )
        if apply_animation:
            animation = str(settings.animation or "").strip()
        elif not animation:
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
            loop=bool(settings.loop) if apply_animation else bool(spine.loop),
            skin=(selected_skins[0] if export_mode == "custom" and (
                not spine.skins or selected_skins[0] in spine.skins
            ) else (
                str(spine.initial_skin or "").strip()
                or (next((s for s in selected_skins if s.casefold() != 'default'), '')
                    if export_mode == 'builtin' else '')
            )),
            time_scale=float(spine.time_scale),
            unity_order=unity_order,
            skeleton_ref=spine.skeleton_json,
            atlas_refs=atlas_refs,
            affine=part_affine(part.transform_chain, spine.skeleton_scale, origin=origin),
            color=tuple(renderer.color) if renderer else (1., 1., 1., 1.),
            sequence=() if apply_animation else spine.animation_sequence,
            fade_in=float(dict(scene.config_metadata).get('runtime_fade_in_seconds', '0'))
                    if export_mode == 'builtin' else 0.0,
        ))
    if not sources and not sprite_sources and not particle_sources:
        raise ValueError("portrait scene has no enabled renderers")

    sources.sort(key=lambda source: source.unity_order, reverse=True)

    extension = str(settings.format).casefold()
    if extension not in {"png", "mp4", "gif"}:
        raise ValueError("Portrait scene export supports PNG, MP4, or GIF")
    family = scene_resource_family(scene)
    family_label = display_resource_family(family)
    skin_number = display_skin_number(scene.prefab_container, scene.character_id, family)
    output_stem = (
        f"{scene.character_id or 'eventcovers'}_{family_label}_{skin_number}"
        f"_{scene.skin_id}_v{version_timestamp}"
    )
    output_stem = "".join("_" if char in '<>:"/\\|?*' else char for char in output_stem).strip(" .")
    if not output_stem:
        raise ValueError("portrait scene output name is empty after sanitization")
    output_path = Path(output_dir) / f"{output_stem}.{extension}"
    return PortraitSceneExportJob(
        scene, tuple(sources), output_path, settings, export_mode, tuple(sprite_sources),
        tuple(particle_sources),
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
    options: dict | None = None,
) -> tuple[PortraitSceneExportJob, ...]:
    """Create the scene's built-in output matrix, or one custom output job."""
    from .export_presets import default_export_settings
    from .scene_capabilities import portrait_scene_capabilities

    options = options or {}
    include_effects = options.get('include_effects', True)
    basic = not include_effects
    original_scene = scene
    capabilities = portrait_scene_capabilities(scene)
    if basic:
        if not options.get('allow_basic', False) or not capabilities.can_export_basic:
            raise ValueError('basic scene export requires explicit opt-in and complete base resources')
        base_parts = tuple(p for p in scene.parts if p.spine or p.sprite)
        base_ids = {p.part_id for p in base_parts}
        scene = replace(scene, parts=base_parts, diagnostics=tuple(
            note for note in scene.diagnostics if note.severity != 'error' or note.part_id in base_ids
        ))
    elif not capabilities.can_export:
        raise ValueError(f'portrait scene cannot be fully exported: {capabilities.label}: {capabilities.reason}')

    def finalize(job):
        if not basic:
            return job
        return replace(job, scene=original_scene, render_scope='basic',
                       output_path=job.output_path.with_stem(job.output_path.stem + '_basic'))

    family = scene_resource_family(scene)
    character_id = str(scene.character_id or "eventcovers")
    character_name = name_resolver.resolve(scene.character_id) if name_resolver else None
    directory_name = f"{character_id}_{character_name}" if character_name else character_id
    output_dir = Path(output_root) / directory_name
    if settings is not None:
        return (
            finalize(build_portrait_scene_export_job(
                scene, asset_index, material_root, output_dir, settings,
                export_mode="custom",
            )),
        )

    entrance = dict(scene.config_metadata).get('runtime_purpose') in {'entrance_only', 'entrance_stage'}
    output_formats = ("Png", "Mp4") if family == "cardspine" or entrance else ("Png",)
    jobs = [
        finalize(build_portrait_scene_export_job(
            scene,
            asset_index,
            material_root,
            output_dir,
            default_export_settings(family, output_format, entrance=entrance),
        ))
        for output_format in output_formats
    ]
    if options.get('include_interactions', True):
        groups = {}
        for event in scene.interactions:
            if not event.evidence or not event.sequence or event.target is None:
                continue
            key = (event.gesture, event.finger_count, event.callback, event.animation, event.track, event.collider)
            groups.setdefault(key, []).append(event)
        for key, events in groups.items():
            job = finalize(build_portrait_scene_export_job(
                scene, asset_index, material_root, output_dir,
                default_export_settings(family, 'Mp4'),
            ))
            by_target = {event.target: event for event in events}
            changed, bound = [], False
            for source in job.sources:
                part = next(p for p in scene.parts if p.part_id == source.part_id)
                event = by_target.get(part.object_ref)
                if event is None:
                    changed.append(source)
                    continue
                if part.spine.animations and any(step.animation not in part.spine.animations for step in event.sequence):
                    raise ValueError(f'configured interaction animation unavailable: {part.part_id}')
                bound = True
                if event.track == 0:
                    changed.append(replace(source, animation=event.sequence[0].animation,
                        loop=event.sequence[0].loop, sequence=event.sequence))
                else:
                    changed.append(replace(source, tracks=(AnimationTrack(event.track, event.sequence),)))
            if bound:
                identity = hashlib.sha256(repr(key).encode()).hexdigest()[:10]
                jobs.append(replace(job, sources=tuple(changed), interaction_id=identity,
                    output_path=job.output_path.with_stem(job.output_path.stem + '_interaction_' + identity)))
    return tuple(jobs)
