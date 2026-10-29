import json
import math

import pytest

from PIL import Image

from app.features.preview.portrait_config.model import TransformState, AssetRef
from app.features.preview.portrait_config.static_layer_renderer import (
    StaticLayerSpec,
    generate_static_layer,
    order_render_sources,
)


def _transform(position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0), sibling=0):
    return TransformState(
        AssetRef("bundle", sibling + 1), position, rotation, scale, sibling,
    )


def test_generated_static_layer_crops_sprite_rect_and_preserves_rgba(tmp_path):
    texture = Image.new("RGBA", (4, 4), (1, 2, 3, 0))
    texture.putpixel((1, 1), (255, 0, 0, 128))
    texture.putpixel((2, 1), (0, 255, 0, 255))
    texture.putpixel((1, 2), (0, 0, 255, 64))
    texture.putpixel((2, 2), (255, 255, 0, 200))
    texture_path = tmp_path / "atlas.png"
    texture.save(texture_path)
    spec = StaticLayerSpec(
        part_id="SpriteRenderer:bundle:7",
        texture_path=texture_path,
        rect=(1, 1, 2, 2),
        pivot=(0.5, 0.5),
        pixels_per_unit=100.0,
        transform_chain=(_transform(),),
        unity_order=(0, 3, 0.0, 4),
    )

    generated = generate_static_layer(spec, tmp_path / "generated")

    with Image.open(generated.texture_path) as cropped:
        assert cropped.size == (2, 2)
        assert cropped.getpixel((0, 0)) == (255, 0, 0, 128)
        assert cropped.getpixel((1, 0)) == (0, 255, 0, 255)
        assert cropped.getpixel((0, 1)) == (0, 0, 255, 64)
        assert cropped.getpixel((1, 1)) == (255, 255, 0, 200)
    skeleton = json.loads(generated.skeleton_path.read_text(encoding="utf-8"))
    attachment_name = skeleton["slots"][0]["attachment"]
    assert skeleton["skins"][0]["attachments"]["sprite-layer"][attachment_name]["width"] == 2
    assert generated.atlas_path.is_file()


def test_generated_sprite_texture_matches_scene_premultiplied_alpha_mode(tmp_path):
    texture_path = tmp_path / "source.png"
    Image.new("RGBA", (2, 2), (100, 50, 20, 128)).save(texture_path)
    generated = generate_static_layer(StaticLayerSpec(
        part_id="half-transparent", texture_path=texture_path, rect=(0, 0, 2, 2),
        pivot=(0.5, 0.5), pixels_per_unit=100.0, transform_chain=(),
        unity_order=(0, 0, 0.0, 0), pma=True,
    ), tmp_path / "generated")
    with Image.open(generated.texture_path) as image:
        assert image.getpixel((0, 0)) == (50, 25, 10, 128)


def test_generated_static_layer_applies_unity_pivot_transform_and_renderer_alpha(tmp_path):
    texture_path = tmp_path / "sprite.png"
    Image.new("RGBA", (20, 10), (200, 100, 50, 255)).save(texture_path)
    half_turn = math.sqrt(0.5)
    spec = StaticLayerSpec(
        part_id="sprite-rotated",
        texture_path=texture_path,
        rect=(0, 0, 20, 10),
        pivot=(0.25, 0.5),
        pixels_per_unit=100.0,
        transform_chain=(
            _transform(position=(1.0, 2.0, 0.0), rotation=(0.0, 0.0, half_turn, half_turn), scale=(2.0, 2.0, 1.0)),
        ),
        unity_order=(0, 1, 0.0, 2),
        color=(1.0, 1.0, 1.0, 0.5),
    )

    generated = generate_static_layer(spec, tmp_path / "generated")

    with Image.open(generated.texture_path) as rendered:
        assert rendered.getpixel((10, 5)) == (200, 100, 50, 128)
    skeleton = json.loads(generated.skeleton_path.read_text(encoding="utf-8"))
    root = skeleton["bones"][0]
    assert root["x"] == 100
    assert root["y"] == 210
    assert root["scaleX"] == 2
    assert root["scaleY"] == 2
    assert root["rotation"] == 90


