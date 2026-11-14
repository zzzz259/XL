import json
import unittest

from test_preview_portrait_particle_color import _gradient, _renderer, _transform, _tree
from app.features.preview.portrait_config.particle_metadata import (
    normalize_material,
    normalize_system,
    validate_config,
)


def _key(time, value, in_slope=0.0, out_slope=0.0):
    return {
        'time': time, 'value': value, 'inSlope': in_slope, 'outSlope': out_slope,
        'weightedMode': 0, 'inWeight': 1 / 3, 'outWeight': 1 / 3,
    }


def _curve(points, mode=1, scalar=1.0, min_points=None):
    return {
        'minMaxState': mode,
        'scalar': scalar,
        'minScalar': 0.0,
        'maxCurve': {'m_Curve': [_key(*point) for point in points]},
        'minCurve': {'m_Curve': [_key(*point) for point in (min_points or [])]},
    }


def _pass_state(src=5, dst=10):
    blend = {'val': 0.0, 'name': '<noninit>'}
    return {
        'm_ParsedForm': {
            'm_SubShaders': [{
                'm_Passes': [{
                    'm_State': {
                        'rtBlend0': {
                            'srcBlend': {'val': src, 'name': '<noninit>'},
                            'destBlend': {'val': dst, 'name': '<noninit>'},
                            'srcBlendAlpha': {'val': src, 'name': '<noninit>'},
                            'destBlendAlpha': {'val': dst, 'name': '<noninit>'},
                            'blendOp': blend,
                            'blendOpAlpha': blend,
                            'colMask': {'val': 15, 'name': '<noninit>'},
                        },
                        'zWrite': {'val': 0, 'name': '<noninit>'},
                        'lighting': False,
                    },
                }],
            }],
        },
    }


def _alpha_blend_material():
    return {
        'm_ValidKeywords': [],
        'm_SavedProperties': {
            'm_Floats': {'_SrcBlend': 1, '_DstBlend': 0, '_AlphaRatio': 1, '_Cutoff': 0.5},
            'm_Colors': {
                '_TintColor': {'r': 0.5, 'g': 0.5, 'b': 0.5, 'a': 0.5},
                '_DarkColorExt': {'r': 1, 'g': 1, 'b': 1, 'a': 1},
            },
            'm_TexEnvs': {'_MainTex': {'m_Scale': {'x': 1, 'y': 1}, 'm_Offset': {'x': 0, 'y': 0}}},
        },
    }


