"""Create one-frame Spine inputs for Unity SpriteRenderers in mixed scenes."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops


class UnsupportedSpriteRenderFeature(ValueError):
    """A SpriteRenderer uses an effect this renderer cannot faithfully reproduce."""

    code = "SPRITE_RENDER_FEATURE_UNSUPPORTED"

    def __init__(self, message: str, *, part_id: str = "") -> None:
        self.part_id = part_id
        super().__init__(message)


class UnsupportedSpineTransform(ValueError):
    """Native CLI merge cannot place these Spine sources in one coordinate space."""

    code = "SPINE_TRANSFORM_UNSUPPORTED"

    def __init__(self, message: str, part: Any, *, reference_part_id: str = "") -> None:
        from .model import SceneDiagnostic

        self.part_id = part.part_id
        self.diagnostic = SceneDiagnostic(
            "unsupported_spine_transform", message, part_id=part.part_id,
            source=part.object_ref,
            details=(("reference_part_id", reference_part_id),),
        )
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class StaticLayerSpec:
    part_id: str
    texture_path: Path
    rect: tuple[float, ...]
    pivot: tuple[float, ...]
    pixels_per_unit: float
    transform_chain: tuple[Any, ...]
    unity_order: tuple[int, int, float, int]
    color: tuple[float, ...] = (1.0, 1.0, 1.0, 1.0)
    flip_x: bool = False
    flip_y: bool = False
    mask_interaction: int = 0
    blend_mode: str = "normal"
    spine_transform_chain: tuple[Any, ...] = ()
    skeleton_scale: float = 0.01
    pma: bool = False


@dataclass(frozen=True, slots=True)
class GeneratedStaticLayer:
    part_id: str
    skeleton_path: Path
    atlas_path: Path
    texture_path: Path
    unity_order: tuple[int, int, float, int]


def order_render_sources(sources):
    """Return merge inputs front-to-back, reversing Unity sibling tie order."""

    def sort_key(source):
        if isinstance(source, dict):
            return tuple(source["order"])
        return tuple(source.unity_order)

    return tuple(sorted(sources, key=sort_key, reverse=True))


def _rgba_crop(spec: StaticLayerSpec) -> Image.Image:
    if spec.mask_interaction:
        raise UnsupportedSpriteRenderFeature(
            f"SpriteRenderer mask interaction {spec.mask_interaction} is unsupported",
            part_id=spec.part_id,
        )
    if str(spec.blend_mode).casefold() != "normal":
        raise UnsupportedSpriteRenderFeature(
            f"SpriteRenderer blend mode {spec.blend_mode!r} is unsupported",
            part_id=spec.part_id,
        )
    if len(spec.rect) != 4 or len(spec.pivot) != 2:
        raise ValueError(f"Sprite part {spec.part_id} has incomplete rect or pivot metadata")
    if not math.isfinite(spec.pixels_per_unit) or spec.pixels_per_unit <= 0:
        raise ValueError(f"Sprite part {spec.part_id} has invalid pixels-per-unit")
    x, y, width, height = map(float, spec.rect)
    if not all(math.isfinite(value) for value in (x, y, width, height)) or width <= 0 or height <= 0:
        raise ValueError(f"Sprite part {spec.part_id} has an invalid texture rect")
    left, bottom = math.floor(x + 1e-6), math.floor(y + 1e-6)
    right, top = math.ceil(x + width - 1e-6), math.ceil(y + height - 1e-6)
    if right <= left or top <= bottom:
        raise ValueError(f"Sprite part {spec.part_id} has an empty texture rect")
    with Image.open(spec.texture_path) as texture:
        image = texture.convert("RGBA")
    if left < 0 or bottom < 0 or right > image.width or top > image.height:
        raise ValueError(
            f"Sprite part {spec.part_id} rect {(left, bottom, right, top)} exceeds texture {image.size}"
        )
    crop = image.crop((left, image.height - top, right, image.height - bottom))
    if spec.flip_x:
        crop = crop.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if spec.flip_y:
        crop = crop.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    color = tuple(map(float, spec.color))
    if len(color) != 4 or any(not math.isfinite(value) or value < 0 or value > 1 for value in color):
        raise ValueError(f"Sprite part {spec.part_id} has invalid renderer color")
    if color != (1.0, 1.0, 1.0, 1.0):
        channels = crop.split()
        crop = Image.merge("RGBA", tuple(
            channel.point(lambda value, factor=factor: max(0, min(255, round(value * factor))))
            for channel, factor in zip(channels, color, strict=True)
        ))
    return crop


def _xy_transform(chain: tuple[Any, ...], part_id: str) -> tuple[float, ...]:
    """Compose the full XY affine matrix, before choosing the shared local frame."""
    a, b, c, d, tx, ty = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
    for node in chain:
        x, y, z, w = tuple(map(float, node.local_rotation))
        if abs(x) > 1e-6 or abs(y) > 1e-6:
            raise UnsupportedSpriteRenderFeature(
                "Renderer has an out-of-plane rotation", part_id=part_id,
            )
        angle = 2.0 * math.atan2(z, w)
        sx, sy, sz = tuple(map(float, node.local_scale))
        if abs(sz) < 1e-9:
            raise ValueError(f"Renderer part {part_id} has a zero Z scale")
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        la, lb, lc, ld = cos_a * sx, -sin_a * sy, sin_a * sx, cos_a * sy
        px, py, _ = tuple(map(float, node.local_position))
        tx, ty = a * px + b * py + tx, c * px + d * py + ty
        a, b, c, d = a * la + b * lc, a * lb + b * ld, c * la + d * lc, c * lb + d * ld

    if not all(math.isfinite(value) for value in (a, b, c, d, tx, ty)):
        raise ValueError(f"Renderer part {part_id} has a non-finite XY transform")
    if abs(a * d - b * c) < 1e-12:
        raise ValueError(f"Renderer part {part_id} has a zero XY scale")
    return a, b, c, d, tx, ty


def common_spine_coordinate_space(parts) -> tuple[tuple[Any, ...], float]:
    """Return the common Spine frame, rejecting placement the CLI cannot merge.

    A common world transform can be removed from every source. Distinct Spine
    frames cannot: the CLI loads the skeletons' raw coordinates without Unity's
    per-renderer transforms or SkeletonDataAsset scaling.
    """
    reference = None
    reference_matrix = ()
    for part in parts:
        if part.spine is None or (part.renderer is not None and not part.renderer.enabled):
            continue
        scale = float(part.spine.skeleton_scale)
        if not math.isfinite(scale) or scale <= 0:
            raise UnsupportedSpineTransform("SkeletonDataAsset scale must be finite and positive", part)
        try:
            matrix = _xy_transform(part.transform_chain, part.part_id)
        except ValueError as error:
            raise UnsupportedSpineTransform(str(error), part) from error
        if reference is None:
            reference, reference_matrix = part, matrix
            continue
        if not math.isclose(scale, reference.spine.skeleton_scale, rel_tol=1e-7, abs_tol=1e-12) or not all(
            math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-7)
            for actual, expected in zip(matrix, reference_matrix, strict=True)
        ):
            raise UnsupportedSpineTransform(
                "SpineViewerCLI merge cannot represent different per-part Spine transforms or SkeletonDataAsset scales",
                part, reference_part_id=reference.part_id,
            )
    if reference is None:
        return (), 0.01
    return reference.transform_chain, float(reference.spine.skeleton_scale)


def _world_transform(spec: StaticLayerSpec) -> tuple[float, float, float, float, float]:
    """Express Sprite geometry in the shared Spine frame and reject relative shear."""
    a, b, c, d, tx, ty = _xy_transform(spec.transform_chain, spec.part_id)
    ra, rb, rc, rd, rx, ry = _xy_transform(spec.spine_transform_chain, spec.part_id)
    determinant = ra * rd - rb * rc
    a, b, c, d, tx, ty = (
        (rd * a - rb * c) / determinant,
        (rd * b - rb * d) / determinant,
        (ra * c - rc * a) / determinant,
        (ra * d - rc * b) / determinant,
        (rd * (tx - rx) - rb * (ty - ry)) / determinant,
        (ra * (ty - ry) - rc * (tx - rx)) / determinant,
    )

    x_axis_length = math.hypot(a, c)
    y_axis_length = math.hypot(b, d)
    if x_axis_length < 1e-9 or y_axis_length < 1e-9:
        raise ValueError(f"Sprite part {spec.part_id} has a zero XY scale")
    dot = (a * b + c * d) / (x_axis_length * y_axis_length)
    if abs(dot) > 1e-5:
        raise UnsupportedSpriteRenderFeature(
            "SpriteRenderer transform contains shear that Spine region attachments cannot represent",
            part_id=spec.part_id,
        )
    rotation = math.degrees(math.atan2(c, a))
    signed_y_scale = y_axis_length if (a * d - b * c) >= 0 else -y_axis_length
    return tx, ty, rotation, x_axis_length, signed_y_scale


def generate_static_layer(spec: StaticLayerSpec, work_dir: str | Path) -> GeneratedStaticLayer:
    """Crop/tint a Sprite texture and serialize it as a one-frame Spine layer."""
    crop = _rgba_crop(spec)
    if spec.pma:
        red, green, blue, alpha = crop.split()
        crop = Image.merge("RGBA", (
            ImageChops.multiply(red, alpha), ImageChops.multiply(green, alpha),
            ImageChops.multiply(blue, alpha), alpha,
        ))
    width, height = crop.size
    pivot_x, pivot_y = tuple(map(float, spec.pivot))
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in (pivot_x, pivot_y)):
        raise ValueError(f"Sprite part {spec.part_id} has invalid normalized pivot")
    tx, ty, rotation, scale_x, scale_y = _world_transform(spec)
    if not math.isfinite(spec.skeleton_scale) or spec.skeleton_scale <= 0:
        raise ValueError(f"Sprite part {spec.part_id} has invalid SkeletonDataAsset scale")
    center_x = (0.5 - pivot_x) * width / spec.pixels_per_unit
    center_y = (0.5 - pivot_y) * height / spec.pixels_per_unit
    if spec.flip_x:
        center_x = -center_x
    if spec.flip_y:
        center_y = -center_y
    radians = math.radians(rotation)
    world_x = tx + math.cos(radians) * scale_x * center_x - math.sin(radians) * scale_y * center_y
    world_y = ty + math.sin(radians) * scale_x * center_x + math.cos(radians) * scale_y * center_y

    layer_key = hashlib.sha256(spec.part_id.encode("utf-8")).hexdigest()[:16]
    layer_name = f"static_{layer_key}"
    output_dir = Path(work_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    texture_path = output_dir / f"{layer_name}.png"
    skeleton_path = output_dir / f"{layer_name}.json"
    atlas_path = output_dir / f"{layer_name}.atlas"
    crop.save(texture_path, format="PNG")

    skeleton = {
        "skeleton": {
            "hash": layer_name, "spine": "4.1.24", "x": 0, "y": 0,
            "width": width, "height": height, "images": "./",
        },
        "bones": [{
            "name": "root", "x": world_x / spec.skeleton_scale,
            "y": world_y / spec.skeleton_scale, "rotation": rotation,
            "scaleX": scale_x / (spec.pixels_per_unit * spec.skeleton_scale),
            "scaleY": scale_y / (spec.pixels_per_unit * spec.skeleton_scale),
        }],
        "slots": [{"name": "sprite-layer", "bone": "root", "attachment": layer_name}],
        "skins": [{"name": "default", "attachments": {
            "sprite-layer": {layer_name: {
                "type": "region", "path": layer_name,
                "x": 0, "y": 0, "width": width, "height": height,
            }},
        }}],
        "animations": {"idle": {}},
    }
    skeleton_path.write_text(json.dumps(skeleton, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    atlas_path.write_text(
        f"{layer_name}.png\nsize:{width},{height}\nfilter:Linear,Linear\nrepeat:none\n"
        f"{layer_name}\nbounds:0,0,{width},{height}\n",
        encoding="utf-8",
    )
    return GeneratedStaticLayer(spec.part_id, skeleton_path, atlas_path, texture_path, spec.unity_order)
