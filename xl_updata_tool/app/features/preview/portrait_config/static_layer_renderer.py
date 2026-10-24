"""Create one-frame Spine inputs for Unity SpriteRenderers in mixed scenes."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image


class UnsupportedSpriteRenderFeature(ValueError):
    """A SpriteRenderer uses an effect this renderer cannot faithfully reproduce."""

    code = "SPRITE_RENDER_FEATURE_UNSUPPORTED"

    def __init__(self, message: str, *, part_id: str = "") -> None:
        self.part_id = part_id
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


def _world_transform(spec: StaticLayerSpec) -> tuple[float, float, float, float, float]:
    """Compose Unity XY transforms and reject 3-D or sheared sprite planes."""
    a, b, c, d, tx, ty = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
    for node in spec.transform_chain:
        x, y, z, w = tuple(map(float, node.local_rotation))
        if abs(x) > 1e-6 or abs(y) > 1e-6:
            raise UnsupportedSpriteRenderFeature(
                "SpriteRenderer has an out-of-plane rotation", part_id=spec.part_id,
            )
        angle = 2.0 * math.atan2(z, w)
        sx, sy, sz = tuple(map(float, node.local_scale))
        if abs(sz) < 1e-9:
            raise ValueError(f"Sprite part {spec.part_id} has a zero Z scale")
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        la, lb, lc, ld = cos_a * sx, -sin_a * sy, sin_a * sx, cos_a * sy
        px, py, _ = tuple(map(float, node.local_position))
        tx, ty = a * px + b * py + tx, c * px + d * py + ty
        a, b, c, d = a * la + b * lc, a * lb + b * ld, c * la + d * lc, c * lb + d * ld

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
    width, height = crop.size
    pivot_x, pivot_y = tuple(map(float, spec.pivot))
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in (pivot_x, pivot_y)):
        raise ValueError(f"Sprite part {spec.part_id} has invalid normalized pivot")
    tx, ty, rotation, scale_x, scale_y = _world_transform(spec)
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
            "name": "root", "x": world_x * spec.pixels_per_unit,
            "y": world_y * spec.pixels_per_unit, "rotation": rotation,
            "scaleX": scale_x, "scaleY": scale_y,
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
