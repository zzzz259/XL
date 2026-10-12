"""Deterministic Unity Prefab object-graph extraction from UnityPy typetrees."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import AssetRef, SceneDiagnostic


@dataclass(frozen=True, slots=True)
class TransformNode:
    component_ref: AssetRef
    local_position: tuple[float, float, float]
    local_rotation: tuple[float, float, float, float]
    local_scale: tuple[float, float, float]
    sibling_index: int


@dataclass(frozen=True, slots=True)
class UnityScenePart:
    game_object_ref: AssetRef
    component_ref: AssetRef
    game_object_name: str
    component_type: str
    hierarchy_path: str
    transform_chain: tuple[TransformNode, ...]
    world_position: tuple[float, float, float]
    enabled: bool
    sorting_layer_value: int
    sorting_order: int
    renderer_depth: float
    renderer_ref: AssetRef | None = None
    sprite_ref: AssetRef | None = None
    sprite_texture_ref: AssetRef | None = None
    sprite_rect: tuple[float, ...] = ()
    sprite_pivot: tuple[float, ...] = ()
    pixels_to_units: float = 100.0
    skeleton_ref: AssetRef | None = None
    skeleton_json_ref: AssetRef | None = None
    atlas_refs: tuple[AssetRef, ...] = ()
    material_refs: tuple[AssetRef, ...] = ()
    mesh_ref: AssetRef | None = None
    animation: str = ""
    loop: bool = False
    time_scale: float = 1.0
    initial_skin: str = ""
    flip_x: bool = False
    flip_y: bool = False
    renderer_color: tuple[float, ...] = (1.0, 1.0, 1.0, 1.0)
    renderer_size: tuple[float, ...] = ()
    draw_mode: int = 0
    mask_interaction: int = 0
    sorting_layer_id: int = 0


@dataclass(frozen=True, slots=True)
class UnityInteraction:
    component_ref: AssetRef
    game_object_ref: AssetRef
    gesture: str
    animation: str
    target_component: AssetRef | None
    finger_count: int = 1
    collider_ref: AssetRef | None = None
    callback_script: str = ""
    fallback: str = ""


@dataclass(frozen=True, slots=True)
class UnitySceneParseResult:
    bundle_hash: str
    game_objects: tuple[tuple[AssetRef, str], ...]
    component_refs: tuple[tuple[AssetRef, AssetRef, str], ...]
    parts: tuple[UnityScenePart, ...]
    interactions: tuple[UnityInteraction, ...]
    diagnostics: tuple[SceneDiagnostic, ...]

    def sort_key(self, part: UnityScenePart) -> tuple[Any, ...]:
        sibling_path = tuple(node.sibling_index for node in part.transform_chain)
        return (
            part.sorting_layer_value,
            part.sorting_order,
            part.renderer_depth,
            sibling_path,
            part.component_ref.bundle_hash,
            part.component_ref.path_id,
        )

    def sorted_parts(self) -> tuple[UnityScenePart, ...]:
        return tuple(sorted(self.parts, key=self.sort_key))


def _load_environment(bundle_path: Path):
    import UnityPy

    UnityPy.set_assetbundle_decrypt_key(b"yunguihaowan1234")
    return UnityPy.load(str(bundle_path))


def _ptr(value: Any) -> tuple[int, int]:
    if not isinstance(value, dict):
        return 0, 0
    return int(value.get("m_FileID", 0) or 0), int(value.get("m_PathID", 0) or 0)


def _asset_ref(bundle_hash: str, pointer: Any, objects: dict[int, Any]) -> AssetRef | None:
    file_id, path_id = _ptr(pointer)
    if file_id != 0 or not path_id or path_id not in objects:
        return None
    return AssetRef(bundle_hash, path_id)


def _vec(value: Any, keys: tuple[str, ...], default: tuple[float, ...]) -> tuple[float, ...]:
    if not isinstance(value, dict):
        return default
    if any(key not in value and index >= len(default) for index, key in enumerate(keys)):
        return default
    return tuple(float(value[key] if key in value else default[index]) for index, key in enumerate(keys))


def _typetree(obj: Any) -> dict[str, Any]:
    try:
        return obj.read_typetree()
    except (AttributeError, TypeError):
        return obj.read().read_typetree()


def _component_kind(type_name: str, tree: dict[str, Any]) -> str:
    if type_name in {"Transform", "RectTransform"}:
        return "Transform"
    if type_name != "MonoBehaviour":
        return type_name
    if "skeletonDataAsset" in tree:
        return "SkeletonAnimation"
    if "skeletonJSON" in tree and "atlasAssets" in tree:
        return "SkeletonDataAsset"
    if "eventAnimationEntries" in tree or str(tree.get("scriptName", "")).casefold().startswith("touch_"):
        return "CardSpineTouchEvent"
    return type_name


def _world_position(chain: tuple[TransformNode, ...]) -> tuple[float, float, float]:
    matrix = (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    for node in chain:
        x, y, z, w = node.local_rotation
        sx, sy, sz = node.local_scale
        local = (
            ((1 - 2 * (y * y + z * z)) * sx, 2 * (x * y - z * w) * sy,
             2 * (x * z + y * w) * sz, node.local_position[0]),
            (2 * (x * y + z * w) * sx, (1 - 2 * (x * x + z * z)) * sy,
             2 * (y * z - x * w) * sz, node.local_position[1]),
            (2 * (x * z - y * w) * sx, 2 * (y * z + x * w) * sy,
             (1 - 2 * (x * x + y * y)) * sz, node.local_position[2]),
            (0.0, 0.0, 0.0, 1.0),
        )
        matrix = tuple(tuple(sum(matrix[row][k] * local[k][column] for k in range(4))
                             for column in range(4)) for row in range(4))
    return matrix[0][3], matrix[1][3], matrix[2][3]


def _record_unresolved(pointer: Any, source: AssetRef, field: str, objects: dict[int, Any],
                       diagnostics: list[SceneDiagnostic]) -> None:
    file_id, path_id = _ptr(pointer)
    if not path_id:
        return
    if file_id:
        code = "unresolved_external_pptr"
        message = f"{field} references external serialized file {file_id}, path {path_id}"
    elif path_id not in objects:
        code = "unresolved_pptr"
        message = f"{field} references missing path {path_id}"
    else:
        return
    diagnostics.append(SceneDiagnostic(code, message, source=source,
        details=(("field", field), ("file_id", str(file_id)), ("path_id", str(path_id)))))


def _renderer_refs(tree: dict[str, Any], bundle_hash: str, objects: dict[int, Any]) -> tuple[AssetRef, ...]:
    refs = []
    for pointer in tree.get("m_Materials", ()):
        ref = _asset_ref(bundle_hash, pointer, objects)
        if ref is not None:
            refs.append(ref)
    return tuple(refs)


def parse_unity_scene(bundle_path: Path, bundle_hash: str) -> UnitySceneParseResult:
    """Read a prefab's object graph and retain exact Unity object identities."""
    diagnostics: list[SceneDiagnostic] = []
    try:
        env = _load_environment(Path(bundle_path))
    except Exception as exc:
        return UnitySceneParseResult(str(bundle_hash), (), (), (), (), (
            SceneDiagnostic("bundle_load_failed", f"UnityPy could not load {bundle_path}: {exc}"),
        ))

    objects = {int(obj.path_id): obj for obj in env.objects}
    types = {path_id: str(obj.type.name) for path_id, obj in objects.items()}
    accepted_types = {"GameObject", "Transform", "RectTransform", "MeshRenderer", "SkinnedMeshRenderer",
                      "SpriteRenderer", "MeshFilter", "Sprite", "MonoBehaviour"}
    trees: dict[int, dict[str, Any]] = {}
    kinds: dict[int, str] = {}
    for path_id, obj in objects.items():
        type_name = types[path_id]
        if type_name not in accepted_types and not type_name.endswith("Renderer"):
            continue
        try:
            trees[path_id] = _typetree(obj)
            kinds[path_id] = _component_kind(type_name, trees[path_id])
        except Exception as exc:
            diagnostics.append(SceneDiagnostic(
                "typetree_read_failed", f"{type_name} {path_id}: {exc}",
                source=AssetRef(str(bundle_hash), path_id),
            ))

    names = {
        path_id: str(tree.get("m_Name", ""))
        for path_id, tree in trees.items()
        if types.get(path_id) == "GameObject"
    }
    go_components: dict[int, list[int]] = {}
    component_owner: dict[int, int] = {}
    for go_id, tree in trees.items():
        if types.get(go_id) != "GameObject":
            continue
        for entry in tree.get("m_Component", ()):
            pointer = entry.get("component") if isinstance(entry, dict) else entry
            file_id, component_id = _ptr(pointer)
            if not component_id:
                continue
            if file_id != 0 or component_id not in objects:
                _record_unresolved(pointer, AssetRef(str(bundle_hash), go_id), "m_Component", objects, diagnostics)
                continue
            if component_id not in trees and (
                types.get(component_id) in accepted_types or types.get(component_id, "").endswith("Renderer")
            ):
                diagnostics.append(SceneDiagnostic(
                    "component_typetree_unavailable",
                    f"Component {component_id} ({types.get(component_id, 'unknown')}) is present but could not be parsed",
                    source=AssetRef(str(bundle_hash), go_id),
                    details=(("component_path_id", str(component_id)),
                             ("component_type", types.get(component_id, "unknown"))),
                ))
                component_owner[component_id] = go_id
                continue
            go_components.setdefault(go_id, []).append(component_id)
            component_owner[component_id] = go_id

    transforms: dict[int, tuple[int, dict[str, Any]]] = {}
    for component_id, tree in trees.items():
        if kinds.get(component_id) != "Transform":
            continue
        _, game_object_id = _ptr(tree.get("m_GameObject"))
        if not game_object_id:
            continue
        if game_object_id in transforms:
            diagnostics.append(SceneDiagnostic(
                "duplicate_transform", f"Multiple Transform components on GameObject {game_object_id}",
                source=AssetRef(str(bundle_hash), component_id),
            ))
            continue
        transforms[game_object_id] = (component_id, tree)
    for transform_id, tree in trees.items():
        if kinds.get(transform_id) != "Transform":
            continue
        source = AssetRef(str(bundle_hash), transform_id)
        _record_unresolved(tree.get("m_Father"), source, "m_Father", objects, diagnostics)
        for child in tree.get("m_Children", ()):
            _record_unresolved(child, source, "m_Children", objects, diagnostics)

    def chain_for(game_object_id: int) -> tuple[str, tuple[TransformNode, ...]]:
        reversed_nodes: list[TransformNode] = []
        reversed_names: list[str] = []
        seen: set[int] = set()
        current_go = game_object_id
        while current_go:
            if current_go in seen:
                diagnostics.append(SceneDiagnostic(
                    "transform_cycle", f"Transform cycle detected at GameObject {current_go}",
                    source=AssetRef(str(bundle_hash), current_go),
                ))
                break
            seen.add(current_go)
            item = transforms.get(current_go)
            if item is None:
                name = names.get(current_go, f"<GameObject:{current_go}>")
                reversed_names.append(name)
                diagnostics.append(SceneDiagnostic(
                    "missing_transform", f"GameObject {current_go} has no Transform",
                    source=AssetRef(str(bundle_hash), current_go),
                ))
                break
            transform_id, tree = item
            position = _vec(tree.get("m_LocalPosition"), ("x", "y", "z"), (0.0, 0.0, 0.0))
            rotation = _vec(tree.get("m_LocalRotation"), ("x", "y", "z", "w"), (0.0, 0.0, 0.0, 1.0))
            scale = _vec(tree.get("m_LocalScale"), ("x", "y", "z"), (1.0, 1.0, 1.0))
            parent_transform_id = _ptr(tree.get("m_Father"))[1]
            parent_go_id = _ptr(trees.get(parent_transform_id, {}).get("m_GameObject"))[1] if parent_transform_id else 0
            sibling_index = 0
            if parent_transform_id:
                parent_children = trees.get(parent_transform_id, {}).get("m_Children", ())
                sibling_index = next((index for index, child in enumerate(parent_children)
                                      if _ptr(child)[1] == transform_id), 0)
            reversed_nodes.append(TransformNode(AssetRef(str(bundle_hash), transform_id), position,
                rotation, scale, sibling_index))
            reversed_names.append(names.get(current_go, f"<GameObject:{current_go}>"))
            current_go = parent_go_id
        reversed_nodes.reverse()
        reversed_names.reverse()
        return "/".join(reversed_names), tuple(reversed_nodes)

    game_objects = tuple(sorted(
        ((AssetRef(str(bundle_hash), path_id), name) for path_id, name in names.items()),
        key=lambda item: (item[0].bundle_hash, item[0].path_id),
    ))
    component_refs = tuple(sorted(
        ((AssetRef(str(bundle_hash), component_id), AssetRef(str(bundle_hash), game_object_id),
          kinds.get(component_id, types.get(component_id, "")))
         for component_id, game_object_id in component_owner.items()),
        key=lambda item: (item[0].bundle_hash, item[0].path_id),
    ))

    interactions: list[UnityInteraction] = []
    for component_id, game_object_id in component_owner.items():
        if kinds.get(component_id) != "CardSpineTouchEvent":
            continue
        tree = trees.get(component_id, {})
        entries = tree.get("eventAnimationEntries", ())
        if entries:
            for event_entry in entries:
                gesture = str(event_entry.get("gesture", ""))
                fingers = int(event_entry.get("requiredFingers", 1) or 1)
                collider_pointer = event_entry.get("collider")
                animation_entries = event_entry.get("animationEntries", ()) or ({},)
                for animation_entry in animation_entries:
                    target = _asset_ref(bundle_hash, animation_entry.get("skeletonAnimation"), objects)
                    animation = str(animation_entry.get("animation", ""))
                    if animation_entry.get("skeletonAnimation") and target is None:
                        _record_unresolved(animation_entry.get("skeletonAnimation"),
                            AssetRef(str(bundle_hash), component_id), "skeletonAnimation", objects, diagnostics)
                        diagnostics.append(SceneDiagnostic("unresolved_interaction_target",
                            f"Unresolved touch target for {gesture}/{animation}",
                            source=AssetRef(str(bundle_hash), component_id)))
                    if collider_pointer:
                        _record_unresolved(collider_pointer, AssetRef(str(bundle_hash), component_id),
                            "collider", objects, diagnostics)
                    interactions.append(UnityInteraction(
                        AssetRef(str(bundle_hash), component_id), AssetRef(str(bundle_hash), game_object_id),
                        gesture, animation, target, fingers,
                        _asset_ref(bundle_hash, collider_pointer, objects), str(tree.get("scriptName", "")),
                    ))
        else:
            target_pointer = tree.get("m_Target", tree.get("target", tree.get("skeletonAnimation")))
            collider_pointer = tree.get("m_Collider", tree.get("collider"))
            target = _asset_ref(bundle_hash, target_pointer, objects)
            if target_pointer and target is None:
                _record_unresolved(target_pointer, AssetRef(str(bundle_hash), component_id),
                                   "skeletonAnimation", objects, diagnostics)
                diagnostics.append(SceneDiagnostic(
                    "unresolved_interaction_target", f"Unresolved touch target PPtr {target_pointer}",
                    source=AssetRef(str(bundle_hash), component_id),
                ))
            if collider_pointer:
                _record_unresolved(collider_pointer, AssetRef(str(bundle_hash), component_id),
                                   "collider", objects, diagnostics)
            interactions.append(UnityInteraction(
                AssetRef(str(bundle_hash), component_id), AssetRef(str(bundle_hash), game_object_id),
                str(tree.get("m_Gesture", tree.get("gesture", ""))),
                str(tree.get("m_AnimationName", tree.get("animationName", ""))), target,
                collider_ref=_asset_ref(bundle_hash, collider_pointer, objects),
                callback_script=str(tree.get("scriptName", "")),
            ))

    parts: list[UnityScenePart] = []
    renderer_kinds = {"MeshRenderer", "SpriteRenderer"}
    for game_object_id, component_ids in go_components.items():
        mesh_renderers = [cid for cid in component_ids if kinds.get(cid) == "MeshRenderer"]
        sprite_renderers = [cid for cid in component_ids if kinds.get(cid) == "SpriteRenderer"]
        spine_components = [cid for cid in component_ids if kinds.get(cid) == "SkeletonAnimation"]
        mesh_filter_ids = [cid for cid in component_ids if types.get(cid) == "MeshFilter"]
        mesh_filter = trees.get(mesh_filter_ids[0], {}) if mesh_filter_ids else {}
        mesh_ref = _asset_ref(bundle_hash, mesh_filter.get("m_Mesh"), objects)
        hierarchy, transform_chain = chain_for(game_object_id)
        world_position = _world_position(transform_chain)

        def append_part(component_id: int, component_type: str, renderer_id: int | None = None,
                        sprite_pointer: Any = None) -> None:
            tree = trees.get(component_id, {})
            renderer_tree = trees.get(renderer_id, {}) if renderer_id else tree
            renderer_component_id = renderer_id
            if renderer_component_id is None and component_type in {"MeshRenderer", "SpriteRenderer"}:
                renderer_component_id = component_id
            renderer_ref = AssetRef(str(bundle_hash), renderer_component_id) if renderer_component_id else None
            sprite_ref = _asset_ref(bundle_hash, sprite_pointer, objects) if sprite_pointer else None
            sprite_tree = trees.get(sprite_ref.path_id, {}) if sprite_ref else {}
            render_data = sprite_tree.get("m_RD", {}) if isinstance(sprite_tree, dict) else {}
            texture_pointer = render_data.get("texture") if render_data else None
            sprite_texture = _asset_ref(bundle_hash, texture_pointer, objects) if texture_pointer else None
            rect = _vec(sprite_tree.get("m_Rect"), ("x", "y", "width", "height"), ()) if sprite_tree else ()
            pivot = _vec(sprite_tree.get("m_Pivot"), ("x", "y"), ()) if sprite_tree else ()
            pixels_to_units = float(sprite_tree.get("m_PixelsToUnits", 100.0) or 0.0) if sprite_tree else 100.0
            layer_value = renderer_tree.get("m_SortingLayerValue", renderer_tree.get("m_SortingLayer", 0))
            if isinstance(layer_value, dict):
                layer_value = layer_value.get("value", 0)
            color = _vec(renderer_tree.get("m_Color", tree.get("color")), ("r", "g", "b", "a"),
                         (1.0, 1.0, 1.0, 1.0))
            renderer_size = _vec(renderer_tree.get("m_Size"), ("x", "y"), ())
            sorting_layer_id = int(renderer_tree.get("m_SortingLayerID", 0) or 0)
            depth = float(renderer_tree.get("m_RendererDepth", world_position[2]) or 0.0)
            skeleton_pointer = tree.get("skeletonDataAsset", tree.get("skeleton", tree.get("m_SkeletonDataAsset")))
            skeleton_ref = _asset_ref(bundle_hash, skeleton_pointer, objects)
            skeleton_tree = trees.get(skeleton_ref.path_id, {}) if skeleton_ref else {}
            skeleton_json = _asset_ref(bundle_hash, skeleton_tree.get("skeletonJSON"), objects)
            atlas_refs = tuple(ref for pointer in skeleton_tree.get("atlasAssets", ())
                               if (ref := _asset_ref(bundle_hash, pointer, objects)) is not None)
            missing_refs = []
            if component_type == "SkeletonAnimation" and skeleton_pointer and skeleton_ref is None:
                missing_refs.append(("skeletonDataAsset", skeleton_pointer))
            if component_type == "SkeletonAnimation" and skeleton_ref is not None:
                if skeleton_tree.get("skeletonJSON") and skeleton_json is None:
                    missing_refs.append(("skeletonJSON", skeleton_tree.get("skeletonJSON")))
                for pointer in skeleton_tree.get("atlasAssets", ()):
                    if _asset_ref(bundle_hash, pointer, objects) is None:
                        missing_refs.append(("atlasAssets", pointer))
            if component_type == "SpriteRenderer" and tree.get("m_Sprite") and sprite_ref is None:
                missing_refs.append(("m_Sprite", tree.get("m_Sprite")))
            if component_type == "SpriteRenderer" and sprite_ref is not None and texture_pointer and sprite_texture is None:
                missing_refs.append(("m_RD.texture", texture_pointer))
            if component_type == "SpriteRenderer" and sprite_ref is not None and not rect:
                diagnostics.append(SceneDiagnostic(
                    "missing_sprite_rect", "Sprite object has no rect data", source=sprite_ref,
                ))
            if component_type == "SpriteRenderer" and sprite_ref is not None and not pivot:
                diagnostics.append(SceneDiagnostic(
                    "missing_sprite_pivot", "Sprite object has no pivot data", source=sprite_ref,
                ))
            for pointer in renderer_tree.get("m_Materials", ()):
                if _asset_ref(bundle_hash, pointer, objects) is None:
                    missing_refs.append(("m_Materials", pointer))
            if component_type == "MeshRenderer" and mesh_filter_ids and mesh_ref is None:
                missing_refs.append(("m_Mesh", mesh_filter.get("m_Mesh")))
            for field_name, pointer in missing_refs:
                _record_unresolved(pointer, AssetRef(str(bundle_hash), component_id), field_name, objects, diagnostics)
            materials = _renderer_refs(renderer_tree, str(bundle_hash), objects)
            parts.append(UnityScenePart(
                AssetRef(str(bundle_hash), game_object_id), AssetRef(str(bundle_hash), component_id),
                names.get(game_object_id, ""), component_type, hierarchy, transform_chain, world_position,
                bool(tree.get("m_Enabled", tree.get("enabled", True))), int(layer_value or 0),
                int(renderer_tree.get("m_SortingOrder", renderer_tree.get("sortingOrder", 0)) or 0), depth,
                renderer_ref, sprite_ref, sprite_texture, rect, pivot, pixels_to_units,
                skeleton_ref, skeleton_json, atlas_refs,
                materials, mesh_ref, str(tree.get("_animationName", tree.get("startingAnimation", ""))),
                bool(tree.get("loop", tree.get("m_Loop", False))), float(tree.get("timeScale", 1.0) or 1.0),
                str(tree.get("initialSkinName", tree.get("initialSkin", ""))),
                bool(tree.get("m_FlipX", tree.get("initialFlipX", False))),
                bool(tree.get("m_FlipY", tree.get("initialFlipY", False))), color, renderer_size,
                int(renderer_tree.get("m_DrawMode", 0) or 0),
                int(renderer_tree.get("m_MaskInteraction", 0) or 0), sorting_layer_id,
            ))

        for component_id in spine_components:
            tree = trees.get(component_id, {})
            if not bool(tree.get("m_Enabled", tree.get("enabled", True))):
                continue
            renderer_id = mesh_renderers[0] if len(mesh_renderers) == 1 else None
            if not mesh_renderers:
                diagnostics.append(SceneDiagnostic(
                    "missing_spine_renderer", "SkeletonAnimation has no MeshRenderer on its GameObject",
                    source=AssetRef(str(bundle_hash), component_id),
                ))
            elif len(mesh_renderers) > 1:
                diagnostics.append(SceneDiagnostic(
                    "ambiguous_spine_renderer_binding",
                    f"SkeletonAnimation has {len(mesh_renderers)} possible MeshRenderer components",
                    source=AssetRef(str(bundle_hash), component_id),
                    details=(("renderer_path_ids", ",".join(map(str, mesh_renderers))),),
                ))
            append_part(component_id, "SkeletonAnimation", renderer_id)

        for component_id in sprite_renderers:
            tree = trees.get(component_id, {})
            if bool(tree.get("m_Enabled", True)):
                append_part(component_id, "SpriteRenderer", component_id, tree.get("m_Sprite"))

        if not spine_components:
            for component_id in mesh_renderers:
                tree = trees.get(component_id, {})
                if bool(tree.get("m_Enabled", True)):
                    append_part(component_id, kinds.get(component_id, "MeshRenderer"))

        for component_id in component_ids:
            kind = kinds.get(component_id, types.get(component_id, ""))
            tree = trees.get(component_id, {})
            if kind.endswith("Renderer") and kind not in renderer_kinds and bool(tree.get("m_Enabled", True)):
                diagnostics.append(SceneDiagnostic(
                    "unsupported_enabled_renderer", f"Enabled renderer {kind} is unsupported",
                    source=AssetRef(str(bundle_hash), component_id),
                ))

    parts.sort(key=lambda part: (
        part.sorting_layer_value, part.sorting_order, part.renderer_depth,
        tuple(node.sibling_index for node in part.transform_chain),
        part.component_ref.bundle_hash, part.component_ref.path_id,
    ))
    interactions.sort(key=lambda event: (event.component_ref.bundle_hash, event.component_ref.path_id,
                                         event.gesture, event.animation))
    return UnitySceneParseResult(str(bundle_hash), game_objects, component_refs, tuple(parts),
                                tuple(interactions), tuple(diagnostics))
