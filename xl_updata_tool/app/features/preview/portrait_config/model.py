"""Immutable and serializable normalized Unity portrait scene model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1


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


@dataclass(frozen=True, slots=True)
class SpriteState:
    sprite: AssetRef
    rect: tuple[float, ...] = ()
    pivot: tuple[float, ...] = ()
    texture: AssetRef | None = None


@dataclass(frozen=True, slots=True)
class ScenePart:
    part_id: str
    object_ref: AssetRef
    game_object_name: str
    hierarchy_path: str = ""
    renderer: RendererState | None = None
    spine: SpineState | None = None
    sprite: SpriteState | None = None


@dataclass(frozen=True, slots=True)
class InteractionEvent:
    gesture: str
    finger_count: int = 1
    target: AssetRef | None = None
    animation: str = ""
    callback: str = ""
    fallback: str = ""
    collider: AssetRef | None = None


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
        "renderer": None if renderer is None else {
            "kind": renderer.kind, "enabled": renderer.enabled, "sorting_layer": renderer.sorting_layer,
            "sorting_order": renderer.sorting_order, "transform": list(renderer.transform),
            "material": _ref(renderer.material),
        },
        "spine": None if spine is None else {
            "skeleton": spine.skeleton.to_dict(), "atlas": _ref(spine.atlas), "texture": _ref(spine.texture),
            "material": _ref(spine.material), "default_animation": spine.default_animation,
            "loop": spine.loop, "time_scale": spine.time_scale, "initial_skin": spine.initial_skin,
            "animations": list(spine.animations), "skins": list(spine.skins),
        },
        "sprite": None if sprite is None else {
            "sprite": sprite.sprite.to_dict(), "rect": list(sprite.rect), "pivot": list(sprite.pivot),
            "texture": _ref(sprite.texture),
        },
    }


def _part(value: dict[str, Any]) -> ScenePart:
    r, s, p = value.get("renderer"), value.get("spine"), value.get("sprite")
    renderer = None if r is None else RendererState(
        str(r["kind"]), bool(r["enabled"]), str(r.get("sorting_layer", "Default")),
        int(r.get("sorting_order", 0)), tuple(map(float, r.get("transform", ()))), _unref(r.get("material")),
    )
    spine = None if s is None else SpineState(
        AssetRef.from_dict(s["skeleton"]), _unref(s.get("atlas")), _unref(s.get("texture")),
        _unref(s.get("material")), str(s.get("default_animation", "")), bool(s.get("loop", False)),
        float(s.get("time_scale", 1)), str(s.get("initial_skin", "")),
        tuple(map(str, s.get("animations", ()))), tuple(map(str, s.get("skins", ()))),
    )
    sprite = None if p is None else SpriteState(
        AssetRef.from_dict(p["sprite"]), tuple(map(float, p.get("rect", ()))),
        tuple(map(float, p.get("pivot", ()))), _unref(p.get("texture")),
    )
    return ScenePart(str(value["part_id"]), AssetRef.from_dict(value["object_ref"]),
                     str(value["game_object_name"]), str(value.get("hierarchy_path", "")), renderer, spine, sprite)


@dataclass(frozen=True, slots=True)
class PortraitScene:
    game_version: str
    version_timestamp: str
    entry_config_id: str
    character_id: str
    skin_id: str
    prefab: AssetRef
    source_bundles: tuple[str, ...]
    parts: tuple[ScenePart, ...]
    available_assets: tuple[AssetRef, ...] = ()
    interactions: tuple[InteractionEvent, ...] = ()
    diagnostics: tuple[SceneDiagnostic, ...] = ()
    schema_version: int = field(default=SCHEMA_VERSION)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "game_version": self.game_version,
            "version_timestamp": self.version_timestamp, "entry_config_id": self.entry_config_id,
            "character_id": self.character_id, "skin_id": self.skin_id, "prefab": self.prefab.to_dict(),
            "source_bundles": list(self.source_bundles), "parts": [_part_dict(item) for item in self.parts],
            "available_assets": [item.to_dict() for item in self.available_assets],
            "interactions": [{"gesture": i.gesture, "finger_count": i.finger_count, "target": _ref(i.target),
                              "animation": i.animation, "callback": i.callback, "fallback": i.fallback,
                              "collider": _ref(i.collider)} for i in self.interactions],
            "diagnostics": [{"code": d.code, "message": d.message, "severity": d.severity,
                             "part_id": d.part_id, "source": _ref(d.source), "details": [list(x) for x in d.details]}
                            for d in self.diagnostics],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortraitScene:
        if int(data.get("schema_version", -1)) != SCHEMA_VERSION:
            raise ValueError(f"Unsupported portrait scene schema_version: {data.get('schema_version')!r}")
        known = {"schema_version", "game_version", "version_timestamp", "entry_config_id", "character_id",
                 "skin_id", "prefab", "source_bundles", "parts", "available_assets", "interactions", "diagnostics"}
        diagnostics = [SceneDiagnostic(str(d["code"]), str(d.get("message", "")), str(d.get("severity", "error")),
                                       str(d.get("part_id", "")), _unref(d.get("source")),
                                       tuple((str(k), str(v)) for k, v in d.get("details", ())))
                       for d in data.get("diagnostics", ())]
        diagnostic_fields = {"code", "message", "severity", "part_id", "source", "details"}
        for item in data.get("diagnostics", ()):
            diagnostics.extend(_unknown_fields(item, diagnostic_fields, "diagnostic", str(item.get("part_id", ""))))
        diagnostics.extend(SceneDiagnostic("unknown_scene_field", f"Unknown scene field: {key}", "warning",
                                           details=(("field", key),)) for key in sorted(set(data) - known))
        interaction_fields = {"gesture", "finger_count", "target", "animation", "callback", "fallback", "collider"}
        interactions_list = []
        for item in data.get("interactions", ()):
            diagnostics.extend(_unknown_fields(item, interaction_fields, "interaction"))
            interactions_list.append(InteractionEvent(str(item["gesture"]), int(item.get("finger_count", 1)),
                                              _unref(item.get("target")), str(item.get("animation", "")),
                                              str(item.get("callback", "")), str(item.get("fallback", "")),
                                              _unref(item.get("collider"))))
        part_fields = {"part_id", "object_ref", "game_object_name", "hierarchy_path", "renderer", "spine", "sprite"}
        nested_fields = {
            "renderer": {"kind", "enabled", "sorting_layer", "sorting_order", "transform", "material"},
            "spine": {"skeleton", "atlas", "texture", "material", "default_animation", "loop", "time_scale",
                      "initial_skin", "animations", "skins"},
            "sprite": {"sprite", "rect", "pivot", "texture"},
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
                   str(data["character_id"]), str(data["skin_id"]), AssetRef.from_dict(data["prefab"]),
                   tuple(map(str, data.get("source_bundles", ()))), tuple(parts),
                   tuple(AssetRef.from_dict(x) for x in data.get("available_assets", ())),
                   tuple(interactions_list), tuple(diagnostics), SCHEMA_VERSION)

    def validate(self) -> tuple[SceneDiagnostic, ...]:
        found = list(self.diagnostics)
        seen_ids: set[str] = set()
        seen_refs: set[AssetRef] = set()
        known_assets = set(self.available_assets)
        if self.prefab not in known_assets:
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
            refs = [part.object_ref]
            if part.renderer and part.renderer.material:
                refs.append(part.renderer.material)
            if part.spine:
                refs.extend(x for x in (part.spine.skeleton, part.spine.atlas, part.spine.texture, part.spine.material) if x)
            if part.sprite:
                refs.extend(x for x in (part.sprite.sprite, part.sprite.texture) if x)
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
        return tuple(found)
