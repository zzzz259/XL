"""Immutable and serializable normalized Unity portrait scene model."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

SCHEMA_VERSION = 8


@dataclass(frozen=True, slots=True, order=True)
class AssetRef:
    bundle_hash: str
    path_id: int

    def to_dict(self) -> dict[str, Any]:
        return {"bundle_hash": self.bundle_hash, "path_id": self.path_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssetRef:
        return cls(str(data["bundle_hash"]), int(data["path_id"]))


@dataclass(frozen=True, slots=True)
class RendererState:
    kind: str
    enabled: bool
    sorting_layer: str = "Default"
    sorting_order: int = 0
    transform: tuple[float, ...] = ()
    material: AssetRef | None = None
    materials: tuple[AssetRef, ...] = ()
    mesh: AssetRef | None = None
    color: tuple[float, ...] = (1.0, 1.0, 1.0, 1.0)
    flip_x: bool = False
    flip_y: bool = False
    mask_interaction: int = 0
    blend_mode: str = "normal"


@dataclass(frozen=True, slots=True)
class AnimationStep:
    animation: str
    loop: bool
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class SpineState:
    skeleton: AssetRef
    atlas: AssetRef | None = None
    texture: AssetRef | None = None
    material: AssetRef | None = None
    default_animation: str = ""
    loop: bool = False
    time_scale: float = 1.0
    initial_skin: str = ""
    animations: tuple[str, ...] = ()
    skins: tuple[str, ...] = ()
    skeleton_json: AssetRef | None = None
    atlases: tuple[AssetRef, ...] = ()
    skeleton_scale: float = 0.01
    animation_sequence: tuple[AnimationStep, ...] = ()


@dataclass(frozen=True, slots=True)
class SpriteState:
    sprite: AssetRef
    rect: tuple[float, ...] = ()
    pivot: tuple[float, ...] = ()
    texture: AssetRef | None = None
    pixels_per_unit: float = 100.0


@dataclass(frozen=True, slots=True)
class TransformState:
    object_ref: AssetRef
    local_position: tuple[float, float, float]
    local_rotation: tuple[float, float, float, float]
    local_scale: tuple[float, float, float]
    sibling_index: int


@dataclass(frozen=True, slots=True)
class SourceBundle:
    bundle_hash: str
    path: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AssetDependency:
    source: AssetRef
    field_path: str
    target: AssetRef


@dataclass(frozen=True, slots=True)
class ParticleState:
    system: AssetRef
    material: AssetRef | None = None
    texture: AssetRef | None = None
    profile: str = "unsupported"
    config_json: str = ""


@dataclass(frozen=True, slots=True)
class ScenePart:
    part_id: str
    object_ref: AssetRef
    game_object_name: str
    hierarchy_path: str = ""
    renderer: RendererState | None = None
    spine: SpineState | None = None
    sprite: SpriteState | None = None
    transform_chain: tuple[TransformState, ...] = ()
    sorting_layer_value: int = 0
    renderer_depth: float = 0.0
    stable_order: int = 0
    particle: ParticleState | None = None


@dataclass(frozen=True, slots=True)
class InteractionEvent:
    gesture: str
    finger_count: int = 1
    target: AssetRef | None = None
    animation: str = ""
    callback: str = ""
    fallback: str = ""
    collider: AssetRef | None = None
    track: int = 0
    sequence: tuple[AnimationStep, ...] = ()
    gate: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SceneDiagnostic:
    code: str
    message: str
    severity: str = "error"
    part_id: str = ""
    source: AssetRef | None = None
    details: tuple[tuple[str, str], ...] = ()


def _ref(ref: AssetRef | None) -> dict[str, Any] | None:
    return None if ref is None else ref.to_dict()


def _unref(value: dict[str, Any] | None) -> AssetRef | None:
    return None if value is None else AssetRef.from_dict(value)


def _unknown_fields(
    data: dict[str, Any], known: set[str], boundary: str, part_id: str = ""
) -> list[SceneDiagnostic]:
    return [
        SceneDiagnostic(
            "unknown_field",
            f"Unknown field at {boundary}: {key}",
            "warning",
            part_id=part_id,
            details=tuple(sorted([("boundary", boundary), ("field", key)] + ([ ("part_id", part_id) ] if part_id else []))),
        )
        for key in sorted(set(data) - known)
    ]


def _part_dict(part: ScenePart) -> dict[str, Any]:
    renderer, spine, sprite = part.renderer, part.spine, part.sprite
    return {
        "part_id": part.part_id, "object_ref": part.object_ref.to_dict(),
        "game_object_name": part.game_object_name, "hierarchy_path": part.hierarchy_path,
        "transform_chain": [{
            "object_ref": node.object_ref.to_dict(),
            "local_position": list(node.local_position),
            "local_rotation": list(node.local_rotation),
            "local_scale": list(node.local_scale),
            "sibling_index": node.sibling_index,
        } for node in part.transform_chain],
        "sorting_layer_value": part.sorting_layer_value,
        "renderer_depth": part.renderer_depth,
        "stable_order": part.stable_order,
        "renderer": None if renderer is None else {
            "kind": renderer.kind, "enabled": renderer.enabled, "sorting_layer": renderer.sorting_layer,
            "sorting_order": renderer.sorting_order, "transform": list(renderer.transform),
            "material": _ref(renderer.material), "materials": [item.to_dict() for item in renderer.materials],
            "mesh": _ref(renderer.mesh),
            "color": list(renderer.color), "flip_x": renderer.flip_x, "flip_y": renderer.flip_y,
            "mask_interaction": renderer.mask_interaction, "blend_mode": renderer.blend_mode,
        },
        "spine": None if spine is None else {
            "skeleton": spine.skeleton.to_dict(), "atlas": _ref(spine.atlas), "texture": _ref(spine.texture),
            "material": _ref(spine.material), "default_animation": spine.default_animation,
            "loop": spine.loop, "time_scale": spine.time_scale, "initial_skin": spine.initial_skin,
            "animations": list(spine.animations), "skins": list(spine.skins),
            "skeleton_json": _ref(spine.skeleton_json),
            "atlases": [item.to_dict() for item in spine.atlases],
            "skeleton_scale": spine.skeleton_scale,
            "animation_sequence": [{"animation": step.animation, "loop": step.loop,
                                    "evidence": step.evidence} for step in spine.animation_sequence],
        },
        "particle": None if part.particle is None else {
            "system": _ref(part.particle.system), "material": _ref(part.particle.material),
            "texture": _ref(part.particle.texture), "profile": part.particle.profile,
            "config_json": part.particle.config_json,
        },
        "sprite": None if sprite is None else {
            "sprite": sprite.sprite.to_dict(), "rect": list(sprite.rect), "pivot": list(sprite.pivot),
            "texture": _ref(sprite.texture), "pixels_per_unit": sprite.pixels_per_unit,
        },
    }


def _part(value: dict[str, Any]) -> ScenePart:
    r, s, p = value.get("renderer"), value.get("spine"), value.get("sprite")
    renderer = None if r is None else RendererState(
        str(r["kind"]), bool(r["enabled"]), str(r.get("sorting_layer", "Default")),
        int(r.get("sorting_order", 0)), tuple(map(float, r.get("transform", ()))), _unref(r.get("material")),
        tuple(AssetRef.from_dict(item) for item in r.get("materials", ())), _unref(r.get("mesh")),
        tuple(map(float, r.get("color", (1.0, 1.0, 1.0, 1.0)))),
        bool(r.get("flip_x", False)), bool(r.get("flip_y", False)),
        int(r.get("mask_interaction", 0)), str(r.get("blend_mode", "normal")),
    )
    spine = None if s is None else SpineState(
        AssetRef.from_dict(s["skeleton"]), _unref(s.get("atlas")), _unref(s.get("texture")),
        _unref(s.get("material")), str(s.get("default_animation", "")), bool(s.get("loop", False)),
        float(s.get("time_scale", 1)), str(s.get("initial_skin", "")),
        tuple(map(str, s.get("animations", ()))), tuple(map(str, s.get("skins", ()))),
        _unref(s.get("skeleton_json")),
        tuple(AssetRef.from_dict(item) for item in s.get("atlases", ())),
        float(s.get("skeleton_scale", 0.01)),
        tuple(AnimationStep(str(step["animation"]), bool(step["loop"]), str(step.get("evidence", "")))
              for step in s.get("animation_sequence", ())),
    )
    sprite = None if p is None else SpriteState(
        AssetRef.from_dict(p["sprite"]), tuple(map(float, p.get("rect", ()))),
        tuple(map(float, p.get("pivot", ()))), _unref(p.get("texture")),
        float(p.get("pixels_per_unit", 100.0)),
    )
    transform_chain = tuple(TransformState(
        AssetRef.from_dict(item["object_ref"]),
        tuple(map(float, item.get("local_position", (0.0, 0.0, 0.0)))),
        tuple(map(float, item.get("local_rotation", (0.0, 0.0, 0.0, 1.0)))),
        tuple(map(float, item.get("local_scale", (1.0, 1.0, 1.0)))),
        int(item.get("sibling_index", 0)),
    ) for item in value.get("transform_chain", ()))
    return ScenePart(
        str(value["part_id"]), AssetRef.from_dict(value["object_ref"]),
        str(value["game_object_name"]), str(value.get("hierarchy_path", "")),
        renderer, spine, sprite, transform_chain,
        int(value.get("sorting_layer_value", 0)), float(value.get("renderer_depth", 0.0)),
        int(value.get("stable_order", 0)),
        None if value.get("particle") is None else ParticleState(
            AssetRef.from_dict(value["particle"]["system"]), _unref(value["particle"].get("material")),
            _unref(value["particle"].get("texture")), str(value["particle"].get("profile", "unsupported")),
            str(value["particle"].get("config_json", "")),
        ),
    )


@dataclass(frozen=True, slots=True)
class PortraitScene:
    game_version: str
    version_timestamp: str
    entry_config_id: str
    character_id: str
    skin_id: str
    prefab: AssetRef | None
    source_bundles: tuple[str, ...]
    parts: tuple[ScenePart, ...]
    available_assets: tuple[AssetRef, ...] = ()
    interactions: tuple[InteractionEvent, ...] = ()
    diagnostics: tuple[SceneDiagnostic, ...] = ()
    source_bundle_details: tuple[SourceBundle, ...] = ()
    asset_dependencies: tuple[AssetDependency, ...] = ()
    prefab_container: str = ""
    schema_version: int = field(default=SCHEMA_VERSION)
    display_position: tuple[float, ...] | None = None
    config_metadata: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "game_version": self.game_version,
            "version_timestamp": self.version_timestamp, "entry_config_id": self.entry_config_id,
            "character_id": self.character_id, "skin_id": self.skin_id, "prefab": _ref(self.prefab),
            "prefab_container": self.prefab_container,
            "display_position": list(self.display_position) if self.display_position is not None else None,
            "config_metadata": [list(item) for item in self.config_metadata],
            "source_bundles": list(self.source_bundles), "parts": [_part_dict(item) for item in self.parts],
            "source_bundle_details": [{
                "bundle_hash": item.bundle_hash, "path": item.path,
                "dependencies": list(item.dependencies),
            } for item in self.source_bundle_details],
            "asset_dependencies": [{
                "source": item.source.to_dict(), "field_path": item.field_path,
                "target": item.target.to_dict(),
            } for item in self.asset_dependencies],
            "available_assets": [item.to_dict() for item in self.available_assets],
            "interactions": [{"gesture": i.gesture, "finger_count": i.finger_count, "target": _ref(i.target),
                              "animation": i.animation, "callback": i.callback, "fallback": i.fallback,
                              "collider": _ref(i.collider), "track": i.track,
                              "sequence": [{"animation": step.animation, "loop": step.loop, "evidence": step.evidence} for step in i.sequence],
                              "gate": i.gate, "evidence": list(i.evidence)} for i in self.interactions],
            "diagnostics": [{"code": d.code, "message": d.message, "severity": d.severity,
                             "part_id": d.part_id, "source": _ref(d.source), "details": [list(x) for x in d.details]}
                            for d in self.diagnostics],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortraitScene:
        if int(data.get("schema_version", -1)) not in {6, 7, SCHEMA_VERSION}:
            raise ValueError(f"Unsupported portrait scene schema_version: {data.get('schema_version')!r}")
        known = {"schema_version", "game_version", "version_timestamp", "entry_config_id", "character_id",
                 "skin_id", "prefab", "prefab_container", "source_bundles", "source_bundle_details", "asset_dependencies",
                 "parts", "available_assets", "interactions", "diagnostics", "display_position", "config_metadata"}
        diagnostics = [SceneDiagnostic(str(d["code"]), str(d.get("message", "")), str(d.get("severity", "error")),
                                       str(d.get("part_id", "")), _unref(d.get("source")),
                                       tuple((str(k), str(v)) for k, v in d.get("details", ())))
                       for d in data.get("diagnostics", ())]
        if int(data["schema_version"]) < SCHEMA_VERSION:
            diagnostics.append(SceneDiagnostic("portrait_engineering_upgrade_required",
                "Legacy scene has no verified particle capability evidence; rebuild from exact-version sources", "warning"))
        diagnostic_fields = {"code", "message", "severity", "part_id", "source", "details"}
        for item in data.get("diagnostics", ()):
            diagnostics.extend(_unknown_fields(item, diagnostic_fields, "diagnostic", str(item.get("part_id", ""))))
        diagnostics.extend(SceneDiagnostic("unknown_scene_field", f"Unknown scene field: {key}", "warning",
                                           details=(("field", key),)) for key in sorted(set(data) - known))
        interaction_fields = {"gesture", "finger_count", "target", "animation", "callback", "fallback", "collider", "track", "sequence", "gate", "evidence"}
        interactions_list = []
        for item in data.get("interactions", ()):
            diagnostics.extend(_unknown_fields(item, interaction_fields, "interaction"))
            interactions_list.append(InteractionEvent(str(item["gesture"]), int(item.get("finger_count", 1)),
                                              _unref(item.get("target")), str(item.get("animation", "")),
                                              str(item.get("callback", "")), str(item.get("fallback", "")),
                                              _unref(item.get("collider")), int(item.get("track", 0)),
                                              tuple(AnimationStep(str(step["animation"]), bool(step["loop"]), str(step.get("evidence", ""))) for step in item.get("sequence", ())),
                                              str(item.get("gate", "")), tuple(map(str, item.get("evidence", ())))))
        part_fields = {
            "part_id", "object_ref", "game_object_name", "hierarchy_path", "renderer", "spine", "sprite",
            "transform_chain", "sorting_layer_value", "renderer_depth", "stable_order", "particle",
        }
        nested_fields = {
            "particle": {"system", "material", "texture", "profile", "config_json"},
            "renderer": {"kind", "enabled", "sorting_layer", "sorting_order", "transform", "material",
                          "materials", "mesh", "color", "flip_x", "flip_y", "mask_interaction", "blend_mode"},
            "spine": {"skeleton", "atlas", "texture", "material", "default_animation", "loop", "time_scale",
                      "initial_skin", "animations", "skins", "skeleton_json", "atlases", "skeleton_scale", "animation_sequence"},
            "sprite": {"sprite", "rect", "pivot", "texture", "pixels_per_unit"},
        }
        parts = []
        for value in data.get("parts", ()):
            part_id = str(value.get("part_id", ""))
            diagnostics.extend(_unknown_fields(value, part_fields, "part", part_id))
            for boundary, fields in nested_fields.items():
                nested = value.get(boundary)
                if nested is not None:
                    diagnostics.extend(_unknown_fields(nested, fields, boundary, part_id))
            parts.append(_part(value))
        return cls(str(data["game_version"]), str(data["version_timestamp"]), str(data["entry_config_id"]),
                   str(data["character_id"]), str(data["skin_id"]), _unref(data.get("prefab")),
                   tuple(map(str, data.get("source_bundles", ()))), tuple(parts),
                   tuple(AssetRef.from_dict(x) for x in data.get("available_assets", ())),
                   tuple(interactions_list), tuple(diagnostics),
                   tuple(SourceBundle(
                       str(item["bundle_hash"]), str(item.get("path", "")),
                       tuple(map(str, item.get("dependencies", ()))),
                   ) for item in data.get("source_bundle_details", ())),
                   tuple(AssetDependency(
                       AssetRef.from_dict(item["source"]), str(item.get("field_path", "")),
                       AssetRef.from_dict(item["target"]),
                   ) for item in data.get("asset_dependencies", ())),
                   str(data.get("prefab_container", "")), SCHEMA_VERSION,
                   tuple(map(float, data["display_position"])) if data.get("display_position") is not None else None,
                   tuple((str(k), str(v)) for k, v in data.get("config_metadata", ())))

    def validate(self) -> tuple[SceneDiagnostic, ...]:
        retired = {"unsupported_spine_transform", "unsupported_spine_time_scale"}
        found = [d for d in self.diagnostics if d.code not in retired]
        if dict(self.config_metadata).get("export_status") == "not_applicable":
            return tuple(found)
        seen_ids: set[str] = set()
        seen_refs: set[AssetRef] = set()
        known_assets = set(self.available_assets)
        if self.prefab is None:
            if not self.prefab_container:
                found.append(SceneDiagnostic("missing_prefab", "Portrait scene has no resolved Prefab"))
        elif self.prefab not in known_assets:
            found.append(SceneDiagnostic("missing_asset", "Prefab is absent from the scene asset index", source=self.prefab))
        for part in self.parts:
            if not part.part_id or part.part_id in seen_ids:
                found.append(SceneDiagnostic("duplicate_part_id", f"Empty or duplicate part_id: {part.part_id!r}",
                                             part_id=part.part_id))
            seen_ids.add(part.part_id)
            if part.object_ref in seen_refs:
                found.append(SceneDiagnostic("duplicate_object_ref", "Unity object identity is duplicated",
                                             part_id=part.part_id, source=part.object_ref))
            seen_refs.add(part.object_ref)
            refs = [part.object_ref, *(node.object_ref for node in part.transform_chain)]
            if part.renderer:
                refs.extend(item for item in (part.renderer.material, part.renderer.mesh) if item)
                refs.extend(part.renderer.materials)
            if part.spine:
                refs.extend(x for x in (
                    part.spine.skeleton, part.spine.atlas, part.spine.texture, part.spine.material,
                    part.spine.skeleton_json, *part.spine.atlases,
                ) if x)
            if part.particle:
                refs.extend(ref for ref in (part.particle.system, part.particle.material, part.particle.texture) if ref)
                if part.particle.profile == "unsupported":
                    found.append(SceneDiagnostic("unsupported_particle", "Particle capability is unsupported", part_id=part.part_id, source=part.object_ref))
                if part.particle.profile != "unsupported":
                    from .particle_metadata import validate_config
                    try:
                        validate_config(part.particle.config_json, part.particle.profile)
                    except (ValueError, KeyError, TypeError, OverflowError) as error:
                        found.append(SceneDiagnostic("invalid_particle_config", str(error), part_id=part.part_id, source=part.object_ref))
                if part.particle.texture is None:
                    found.append(SceneDiagnostic("missing_particle_texture", "Particle has no verified main texture", part_id=part.part_id, source=part.object_ref))
            if part.sprite:
                refs.extend(x for x in (part.sprite.sprite, part.sprite.texture) if x)
                if part.sprite.texture is None:
                    found.append(SceneDiagnostic(
                        "missing_sprite_texture", "Sprite has no resolved exact Texture2D reference",
                        part_id=part.part_id, source=part.sprite.sprite,
                    ))
                if part.sprite.pixels_per_unit <= 0:
                    found.append(SceneDiagnostic(
                        "invalid_sprite_pixels_per_unit", "Sprite pixels-per-unit must be positive",
                        part_id=part.part_id, source=part.sprite.sprite,
                    ))
            if part.renderer and part.sprite:
                if part.renderer.mask_interaction != 0:
                    found.append(SceneDiagnostic(
                        "unsupported_sprite_mask", "SpriteRenderer mask interaction is not supported",
                        part_id=part.part_id, source=part.object_ref,
                    ))
                if part.renderer.blend_mode != "normal":
                    found.append(SceneDiagnostic(
                        "unsupported_sprite_blend", "SpriteRenderer blend mode is not supported",
                        part_id=part.part_id, source=part.object_ref,
                        details=(("blend_mode", part.renderer.blend_mode),),
                    ))
            if part.renderer and part.spine and part.renderer.blend_mode != "normal":
                diagnostic = SceneDiagnostic(
                    "unsupported_spine_blend", "Spine renderer material blend mode is not supported",
                    part_id=part.part_id, source=part.object_ref,
                    details=(("blend_mode", part.renderer.blend_mode),),
                )
                if diagnostic not in found:
                    found.append(diagnostic)
            for ref in refs:
                if ref not in known_assets:
                    found.append(SceneDiagnostic("missing_asset", "Referenced Unity asset is absent from the scene asset index",
                                                 part_id=part.part_id, source=ref))
        for event in self.interactions:
            if event.target is not None and event.target not in seen_refs:
                found.append(SceneDiagnostic("missing_interaction_target", "Interaction target is not a scene part",
                                             source=event.target))
            for ref in (event.target, event.collider):
                if ref is not None and ref not in known_assets:
                    found.append(SceneDiagnostic("missing_asset", "Interaction reference is absent from the scene asset index",
                                                 source=ref))
        for edge in self.asset_dependencies:
            for ref in (edge.source, edge.target):
                if ref not in known_assets:
                    found.append(SceneDiagnostic(
                        "missing_dependency_asset", "Dependency endpoint is absent from the scene asset index",
                        source=ref, details=(("field", edge.field_path),),
                    ))
        from .scene_geometry import part_affine
        for part in self.parts:
            if part.renderer is not None and not part.renderer.enabled:
                continue
            if part.spine or part.sprite or part.particle:
                try:
                    part_affine(part.transform_chain, part.spine.skeleton_scale if part.spine else 0.01)
                except ValueError as error:
                    found.append(SceneDiagnostic("invalid_part_transform", str(error), part_id=part.part_id,
                                                 source=part.object_ref))
            if part.spine and (not math.isfinite(part.spine.time_scale) or part.spine.time_scale < 0):
                found.append(SceneDiagnostic("invalid_spine_time_scale", "Part clock must be finite and non-negative",
                                             part_id=part.part_id, source=part.object_ref))
        return tuple(found)
