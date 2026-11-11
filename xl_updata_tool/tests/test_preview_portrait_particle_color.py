import json
from types import SimpleNamespace
import unittest

from app.features.preview.portrait_config.particle_metadata import (
    normalize_material,
    normalize_system,
    validate_config,
)


def _tree(color_module=None, scaling_mode=1):
    return {
        'lengthInSec': 1.0,
        'looping': False,
        'simulationSpeed': 1.0,
        'scalingMode': scaling_mode,
        'moveWithTransform': 0,
        'ringBufferMode': 0,
        'InitialModule': {
            'maxNumParticles': 1,
            'startLifetime': {'minMaxState': 0, 'scalar': 1.0},
            'startSize': {'minMaxState': 0, 'scalar': 1.0},
            'startSpeed': {'minMaxState': 0, 'scalar': 0.0},
            'startRotation': {'minMaxState': 0, 'scalar': 0.0},
            'gravityModifier': {'minMaxState': 0, 'scalar': 0.0},
            'startColor': {
                'minMaxState': 0,
                'maxColor': {'r': 1.0, 'g': 1.0, 'b': 1.0, 'a': 1.0},
            },
        },
        'ShapeModule': {'enabled': False},
        'EmissionModule': {
            'enabled': True,
            'rateOverTime': {'minMaxState': 0, 'scalar': 0.0},
            'm_Bursts': [],
        },
        'UVModule': {'enabled': False},
        'ColorModule': color_module or {'enabled': False},
    }


def _renderer(alignment=2):
    return {
        'm_RenderMode': 0,
        'm_RenderAlignment': alignment,
        'm_MaskInteraction': 0,
        'm_UseCustomVertexStreams': False,
    }


def _transform(scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0, 1.0)):
    return SimpleNamespace(local_scale=scale, local_rotation=rotation)


def _gradient():
    return {
        'enabled': True,
        'gradient': {
            'minMaxState': 1,
            'maxGradient': {
                'key0': {'r': 1.0, 'g': 1.0, 'b': 1.0, 'a': 1.0},
                'key1': {'r': 1.0, 'g': 0.5613207817077637, 'b': 0.9133239388465881, 'a': 0.0},
                'key2': {'r': 1.0, 'g': 0.0, 'b': 0.9201374053955078, 'a': 0.0},
                'ctime0': 0,
                'ctime1': 22238,
                'ctime2': 65535,
                'atime0': 0,
                'atime1': 65535,
                'm_Mode': 0,
                'm_NumColorKeys': 3,
                'm_NumAlphaKeys': 2,
            },
        },
    }


def _material(dst_blend):
    return {
        'm_ValidKeywords': [
            '_ADD2BLENDMODE_ADD', '_ADD2P_OFF', '_ADD_CUSTOM_ON',
            '_DISSBLENDMODE_ADD', '_DISSP_OFF', '_DISS_CUSTOM_ON',
            '_MAINP_OFF', '_MASKP_OFF', '_NOISP_OFF',
        ],
        'm_SavedProperties': {
            'm_Floats': {
                '_SrcBlend': 5,
                '_DstBlend': dst_blend,
                '_MainPow_Min': 0,
                '_MainPow_Max': 1,
                '_ScrollX': 0,
                '_ScrollY': 0,
                '_MainRotate': 0,
                '_SoftParticlesEnabled': 0,
                '_CameraFadingEnabled': 0,
                '_DistortionEnabled': 0,
                '_MainPow': 1,
                '_Alpha': 1,
                '_AlphaRatio': 1,
                '_BlendAmount': 0,
            },
            'm_Colors': {
                '_TintColor': {'r': 1, 'g': 1, 'b': 1, 'a': 1},
                '_DarkColorExt': {'r': 1, 'g': 1, 'b': 1, 'a': 1},
            },
            'm_TexEnvs': {'_MainTex': {'m_Scale': {'x': 1, 'y': 1}, 'm_Offset': {'x': 0, 'y': 0}}},
        },
    }