class ParticleDynamicsMetadataTests(unittest.TestCase):
    def test_two_constant_start_colors(self):
        tree = _tree()
        tree['InitialModule']['startColor'] = {
            'minMaxState': 2,
            'minColor': {'r': 1, 'g': 0.9318456649780273, 'b': 0.24056601524353027, 'a': 1},
            'maxColor': {'r': 1, 'g': 1, 'b': 1, 'a': 1},
        }
        normalized = normalize_system(tree, _renderer(), 7, [_transform()])
        self.assertEqual(normalized['start_color']['mode'], 'two_colors')
        self.assertEqual(normalized['start_color']['min'][1], 0.9318456649780273)
        self.assertEqual(normalized['start_color']['max'], [1, 1, 1, 1])

    def test_random_fixed_gradient_uses_max_gradient_and_shared_time(self):
        tree = _tree()
        colors = [
            [0.20392155647277832, 0.6053456664085388, 1.0],
            [1.0, 0.7777777910232544, 0.0],
            [1.0, 0.5803921818733215, 0.0],
            [1.0, 0.0, 0.3128490447998047],
            [0.6209022998809814, 0.0, 0.9339622855186462],
        ]
        tree['InitialModule']['startColor'] = {
            'minMaxState': 4,
            'maxGradient': {
                **{f'key{i}': {'r': color[0], 'g': color[1], 'b': color[2], 'a': 1} for i, color in enumerate(colors)},
                'ctime0': 8653, 'ctime1': 22272, 'ctime2': 38296, 'ctime3': 52716, 'ctime4': 65535,
                'atime0': 0, 'atime1': 65535,
                'm_Mode': 1, 'm_NumColorKeys': 5, 'm_NumAlphaKeys': 2,
            },
        }
        start = normalize_system(tree, _renderer(), 7, [_transform()])['start_color']
        self.assertEqual(start['mode'], 'random_fixed_gradient')
        self.assertEqual(start['color_times'], [8653 / 65535, 22272 / 65535, 38296 / 65535, 52716 / 65535, 1])
        self.assertEqual(start['colors'], colors)
        self.assertEqual(start['alpha_times'], [0, 1])
        self.assertEqual(start['alpha'], [1, 1])

    def test_lifetime_gradient_clamps_interior_key_times(self):
        module = _gradient()
        module['gradient']['maxGradient'].update(
            ctime0=25182, ctime1=58851, m_NumColorKeys=2, atime0=3538, atime1=61882,
        )
        normalized = normalize_system(_tree(module), _renderer(), 7, [_transform()])
        self.assertAlmostEqual(normalized['color_over_time']['color_times'][0], 25182 / 65535)
        self.assertAlmostEqual(normalized['color_over_time']['alpha_times'][-1], 61882 / 65535)

    def test_size_rotation_and_velocity_curves(self):
        tree = _tree()
        tree['SizeModule'] = {
            'enabled': True, 'separateAxes': False,
            'curve': _curve([(0, 0.6654663, 0, 0.3345337), (1, 1, 0.3345337, 1)]),
        }
        tree['RotationModule'] = {
            'enabled': True, 'separateAxes': False,
            'curve': _curve([(0, -1, 3.0233769, 3.0233769), (1, -0.072731, 0, 0)], mode=2, scalar=1.7453293,
                            min_points=[(0, 0.80909, -2.047078, -2.047078), (1, 0.0409088, 0, 0)]),
        }
        tree['VelocityModule'] = {
            'enabled': True, 'inWorldSpace': False,
            'x': _curve([(0, 0, 0, 0), (1, -1, 0, 0)], mode=2, scalar=4,
                        min_points=[(0, -0.0029678, 0, 0), (1, -0.543026, 0, 0)]),
            'y': _curve([(0, -0.82492, 0, 0), (1, -0.344208, 0, 0)], mode=2, scalar=3.07,
                        min_points=[(0, -1, 0, 0), (1, -1, 0, 0)]),
            'z': _curve([(0, 0, 0, 0), (1, 0, 0, 0)], mode=2, scalar=1,
                        min_points=[(0, 0, 0, 0), (1, 0, 0, 0)]),
        }
        normalized = normalize_system(tree, _renderer(), 7, [_transform()])
        self.assertEqual(normalized['size_over_time']['mode'], 1)
        self.assertEqual(normalized['rotation_over_time']['mode'], 2)
        self.assertEqual(normalized['rotation_over_time']['scalar'], 1.7453293)
        self.assertEqual(normalized['velocity_curves']['x']['mode'], 2)
        self.assertEqual(normalized['velocity_curves']['x']['scalar'], 4)
        self.assertEqual(len(normalized['velocity_curves']['y']['min']['keys']), 2)

    def test_single_row_random_uv_and_multiple_bursts(self):
        tree = _tree()
        tree['UVModule'] = {
            'enabled': True, 'mode': 0, 'timeMode': 0, 'animationType': 1, 'rowMode': 1,
            'tilesX': 4, 'tilesY': 4, 'cycles': 1, 'flipU': 0, 'flipV': 0,
            'startFrame': {'minMaxState': 0, 'scalar': 0},
            'frameOverTime': {'minMaxState': 3, 'scalar': 0.9999, 'minScalar': 0},
        }
        tree['EmissionModule']['m_Bursts'] = [
            {'time': 0, 'countCurve': {'minMaxState': 0, 'scalar': 8}, 'cycleCount': 1, 'repeatInterval': 0.01, 'probability': 1},
            {'time': 0, 'countCurve': {'minMaxState': 0, 'scalar': 3}, 'cycleCount': 1, 'repeatInterval': 0.01, 'probability': 1},
        ]
        normalized = normalize_system(tree, _renderer(), 7, [_transform()])
        self.assertEqual(normalized['uv_row'], 'random')
        self.assertEqual(normalized['frame_mode'], 'random_constant')
        self.assertEqual(normalized['uv_frame_end'], 0.9999)
        self.assertEqual([burst['count'] for burst in normalized['bursts']], [8, 3])
        data = validate_config(json.dumps({'version': 1, 'system': normalized, 'material': {
            'profile': 'alpha_tint_v1', 'tint': [1, 1, 1, 1], 'dark_color': [1, 1, 1, 1],
            'factor': 1, 'dead_strength': 0, 'main_power': 1, 'alpha': 1, 'alpha_ratio': 1, 'blend_amount': 0,
        }}), 'alpha_tint_v1')
        self.assertEqual([burst['count'] for burst in data['system']['bursts']], [8, 3])

    def test_noise_is_normalized_and_validated(self):
        tree = _tree()
        tree['NoiseModule'] = {
            'enabled': True, 'strength': {'minMaxState': 0, 'scalar': 0.19},
            'frequency': 1.26, 'separateAxes': False, 'damping': True,
            'octaves': 1, 'quality': 2, 'scrollSpeed': {'minMaxState': 0, 'scalar': 0},
            'remapEnabled': False, 'positionAmount': {'minMaxState': 0, 'scalar': 1},
            'rotationAmount': {'minMaxState': 0, 'scalar': 0},
            'sizeAmount': {'minMaxState': 0, 'scalar': 0},
        }
        normalized = normalize_system(tree, _renderer(), 7, [_transform()])
        self.assertEqual(normalized['noise'], {
            'strength': 0.19, 'frequency': 1.26, 'damping': True, 'position_amount': 1.0,
        })
        material = normalize_material(
            _alpha_blend_material(), 'SoulGames/Effects/AlphaBlend', 1905315269886197793,
            shader_tree=_pass_state(),
        )
        validate_config(json.dumps({'version': 1, 'system': normalized, 'material': material}), 'alpha_blend_v1')
        tree['NoiseModule']['frequency'] = 0
        with self.assertRaisesRegex(ValueError, 'frequency'):
            normalize_system(tree, _renderer(), 7, [_transform()])

    def test_orbital_z_curve_and_zero_xy(self):
        tree = _tree()
        zero = _curve([(0, 0), (1, 0)], mode=2, min_points=[(0, 0), (1, 0)])
        tree['VelocityModule'] = {
            'enabled': True, 'inWorldSpace': False,
            'orbitalX': zero, 'orbitalY': zero,
            'orbitalZ': _curve([(0, -0.56307983), (1, -0.16455078)], mode=2, scalar=0.3,
                               min_points=[(0, -0.27789307), (1, -1)]),
        }
        normalized = normalize_system(tree, _renderer(), 7, [_transform()])
        self.assertEqual(normalized['orbital_z']['mode'], 2)
        self.assertEqual(normalized['orbital_z']['scalar'], 0.3)
        self.assertNotIn('orbital_x', normalized)
        material = normalize_material(
            _alpha_blend_material(), 'SoulGames/Effects/AlphaBlend', 1905315269886197793,
            shader_tree=_pass_state(),
        )
        validate_config(json.dumps({'version': 1, 'system': normalized, 'material': material}), 'alpha_blend_v1')

    def test_circle_thickness_is_normalized(self):
        tree = _tree()
        tree['ShapeModule'] = {
            'enabled': True, 'type': 10, 'radiusThickness': 0.6,
            'm_Position': {'x': 0, 'y': 0, 'z': 0}, 'm_Rotation': {'x': 0, 'y': 0, 'z': 0},
            'm_Scale': {'x': 1.48, 'y': 0.8, 'z': 1},
            'radius': {'value': 5, 'mode': 0, 'spread': 0},
            'arc': {'value': 360, 'mode': 0, 'spread': 0}, 'alignToDirection': False,
        }
        normalized = normalize_system(tree, _renderer(), 7, [_transform()])
        self.assertEqual(normalized['circle_thickness'], 0.6)
        with self.assertRaisesRegex(ValueError, 'radiusThickness'):
            tree['ShapeModule']['radiusThickness'] = -0.01
            normalize_system(tree, _renderer(), 7, [_transform()])

    def test_max_particles_zero_is_explicit_noop(self):
        tree = _tree()
        tree['InitialModule']['maxNumParticles'] = 0
        tree['EmissionModule']['enabled'] = False
        normalized = normalize_system(tree, _renderer(), 7, [_transform()])
        self.assertTrue(normalized['noop'])
        validate_config(json.dumps({'version': 1, 'system': normalized}), 'noop')

    def test_alpha_blend_requires_actual_pass_evidence(self):
        material = normalize_material(
            _alpha_blend_material(), 'SoulGames/Effects/AlphaBlend', 1905315269886197793,
            shader_tree=_pass_state(),
        )
        self.assertEqual(material['profile'], 'alpha_blend_v1')
        self.assertEqual(material['factor'], 1)
        with self.assertRaises(ValueError):
            normalize_material(
                _alpha_blend_material(), 'SoulGames/Effects/AlphaBlend', 1905315269886197793,
                shader_tree=_pass_state(src=1, dst=0),
            )


if __name__ == '__main__':
    unittest.main()
