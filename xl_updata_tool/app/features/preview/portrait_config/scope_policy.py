"""Required portrait inputs, independent of auxiliary Unity shader effects."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .model import AssetRef, PortraitScene

if TYPE_CHECKING:
    from .asset_index import AssetIndex


def auxiliary_texture_property(name: str) -> bool:
    """Classify a serialized Material property, never an asset filename."""
    normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
    return any(token in normalized for token in (
        "bump", "normalmap", "emission", "noise", "flow", "mask", "distort", "displacement",
    ))


def required_dependency_field(field_path: str) -> bool:
    """Allow only the skeleton/atlas/base-image graph, not arbitrary scripts."""
    field = field_path.casefold()
    if "m_texenvs." in field:
        property_name = field.split("m_texenvs.", 1)[1].split(".", 1)[0]
        return field.endswith(".m_texture") and not auxiliary_texture_property(property_name)
    if "alphatexture" in field or "secondarytextures" in field:
        return False
    return any(token in field for token in (
        "skeletondataasset", "skeletonjson", "atlasassets", "atlasfile", "m_materials",
        "m_sprite", "m_rd.texture", "m_renderdata.texture",
    )) or field in {"texture", "maintexture"}


def required_scene_assets(scene: PortraitScene, index: AssetIndex) -> tuple[AssetRef, ...]:
    """Return exact TextAsset/Texture2D inputs of enabled portrait parts.

    Metadata objects are traversal nodes only. Unrelated catalog/container
    assets and auxiliary shader textures never become extraction inputs.
    """
    if str(scene.version_timestamp) != str(index.version_timestamp):
        raise ValueError("portrait scene and asset index versions differ")
    pending: list[AssetRef] = []
    explicit_inputs: set[AssetRef] = set()
    for part in scene.parts:
        if part.renderer is not None and not part.renderer.enabled:
            continue
        if part.spine:
            spine = part.spine
            explicit_inputs.update(ref for ref in (
                spine.skeleton_json, spine.atlas, spine.texture, *spine.atlases,
            ) if ref is not None)
            pending.extend(ref for ref in (
                spine.skeleton, spine.skeleton_json, spine.atlas, spine.texture, spine.material,
                *spine.atlases,
            ) if ref is not None)
            if part.renderer:
                pending.extend(part.renderer.materials)
                if part.renderer.material:
                    pending.append(part.renderer.material)
        if part.particle and part.particle.profile != "unsupported" and part.particle.texture:
            explicit_inputs.add(part.particle.texture)
            pending.append(part.particle.texture)
        if part.sprite:
            pending.extend(ref for ref in (part.sprite.sprite, part.sprite.texture) if ref is not None)
            if part.sprite.texture:
                explicit_inputs.add(part.sprite.texture)
    outgoing: dict[AssetRef, list[AssetRef]] = {}
    for edge in scene.asset_dependencies:
        if required_dependency_field(edge.field_path):
            outgoing.setdefault(edge.source, []).append(edge.target)
    seen: set[AssetRef] = set()
    required: set[AssetRef] = set()
    while pending:
        ref = pending.pop()
        if ref in seen:
            continue
        seen.add(ref)
        matches = index.find_exact(ref.bundle_hash, ref.path_id)
        if not matches and ref in explicit_inputs:
            raise ValueError(f"required portrait asset missing: {ref}")
        if len(matches) > 1:
            raise ValueError(f"ambiguous required portrait asset: {ref}")
        if matches and matches[0].asset_type in {"TextAsset", "Texture2D"}:
            if not matches[0].bundle_present:
                raise ValueError(f"required portrait bundle missing: {ref}")
            required.add(ref)
        pending.extend(outgoing.get(ref, ()))
    return tuple(sorted(required))