class ParticleColorMetadataTests(unittest.TestCase):
    def test_constant_and_robin_gradient(self):
        constant = _tree({
            'enabled': True,
            'gradient': {'minMaxState': 0, 'maxColor': {'r': 0.5, 'g': 0.25, 'b': 1.0, 'a': 0.75}},
        })
        normalized = normalize_system(constant, _renderer(), 7, [_transform()])
        self.assertEqual(normalized['color_over_time'], {
            'mode': 'constant', 'color': [0.5, 0.25, 1.0, 0.75],
        })

        normalized = normalize_system(_tree(_gradient()), _renderer(), 7, [_transform()])
        over = normalized['color_over_time']
        self.assertEqual(over['mode'], 'gradient')
        self.assertEqual(over['color_times'], [0, 22238 / 65535, 1])
        self.assertEqual(over['alpha_times'], [0, 1])
        self.assertEqual(over['colors'], [
            [1, 1, 1],
            [1, 0.5613207817077637, 0.9133239388465881],
            [1, 0, 0.9201374053955078],
        ])
        self.assertEqual(over['alpha'], [1, 0])
        self.assertEqual(normalized['render_alignment'], 2)
        self.assertEqual(normalized['scaling_mode'], 1)

    def test_gradient_validation_rejects_invalid_inputs(self):
        for mutate in (
            lambda module: module['gradient']['maxGradient'].update(m_Mode=1),
            lambda module: module['gradient']['maxGradient'].update(m_NumColorKeys=0),
            lambda module: module['gradient']['maxGradient'].update(atime1=0),
            lambda module: module['gradient']['maxGradient']['key1'].update(g=2),
        ):
            module = _gradient()
            mutate(module)
            with self.subTest(mutate=mutate):
                with self.assertRaises(ValueError):
                    normalize_system(_tree(module), _renderer(), 7, [_transform()])

    def test_scaling_and_world_alignment_are_limited_to_evidence(self):
        normalized = normalize_system(
            _tree(_gradient()), _renderer(), 7,
            [_transform(rotation=(0.1, 0.2, 0.3, 0.4))],
        )
        self.assertEqual(normalized['scaling_mode'], 1)
        self.assertEqual(normalized['render_alignment'], 2)
        with self.assertRaisesRegex(ValueError, 'scalingMode'):
            normalize_system(_tree(_gradient()), _renderer(), 7, [_transform(scale=(2, 1, 1))])

    def test_initial_size_3d_is_limited_to_billboard_xy(self):
        tree = _tree()
        tree['InitialModule'].update({
            'size3D': True,
            'startSize': {'minMaxState': 3, 'scalar': 8.0, 'minScalar': 3.0},
            'startSizeY': {'minMaxState': 3, 'scalar': 2.25, 'minScalar': 1.0},
            'startSizeZ': {'minMaxState': 3, 'scalar': 1.0, 'minScalar': 1.0},
        })
        normalized = normalize_system(tree, _renderer(), 7, [_transform()])
        self.assertEqual(normalized['size'], [3, 8])
        self.assertEqual(normalized['size_y'], [1, 2.25])
        material = normalize_material(_material(1), 'Hym/Particles/hym_Dissolution_Nois_04', -4015514059479912215)
        validate_config(json.dumps({'version': 1, 'system': normalized, 'material': material}), 'additive_alpha_v1')

        tree['InitialModule']['startSizeZ']['scalar'] = 2
        with self.assertRaisesRegex(ValueError, 'startSizeZ'):
            normalize_system(tree, _renderer(), 7, [_transform()])
        tree['InitialModule']['startSizeZ']['scalar'] = 1
        tree['InitialModule']['rotation3D'] = True
        with self.assertRaisesRegex(ValueError, 'rotation3D'):
            normalize_system(tree, _renderer(), 7, [_transform()])

    def test_circle_edge_shape_uses_verified_arc_model(self):
        tree = _tree()
        tree['ShapeModule'] = {
            'enabled': True,
            'type': 10,
            'radiusThickness': 0.0,
            'm_Position': {'x': 0.0, 'y': 0.0, 'z': 0.0},
            'm_Rotation': {'x': 0.0, 'y': 0.0, 'z': 0.0},
            'm_Scale': {'x': 1.6, 'y': 1.0, 'z': 1.0},
            'radius': {'value': 5.73, 'mode': 0, 'spread': 0.0},
            'arc': {'value': 169.23, 'mode': 0, 'spread': 0.0},
            'alignToDirection': False,
        }
        normalized = normalize_system(tree, _renderer(), 7, [_transform()])
        self.assertEqual(normalized['circle_radius'], 5.73)
        self.assertAlmostEqual(normalized['circle_arc_radians'], 169.23 * 3.141592653589793 / 180)
        self.assertEqual(normalized['shape_size'], [1.6, 1, 1])
        material = normalize_material(_material(1), 'Hym/Particles/hym_Dissolution_Nois_04', -4015514059479912215)
        validate_config(json.dumps({'version': 1, 'system': normalized, 'material': material}), 'additive_alpha_v1')
        tree['ShapeModule']['radiusThickness'] = 0.5
        with self.assertRaisesRegex(ValueError, 'radiusThickness'):
            normalize_system(tree, _renderer(), 7, [_transform()])

    def test_ghost_multi_alpha_and_noise_gate(self):
        module = _gradient()
        gradient = module['gradient']['maxGradient']
        gradient.update({
            'key0': {'r': 1.0, 'g': 1.0, 'b': 1.0, 'a': 0.0},
            'key1': {'r': 1.0, 'g': 1.0, 'b': 1.0, 'a': 1.0},
            'key2': {'r': 0.0, 'g': 0.0, 'b': 0.0, 'a': 1.0},
            'key3': {'r': 0.0, 'g': 0.0, 'b': 0.0, 'a': 0.0},
            'ctime0': 0, 'ctime1': 65535,
            'atime0': 0, 'atime1': 20890, 'atime2': 39703, 'atime3': 65535,
            'm_NumColorKeys': 2, 'm_NumAlphaKeys': 4,
        })
        tree = _tree(module)
        over = normalize_system(tree, _renderer(), 7, [_transform()])['color_over_time']
        self.assertEqual(over['color_times'], [0, 1])
        self.assertEqual(over['colors'], [[1, 1, 1], [1, 1, 1]])
        self.assertEqual(over['alpha_times'], [0, 20890 / 65535, 39703 / 65535, 1])
        self.assertEqual(over['alpha'], [0, 1, 1, 0])
        tree['NoiseModule'] = {'enabled': True}
        with self.assertRaisesRegex(ValueError, 'NoiseModule'):
            normalize_system(tree, _renderer(), 7, [_transform()])

    def test_exact_dst_blend_profiles(self):
        additive = normalize_material(_material(1), 'Hym/Particles/hym_Dissolution_Nois_04', -4015514059479912215)
        self.assertEqual(additive['profile'], 'additive_alpha_v1')
        self.assertEqual(additive['factor'], 1)
        alpha = normalize_material(_material(10), 'Hym/Particles/hym_Dissolution_Nois_04', -4015514059479912215)
        self.assertEqual(alpha['profile'], 'alpha_tint_v1')
        with self.assertRaises(ValueError):
            normalize_material(_material(2), 'Hym/Particles/hym_Dissolution_Nois_04', -4015514059479912215)

    def test_config_validation_accepts_new_fields_and_rejects_bad_gradient(self):
        system = normalize_system(_tree(_gradient()), _renderer(), 7, [_transform()])
        material = normalize_material(_material(1), 'Hym/Particles/hym_Dissolution_Nois_04', -4015514059479912215)
        config = {'version': 1, 'system': system, 'material': material}
        validated = validate_config(json.dumps(config), 'additive_alpha_v1')
        self.assertEqual(validated['system']['render_alignment'], 2)
        config['system']['color_over_time']['alpha_times'] = [0, 0.5]
        with self.assertRaisesRegex(ValueError, 'alpha'):
            validate_config(json.dumps(config), 'additive_alpha_v1')

    def test_legacy_config_without_new_optional_fields(self):
        system = normalize_system(_tree(), _renderer(alignment=0), 7, [_transform()])
        for key in ('color_over_time', 'render_alignment', 'scaling_mode'):
            system.pop(key, None)
        material = normalize_material(_material(1), 'Hym/Particles/hym_Dissolution_Nois_04', -4015514059479912215)
        validated = validate_config(json.dumps({'version': 1, 'system': system, 'material': material}), 'additive_alpha_v1')
        self.assertNotIn('color_over_time', validated['system'])


if __name__ == '__main__':
    unittest.main()