def test_static_layer_rejects_unsupported_mask_and_blend_mode(tmp_path):
    texture_path = tmp_path / "sprite.png"
    Image.new("RGBA", (2, 2), (10, 20, 30, 255)).save(texture_path)
    base = dict(
        part_id="masked-sprite",
        texture_path=texture_path,
        rect=(0, 0, 2, 2),
        pivot=(0.5, 0.5),
        pixels_per_unit=100.0,
        transform_chain=(_transform(),),
        unity_order=(0, 0, 0.0, 0),
    )

    from app.features.preview.portrait_config.static_layer_renderer import UnsupportedSpriteRenderFeature

    for spec in (
        StaticLayerSpec(**base, mask_interaction=1),
        StaticLayerSpec(**base, blend_mode="additive"),
    ):
        try:
            generate_static_layer(spec, tmp_path / "generated")
        except UnsupportedSpriteRenderFeature as error:
            assert error.code == "SPRITE_RENDER_FEATURE_UNSUPPORTED"
        else:
            raise AssertionError("unsupported renderer feature was silently rendered")


def test_merge_source_order_is_front_to_back_and_stable_for_sort_ties():
    sources = (
        {"name": "spine-low", "order": (0, 2, 0.0, 1)},
        {"name": "sprite-sibling-early", "order": (0, 3, 0.0, 2)},
        {"name": "sprite-sibling-late", "order": (0, 3, 0.0, 5)},
        {"name": "spine-high", "order": (0, 4, 0.0, 0)},
    )

    ordered = order_render_sources(sources)

    assert [source["name"] for source in ordered] == [
        "spine-high", "sprite-sibling-late", "sprite-sibling-early", "spine-low",
    ]


@pytest.mark.parametrize(("ppu", "skeleton_scale", "expected_width"), [
    (100.0, 0.01, 640.0), (100.0, 0.02, 320.0), (200.0, 0.01, 320.0),
])
def test_sprite_pixels_convert_to_raw_spine_units(tmp_path, ppu, skeleton_scale, expected_width):
    texture_path = tmp_path / "sprite.png"
    Image.new("RGBA", (640, 20), "white").save(texture_path)
    spec = StaticLayerSpec(
        "sprite", texture_path, (0, 0, 640, 20), (0.5, 0.5), ppu,
        (), (0, 0, 0.0, 0), skeleton_scale=skeleton_scale,
    )

    generated = generate_static_layer(spec, tmp_path / "generated")

    skeleton = json.loads(generated.skeleton_path.read_text(encoding="utf-8"))
    root = skeleton["bones"][0]
    name = skeleton["slots"][0]["attachment"]
    region = skeleton["skins"][0]["attachments"]["sprite-layer"][name]
    assert root["scaleX"] * region["width"] == pytest.approx(expected_width)


def test_sprite_transform_is_relative_to_full_spine_transform_and_preserves_pivot_and_flips(tmp_path):
    texture_path = tmp_path / "sprite.png"
    Image.new("RGBA", (20, 10), "white").save(texture_path)
    q = math.sqrt(0.5)
    common = _transform(position=(10, 20, 0), rotation=(0, 0, q, q), scale=(3, 3, 1))
    spine_child = _transform(position=(1, 2, 0), scale=(2, 2, 1))
    sprite_child = _transform(position=(5, 8, 0), rotation=(0, 0, q, q), scale=(4, 4, 1))
    spec = StaticLayerSpec(
        "sprite", texture_path, (0, 0, 20, 10), (0.25, 0.5), 100.0,
        (common, sprite_child), (0, 0, 0.0, 0), flip_x=True,
        spine_transform_chain=(common, spine_child), skeleton_scale=0.01,
    )

    generated = generate_static_layer(spec, tmp_path / "generated")

    root = json.loads(generated.skeleton_path.read_text(encoding="utf-8"))["bones"][0]
    # inverse Spine(child(1,2), scale2) maps Sprite(5,8) to (2,3).
    # Flipped quarter pivot moves its center -0.05*2=-0.1 Unity Y after 90deg.
    assert root["x"] == pytest.approx(200.0)
    assert root["y"] == pytest.approx(290.0)
    assert root["rotation"] == pytest.approx(90.0)
    assert root["scaleX"] == pytest.approx(2.0)
    assert root["scaleY"] == pytest.approx(2.0)
