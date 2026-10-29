import importlib
import math

import pytest

from app.features.preview.portrait_config.model import AssetRef, TransformState, SpineState


def geometry():
    return importlib.import_module('app.features.preview.portrait_config.scene_geometry')


def node(position=(0, 0, 0), rotation=(0, 0, 0, 1), scale=(1, 1, 1)):
    return TransformState(AssetRef('test', 1), position, rotation, scale, 0)


def test_mirror_and_relative_translation_are_preserved_in_spine_units():
    result = geometry().part_affine((node((2, 3, 0), scale=(-2, 3, 1)),), 0.02)
    assert result == pytest.approx((-4, 0, 0, 6, 200, 300))


def test_parent_rotation_composes_child_position_before_projecting():
    q = (0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4))
    result = geometry().part_affine((node((3, 4, 0), q), node((2, 0, 0))), 0.01, origin=(3, 4))
    assert result == pytest.approx((0, -1, 1, 0, 0, 200), abs=1e-10)


def test_non_planar_rotation_projects_into_xy_instead_of_rejecting():
    q = (math.sin(math.pi / 6), 0, 0, math.cos(math.pi / 6))
    assert geometry().part_affine((node(rotation=q),), 0.01) == pytest.approx((1, 0, 0, 0.5, 0, 0))


def test_degenerate_projection_and_nonfinite_transform_are_rejected():
    q = (math.sin(math.pi / 4), 0, 0, math.cos(math.pi / 4))
    with pytest.raises(ValueError, match='degenerate'):
        geometry().part_affine((node(rotation=q),), 0.01)
    with pytest.raises(ValueError, match='finite'):
        geometry().part_affine((node((float('nan'), 0, 0)),), 0.01)


def test_animation_sequence_survives_scene_serialization():
    from app.features.preview.portrait_config import model
    step = model.AnimationStep('in', False, 'Entrance.lua:1')
    spine = SpineState(AssetRef('test', 1), animation_sequence=(step, model.AnimationStep('idle', True)))
    part = model.ScenePart('part', AssetRef('test', 2), 'body', spine=spine)
    scene = model.PortraitScene('v', '123', 'skin', 'role', 'skin', None, (), (part,))
    restored = model.PortraitScene.from_dict(scene.to_dict())
    assert restored.parts[0].spine.animation_sequence == spine.animation_sequence


def test_legacy_schema_six_has_an_empty_sequence_and_preserves_assets():
    from app.features.preview.portrait_config import model
    scene = model.PortraitScene('v', '123', 'skin', 'role', 'skin', None, (), ())
    data = scene.to_dict()
    data['schema_version'] = 6
    assert model.PortraitScene.from_dict(data).skin_id == 'skin'
