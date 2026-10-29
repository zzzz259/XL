"""Build normalized, version-bound portrait scenes from Lua roots and Prefabs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from collections.abc import Callable

from .asset_index import AssetIndex, IndexedAsset
from .cache import PortraitSceneCache, file_sha256
from .dependency_resolver import ResolvedScene, ResolvedReference, resolve_scene_assets
from .lua_config import PortraitRoot
from .event_runtime import apply_event_runtime, runtime_diagnostics
from .model import (
    AssetRef,
    AssetDependency,
    InteractionEvent,
    PortraitScene,
    RendererState,
    SceneDiagnostic,
    ScenePart,
    SpineState,
    SourceBundle,
    SpriteState,
    TransformState,
)
from .unity_scene import UnitySceneParseResult, UnityScenePart, parse_unity_scene

PARSER_VERSION = "13"

# Confirmed by the user as Japan-only entries retained in the CN Lua tables.
# If an exact resource becomes available in a later version it is used normally.
_KNOWN_NON_CN_PREFABS = frozenset({
    *(f"cardspine_10099_{i}.prefab" for i in (1, 2, 3, 5)),
    *(f"cardspine_10100_{i}.prefab" for i in (1, 2, 3)),
    "cardspine_10102_4.prefab", "cardspine_10103_4.prefab", "eventcovers_0101.prefab",
})


def _metadata(root: PortraitRoot) -> dict[str, str]:
    return {
        **dict(root.metadata),
        "configuration_evidence": root.evidence,
        "configured_prefab": str(root.prefab_name or ""),
        **{f"field_evidence.{key}": value for key, value in root.field_evidence},
    }


def _root_config_hash(
    root: PortraitRoot, prefab_hash: str, container_assets: tuple[IndexedAsset, ...] = (),
) -> str:
    # Hash this parsed root rather than its whole Lua source file: one unrelated
    # skin entry changing must not invalidate every skin declared in that file.
    from .interaction_metadata import source_fingerprints
    payload = {
        "interaction_sources": source_fingerprints(root),
        "skin_id": root.skin_id,
        "version_timestamp": root.version_timestamp,
        "prefab_name": root.prefab_name,
        "position": root.position,
        "metadata": root.metadata,
        "unresolved_reason": root.unresolved_reason,
        "event_runtime": asdict(root.event_runtime) if root.event_runtime else None,
        "container_assets": [
            (item.name, item.container, item.path_id, item.asset_type)
            for item in sorted(container_assets, key=lambda asset: (
                asset.name.casefold(), asset.path_id, asset.asset_type,
            ))
        ],
    }
    config_digest = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return hashlib.sha256(
        f"{root.version_timestamp}\0{config_digest}\0{prefab_hash}".encode("utf-8")
    ).hexdigest()


def _container_part_refs(part: UnityScenePart) -> set[AssetRef]:
    if part.component_type == "SkeletonAnimation":
        # Atlas assets may be shared by otherwise unrelated SkeletonData; the
        # skeleton JSON is the identity-bearing reference for this Spine part.
        candidates = {part.skeleton_json_ref}
    elif part.component_type == "SpriteRenderer":
        # A texture can back many Sprites/Prefabs. Match the actual Sprite.
        candidates = {part.sprite_ref}
    else:
        # Materials are routinely shared; Mesh is the renderer's stable asset.
        candidates = {part.mesh_ref}
    refs = {ref for ref in candidates if ref is not None}
    return refs


def _scope_to_container(
    parsed: UnitySceneParseResult, assets: tuple[IndexedAsset, ...], index: AssetIndex,
    *, prefab_name: str = "", prefab_ref: AssetRef | None = None,
) -> UnitySceneParseResult:
    """Limit a whole-bundle parse to renderers evidenced by one AS Container."""
    listed = {AssetRef(asset.bundle_hash, asset.path_id) for asset in assets}
    game_objects = {ref for ref, _ in parsed.game_objects} | {ref for ref, _ in parsed.object_ancestry}
    # Unity's m_Container can list TextAssets/textures under the same Prefab
    # path. Only GameObjects can establish hierarchy ownership.
    container_roots = tuple((name, ref) for name, ref in parsed.container_roots if ref in game_objects)
    containers = {a.container.casefold() for a in assets}
    roots = {ref for name, ref in container_roots if name in containers}
    if not roots and prefab_ref is not None:
        roots = {ref for _, ref in container_roots if ref == prefab_ref}
    if not roots and prefab_name:
        configured = prefab_name.replace("\\", "/").strip("/").casefold()
        roots = {ref for name, ref in container_roots
                 if (name == configured if "/" in configured else name.rsplit("/", 1)[-1] == configured)}
    if parsed.container_roots and len(roots) != 1:
        diagnostic = SceneDiagnostic(
            "ambiguous_prefab_root" if roots else "prefab_root_unresolved",
            "Configured Prefab has no unique AssetBundle container root",
            details=(("prefab_name", prefab_name), ("root_matches", str(len(roots)))),
        )
        return replace(parsed, parts=(), interactions=(), references=(), game_objects=(),
                       component_refs=(), diagnostics=(diagnostic,))
    ancestry = dict(parsed.object_ancestry)
    owned = {go for go, chain in ancestry.items() if roots.intersection(chain)}
    parts = tuple(part for part in parsed.parts if
                  (part.game_object_ref in owned if roots else bool(_container_part_refs(part) & listed)))
    selected_objects = {
        ref for part in parts
        for ref in (part.component_ref, part.game_object_ref,
                    *(node.component_ref for node in part.transform_chain))
    }
    selected_objects.update(owned)
    selected_objects.update(ref for ref, owner, _ in parsed.component_refs if owner in owned)
    # Follow local PPtrs from the selected renderers (e.g. SkeletonAnimation ->
    # SkeletonDataAsset -> skeleton JSON/atlas) without importing pointers from
    # other Prefabs that happen to share the same bundle.
    pointers_by_source: dict[AssetRef, list] = {}
    for pointer in parsed.references:
        pointers_by_source.setdefault(pointer.source, []).append(pointer)
    scoped_refs = []
    seen_pointers = set()
    traversal_sources = {
        ref for part in parts
        for ref in (part.component_ref, part.renderer_ref, part.skeleton_ref, part.sprite_ref)
        if ref is not None
    }
    scoped_interactions = tuple(
        item for item in parsed.interactions
        if item.game_object_ref in selected_objects
    )
    traversal_sources.update(item.component_ref for item in scoped_interactions)
    known_sources = set(traversal_sources) | selected_objects
    pending_sources = list(traversal_sources)
    while pending_sources:
        source = pending_sources.pop()
        for pointer in pointers_by_source.get(source, ()):
            field = pointer.field_path.casefold()
            if not any(token in field for token in (
                "skeletondataasset", "skeletonjson", "atlasassets", "atlasfile", "m_materials",
                "m_mesh", "m_sprite", "texture", "collider",
            )):
                continue
            if pointer in seen_pointers:
                continue
            seen_pointers.add(pointer)
            scoped_refs.append(pointer)
            if pointer.file_id == 0:
                target = AssetRef(parsed.bundle_hash, pointer.path_id)
                if target not in known_sources:
                    known_sources.add(target)
                    pending_sources.append(target)
    interactions = scoped_interactions
    diagnostics = tuple(
        item for item in parsed.diagnostics
        if item.source is None or item.source in known_sources
    )
    game_objects = tuple(item for item in parsed.game_objects if item[0] in selected_objects)
    component_refs = tuple(item for item in parsed.component_refs
                           if item[0] in known_sources or item[1] in selected_objects)
    # AssetStudio's export map contains exportable resources, not every Unity
    # GameObject/Transform/MonoBehaviour. Keep only PPtrs that the selected
    # version catalog can actually resolve (plus external PPtrs); local
    # structural links were already consumed by UnityPy during scene parsing.
    indexed_paths = {
        (asset.bundle_hash.casefold(), asset.path_id)
        for asset in index.assets if asset.version_timestamp == index.version_timestamp
    }
    indexed_refs = tuple(
        pointer for pointer in scoped_refs
        if pointer.file_id != 0
        or (parsed.bundle_hash.casefold(), pointer.path_id) in indexed_paths
    )
    return replace(
        parsed, game_objects=game_objects, component_refs=component_refs, parts=parts,
        interactions=interactions, references=indexed_refs, diagnostics=diagnostics,
    )


def _incomplete_scene(
    version_timestamp: int,
    root: PortraitRoot,
    game_version: str,
    code: str,
    message: str,
    details: tuple[tuple[str, str], ...] = (),
) -> PortraitScene:
    metadata = _metadata(root)
    return PortraitScene(
        game_version=game_version,
        version_timestamp=str(version_timestamp),
        entry_config_id=metadata.get("entry_id", root.skin_id),
        character_id=metadata.get("card_id", ""),
        skin_id=root.skin_id,
        prefab=None,
        source_bundles=(),
        parts=(),
        display_position=root.position,
        config_metadata=tuple(metadata.items()),
        diagnostics=(SceneDiagnostic(code, message, details=details), *(
            runtime_diagnostics(root.event_runtime) if root.event_runtime else ()
        )),
    )


def _field_target(resolved: ResolvedScene, source: AssetRef | None, suffix: str) -> AssetRef | None:
    if source is None:
        return None
    normalized = suffix.casefold()
    matches = {
        item.target for item in resolved.references
        if item.source == source and item.target is not None
        and item.field_path.casefold().endswith(normalized)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _field_targets(
    resolved: ResolvedScene, source: AssetRef | None, prefix: str,
) -> tuple[AssetRef, ...]:
    if source is None:
        return ()
    matching = [
        item for item in resolved.references
        if item.source == source and item.target is not None
        and prefix.casefold() in item.field_path.casefold()
    ]
    matching.sort(key=lambda item: item.field_path)
    return tuple(item.target for item in matching if item.target is not None)


def _part_to_model(
    part: UnityScenePart, resolved: ResolvedScene, stable_order: int,
) -> ScenePart:
    skeleton = part.skeleton_ref or _field_target(
        resolved, part.component_ref, "skeletondataasset",
    )
    skeleton_json = part.skeleton_json_ref or _field_target(
        resolved, skeleton, "skeletonjson",
    )
    atlas_refs = part.atlas_refs or _field_targets(resolved, skeleton, "atlasassets")
    sprite_ref = part.sprite_ref or _field_target(
        resolved, part.component_ref, "m_sprite",
    )
    sprite_texture = part.sprite_texture_ref or _field_target(
        resolved, sprite_ref, "texture",
    )
    sprite_renderer = part.component_type == "SpriteRenderer"

    materials = part.material_refs or _field_targets(resolved, part.renderer_ref or part.component_ref, "m_materials")
    renderer = RendererState(
        kind=part.component_type,
        enabled=part.enabled,
        sorting_layer=str(part.sorting_layer_value),
        sorting_order=part.sorting_order,
        transform=tuple(part.world_position),
        material=materials[0] if materials else None,
        materials=materials,
        mesh=part.mesh_ref,
        color=part.renderer_color,
        flip_x=part.flip_x,
        flip_y=part.flip_y,
        mask_interaction=part.mask_interaction,
        blend_mode=part.blend_mode,
    )
    spine = None
    if skeleton is not None:
        spine = SpineState(
            skeleton=skeleton,
            atlas=atlas_refs[0] if atlas_refs else None,
            texture=None,
            material=materials[0] if materials else None,
            default_animation=part.animation,
            loop=part.loop,
            time_scale=part.time_scale,
            initial_skin=part.initial_skin,
            skeleton_json=skeleton_json,
            atlases=atlas_refs,
            skeleton_scale=part.skeleton_scale,
        )
    sprite = None
    if sprite_renderer and sprite_ref is not None:
        sprite = SpriteState(
            sprite=sprite_ref,
            rect=part.sprite_rect,
            pivot=part.sprite_pivot,
            texture=sprite_texture,
            pixels_per_unit=part.pixels_to_units,
        )
    transform_chain = tuple(TransformState(
        object_ref=node.component_ref,
        local_position=node.local_position,
        local_rotation=node.local_rotation,
        local_scale=node.local_scale,
        sibling_index=node.sibling_index,
    ) for node in part.transform_chain)
    return ScenePart(
        part_id=f"{part.component_type}:{part.component_ref.bundle_hash}:{part.component_ref.path_id}",
        object_ref=part.component_ref,
        game_object_name=part.game_object_name,
        hierarchy_path=part.hierarchy_path,
        renderer=renderer,
        spine=spine,
        sprite=sprite,
        transform_chain=transform_chain,
        sorting_layer_value=part.sorting_layer_value,
        renderer_depth=part.renderer_depth,
        stable_order=stable_order,
        particle=part.particle,
    )


def _interactions(
    parsed: UnitySceneParseResult, resolved: ResolvedScene,
) -> tuple[InteractionEvent, ...]:
    result = []
    for item in parsed.interactions:
        target = item.target_component or _field_target(
            resolved, item.component_ref, "skeletonanimation",
        )
        collider = item.collider_ref or _field_target(
            resolved, item.component_ref, "collider",
        )
        result.append(InteractionEvent(
            gesture=item.gesture,
            finger_count=item.finger_count,
            target=target,
            animation=item.animation,
            callback=item.callback_script,
            fallback=item.fallback,
            collider=collider,
        ))
    return tuple(result)


def _scene_assets(
    prefab: AssetRef | None,
    parts: tuple[ScenePart, ...],
    interactions: tuple[InteractionEvent, ...],
    resolved: ResolvedScene,
) -> tuple[AssetRef, ...]:
    refs: set[AssetRef] = set()
    if prefab is not None:
        refs.add(prefab)
    for item in resolved.references:
        refs.add(item.source)
        if item.target is not None:
            refs.add(item.target)
        if item.asset is not None:
            refs.add(AssetRef(item.asset.bundle_hash, item.asset.path_id))
    for part in parts:
        refs.add(part.object_ref)
        refs.update(node.object_ref for node in part.transform_chain)
        if part.renderer:
            refs.update(part.renderer.materials)
            refs.update(ref for ref in (part.renderer.material, part.renderer.mesh) if ref)
        if part.spine:
            refs.update(ref for ref in (
                part.spine.skeleton, part.spine.atlas, part.spine.texture, part.spine.material,
                part.spine.skeleton_json, *part.spine.atlases,
            ) if ref)
        if part.particle:
            refs.update(ref for ref in (part.particle.system, part.particle.material, part.particle.texture) if ref)
        if part.sprite:
            refs.update(ref for ref in (part.sprite.sprite, part.sprite.texture) if ref)
    for item in interactions:
        refs.update(ref for ref in (item.target, item.collider) if ref)
    return tuple(sorted(refs))


def _asset_dependencies(resolved: ResolvedScene) -> tuple[AssetDependency, ...]:
    return tuple(sorted({
        AssetDependency(item.source, item.field_path, item.target)
        for item in resolved.references
        if item.status == "resolved" and item.target is not None
    }, key=lambda item: (item.source, item.field_path, item.target)))


def _source_bundle_details(
    bundle_hashes: tuple[str, ...],
    dependencies: tuple[AssetDependency, ...],
    index: AssetIndex,
) -> tuple[SourceBundle, ...]:
    return tuple(SourceBundle(
        bundle_hash,
        str(index.bundle_path_for(bundle_hash) or ""),
        tuple(sorted({
            edge.target.bundle_hash for edge in dependencies
            if edge.source.bundle_hash == bundle_hash
            and edge.target.bundle_hash != bundle_hash
        })),
    ) for bundle_hash in bundle_hashes)


def _source_bundles(
    prefab: IndexedAsset, parts: tuple[ScenePart, ...], resolved: ResolvedScene,
) -> tuple[str, ...]:
    bundles = {prefab.bundle_hash}
    bundles.update(ref.bundle_hash for ref in resolved.visited)
    for item in resolved.references:
        if item.asset is not None:
            bundles.add(item.asset.bundle_hash)
    for part in parts:
        bundles.add(part.object_ref.bundle_hash)
        for ref in _part_asset_refs(part):
            bundles.add(ref.bundle_hash)
    return tuple(sorted(bundles))


def _part_asset_refs(part: ScenePart) -> set[AssetRef]:
    refs: set[AssetRef] = {node.object_ref for node in part.transform_chain}
    if part.renderer:
        refs.update(part.renderer.materials)
        refs.update(ref for ref in (part.renderer.material, part.renderer.mesh) if ref)
    if part.spine:
        refs.update(ref for ref in (
            part.spine.skeleton, part.spine.atlas, part.spine.texture, part.spine.material,
            part.spine.skeleton_json, *part.spine.atlases,
        ) if ref)
    if part.particle:
        refs.update(ref for ref in (part.particle.system, part.particle.material, part.particle.texture) if ref)
    if part.sprite:
        refs.update(ref for ref in (part.sprite.sprite, part.sprite.texture) if ref)
    return refs


def _unique_diagnostics(items: tuple[SceneDiagnostic, ...]) -> tuple[SceneDiagnostic, ...]:
    return tuple(dict.fromkeys(items))


def _bundle_fingerprints(scene: PortraitScene, index: AssetIndex) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for bundle_hash in scene.source_bundles:
        path = index.bundle_path_for(bundle_hash)
        if path is None or not path.is_file():
            return {}
        fingerprints[bundle_hash] = file_sha256(path)
    return fingerprints


def build_portrait_scenes(
    version_timestamp: int,
    roots: list[PortraitRoot],
    index: AssetIndex,
    *,
    game_version: str = "",
    cache: PortraitSceneCache | None = None,
    cache_hit_callback: Callable[[str], None] | None = None,
) -> list[PortraitScene]:
    """Build each configured skin from an exact prefab reference and its PPtrs."""
    version_timestamp = int(version_timestamp)
    if index.version_timestamp != version_timestamp:
        raise ValueError(
            f"asset index version {index.version_timestamp} does not match requested {version_timestamp}"
        )
    scenes: list[PortraitScene] = []
    ordered_roots = sorted(roots, key=lambda item: (
        item.skin_id, item.prefab_name or "", item.evidence,
    ))
    skin_counts: dict[str, int] = {}
    for root in ordered_roots:
        skin_counts[root.skin_id] = skin_counts.get(root.skin_id, 0) + 1

    for root in ordered_roots:
        metadata = _metadata(root)
        if root.version_timestamp != version_timestamp:
            scenes.append(_incomplete_scene(
                version_timestamp, root, game_version, "root_version_mismatch",
                "Lua portrait root belongs to a different version",
                (("root_version", str(root.version_timestamp)),),
            ))
            continue
        if skin_counts[root.skin_id] > 1:
            scenes.append(_incomplete_scene(
                version_timestamp, root, game_version, "duplicate_skin_root",
                "Multiple Lua portrait roots use the same skin ID",
                (("skin_id", root.skin_id),),
            ))
            continue
        if root.unresolved_reason:
            scenes.append(_incomplete_scene(
                version_timestamp, root, game_version, root.unresolved_reason,
                "Lua portrait root does not identify a Prefab",
                (("evidence", root.evidence),),
            ))
            continue
        if not root.prefab_name:
            scenes.append(_incomplete_scene(
                version_timestamp, root, game_version, "missing_show_spine",
                "Lua portrait root has no configured show_spine Prefab",
                (("evidence", root.evidence),),
            ))
            continue
        if root.event_runtime and root.event_runtime.purpose == "sequential_entrance":
            scenes.append(_incomplete_scene(
                version_timestamp, root, game_version, "event_sequence_unsupported",
                "Configured cover is unmounted; entrance phases are sequential and cannot form one scene",
                (("purpose", root.event_runtime.purpose),
                 ("sequence_prefabs", ";".join(root.event_runtime.sequence_prefabs)),
                 ("evidence", ";".join(root.event_runtime.evidence))),
            ))
            continue
        prefab_matches = index.find_by_name_exact(root.prefab_name)
        container_assets: tuple[IndexedAsset, ...] = ()
        if not prefab_matches:
            container_assets = index.find_by_container_exact(root.prefab_name)
            bundle_hashes = {item.bundle_hash for item in container_assets}
            container_paths = {item.container.casefold() for item in container_assets}
            if len(container_paths) > 1:
                scenes.append(_incomplete_scene(
                    version_timestamp, root, game_version, "ambiguous_prefab_asset",
                    "Configured Prefab basename matches multiple AssetStudio containers",
                    (("prefab_name", root.prefab_name),
                     ("matches", str(len(container_paths)))),
                ))
                continue
            if len(bundle_hashes) == 1:
                # AssetStudio emits one row per contained asset, not a Prefab
                # row. Keep a stable indexed anchor while retaining the full
                # container's asset evidence for scene scoping and cache keys.
                prefab_matches = (min(container_assets, key=lambda item: (
                    0 if item.name.casefold().endswith(".skel") else 1,
                    item.name.casefold(), item.path_id,
                )),)
            elif len(bundle_hashes) > 1:
                scenes.append(_incomplete_scene(
                    version_timestamp, root, game_version, "ambiguous_prefab_asset",
                    "Configured Prefab container maps to more than one source bundle",
                    (("prefab_name", root.prefab_name),
                     ("matches", str(len(bundle_hashes)))),
                ))
                continue
        if not prefab_matches:
            leaf = str(root.prefab_name).replace("\\", "/").rsplit("/", 1)[-1].casefold()
            if leaf in _KNOWN_NON_CN_PREFABS:
                scene = _incomplete_scene(
                    version_timestamp, root, game_version, "region_not_applicable",
                    "Confirmed Japan-only configuration has no asset in this CN version",
                    (("prefab_name", root.prefab_name), ("evidence", root.evidence)),
                )
                scenes.append(replace(
                    scene, config_metadata=(*_metadata(root).items(), ("export_status", "not_applicable")),
                    diagnostics=tuple(replace(d, severity="warning") for d in scene.diagnostics),
                ))
                continue
            scenes.append(_incomplete_scene(
                version_timestamp, root, game_version, "prefab_asset_missing",
                "Configured Prefab name has no exact AssetStudio catalog match",
                (("prefab_name", root.prefab_name), ("evidence", root.evidence)),
            ))
            continue
        if len(prefab_matches) != 1:
            scenes.append(_incomplete_scene(
                version_timestamp, root, game_version, "ambiguous_prefab_asset",
                "Configured Prefab name maps to more than one catalog asset",
                (("prefab_name", root.prefab_name),
                 ("matches", str(len(prefab_matches)))),
            ))
            continue
        prefab_asset = prefab_matches[0]
        if not prefab_asset.bundle_present:
            scenes.append(_incomplete_scene(
                version_timestamp, root, game_version, "prefab_bundle_missing",
                "Configured Prefab bundle is absent from the selected version",
                (("bundle_hash", prefab_asset.bundle_hash),
                 ("path", str(prefab_asset.bundle_path))),
            ))
            continue

        config_hash = _root_config_hash(root, prefab_asset.bundle_hash, container_assets)
        cache_key = cache.key_for(version_timestamp, root.skin_id) if cache else ""
        if cache is not None:
            cached = cache.load(cache_key, config_hash, index)
            if cached is not None:
                scenes.append(cached)
                if cache_hit_callback:
                    cache_hit_callback(root.skin_id)
                continue

        parsed = parse_unity_scene(
            prefab_asset.bundle_path, prefab_asset.bundle_hash, version_timestamp,
        )
        if container_assets or parsed.container_roots:
            parsed = _scope_to_container(
                parsed, container_assets or (prefab_asset,), index,
                prefab_name=str(root.prefab_name),
                prefab_ref=None if container_assets else AssetRef(prefab_asset.bundle_hash, prefab_asset.path_id),
            )
            if not parsed.parts:
                incomplete = _incomplete_scene(
                    version_timestamp, root, game_version, "prefab_scene_parts_unmatched",
                    "AssetStudio container has no matching Unity renderer parts",
                    (("prefab_name", root.prefab_name),
                     ("bundle_hash", prefab_asset.bundle_hash),
                     ("container_assets", str(len(container_assets)))),
                )
                scenes.append(replace(incomplete, diagnostics=(*incomplete.diagnostics, *parsed.diagnostics)))
                continue
        resolved = resolve_scene_assets(parsed, index)
        parts = tuple(
            _part_to_model(part, resolved, order)
            for order, part in enumerate(parsed.sorted_parts())
        )
        runtime_notes = ()
        if root.event_runtime:
            parts, runtime_notes = apply_event_runtime(parts, root.event_runtime)
        from .particle_metadata import enrich_particle
        enriched = [enrich_particle(part, index) for part in parts]
        parts = tuple(part for part, _ in enriched)
        shader_references=[]
        for part in parts:
            if part.particle and part.particle.profile != 'unsupported' and part.particle.material:
                material=part.particle.material
                pointer=index.object_tree(material).get('m_Shader')
                shader=index.resolve_object_pointer(material,pointer,expected_type='Shader')
                if shader is not None:
                    asset=index.find_exact(shader.bundle_hash,shader.path_id)[0]
                    shader_references.append(ResolvedReference(material,'particle.shader',pointer,shader,asset,'resolved',asset.evidence))
        if shader_references:
            resolved=replace(resolved,references=(*resolved.references,*shader_references))
        runtime_notes = (*runtime_notes, *(note for _, notes in enriched for note in notes))
        from .interaction_metadata import apply_callbacks
        interactions, interaction_notes = apply_callbacks(_interactions(parsed, resolved), root)
        runtime_notes = (*runtime_notes, *interaction_notes)
        source_bundles = _source_bundles(prefab_asset, parts, resolved)
        asset_dependencies = _asset_dependencies(resolved)
        source_bundle_details = _source_bundle_details(source_bundles, asset_dependencies, index)
        prefab_ref = (
            None if container_assets
            else AssetRef(prefab_asset.bundle_hash, prefab_asset.path_id)
        )
        scene = PortraitScene(
            game_version=game_version,
            version_timestamp=str(version_timestamp),
            entry_config_id=metadata.get("entry_id", root.skin_id),
            character_id=metadata.get("card_id", ""),
            skin_id=root.skin_id,
            prefab=prefab_ref,
            source_bundles=source_bundles,
            parts=parts,
            available_assets=_scene_assets(prefab_ref, parts, interactions, resolved),
            interactions=interactions,
            diagnostics=_unique_diagnostics((*resolved.diagnostics, *runtime_notes)),
            source_bundle_details=source_bundle_details,
            asset_dependencies=asset_dependencies,
            prefab_container=container_assets[0].container if container_assets else "",
            display_position=root.position,
            config_metadata=tuple(metadata.items()),
        )
        resource_diagnostics = []
        for source_part, model_part in zip(parsed.sorted_parts(), parts, strict=True):
            if source_part.component_type == "SkeletonAnimation":
                if (
                    model_part.spine is None
                    or model_part.spine.skeleton_json is None
                    or not model_part.spine.atlases
                ):
                    resource_diagnostics.append(SceneDiagnostic(
                        "spine_resources_incomplete",
                        "Spine part lacks a resolved SkeletonData, skeleton JSON, or atlas reference",
                        part_id=model_part.part_id,
                        source=model_part.object_ref,
                    ))
            if source_part.component_type == "SpriteRenderer" and (
                model_part.sprite is None or not model_part.sprite.rect or not model_part.sprite.pivot
            ):
                resource_diagnostics.append(SceneDiagnostic(
                    "sprite_metadata_incomplete",
                    "Sprite part lacks a resolved Sprite, rect, or pivot",
                    part_id=model_part.part_id,
                    source=model_part.object_ref,
                ))
        if resource_diagnostics:
            scene = replace(
                scene, diagnostics=_unique_diagnostics((*scene.diagnostics, *resource_diagnostics)),
            )
        missing_bundle_details = tuple(
            SceneDiagnostic(
                "source_bundle_unindexed", "Scene source bundle is missing from the selected version index",
                details=(("bundle_hash", item.bundle_hash),),
            )
            for item in source_bundle_details if not item.path
        )
        if missing_bundle_details:
            scene = replace(
                scene, diagnostics=_unique_diagnostics((*scene.diagnostics, *missing_bundle_details)),
            )
        validation = scene.validate()
        scene = replace(scene, diagnostics=_unique_diagnostics(validation))
        scenes.append(scene)
        if cache is not None and not any(item.severity == "error" for item in scene.diagnostics):
            fingerprints = _bundle_fingerprints(scene, index)
            if fingerprints:
                cache.store(scene, config_hash, fingerprints)
    return scenes
