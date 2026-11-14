"""Strict normalization of the evidenced billboard particle capability, version 1."""

from __future__ import annotations

import json
import math
import struct

from .model import SceneDiagnostic


def _number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("non-finite particle constant")
    return value


def _range(curve, field, default=0):
    curve = curve or {"minMaxState": 0, "scalar": default}
    mode = int(curve.get("minMaxState", 0))
    if mode not in (0, 3):
        raise ValueError(f"{field}: unsupported curve mode {mode}")
    hi = _number(curve.get("scalar", default))
    lo = _number(curve.get("minScalar", hi)) if mode == 3 else hi
    return [min(lo, hi), max(lo, hi)]


def _constant(curve, field, default=0):
    lo, hi = _range(curve, field, default)
    if lo != hi:
        raise ValueError(f"{field}: random constant is unsupported")
    return hi


def _vec(value, keys="xyz", default=0):
    return [_number((value or {}).get(k, default)) for k in keys]


def _gradient_keys(tree):
    if not isinstance(tree, dict):
        raise ValueError('gradient is missing')

    def keys(kind):
        count = int(tree.get('m_NumColorKeys' if kind == 'color' else 'm_NumAlphaKeys', 0))
        if not 2 <= count <= 8:
            raise ValueError(f'invalid {kind} key count {count}')
        prefix = 'ctime' if kind == 'color' else 'atime'
        times, values = [], []
        for index in range(count):
            key = tree.get(f'key{index}')
            if not isinstance(key, dict):
                raise ValueError(f'missing {kind} key {index}')
            value = _vec(key, 'rgb', 1) if kind == 'color' else _number(key.get('a', 1))
            if any(component < 0 or component > 1 for component in value) if kind == 'color' else not 0 <= value <= 1:
                raise ValueError(f'{kind} key {index} is outside 0..1')
            times.append(_number(tree.get(f'{prefix}{index}')) / 65535)
            values.append(value)
        if not 0 <= times[0] <= 1 or not 0 <= times[-1] <= 1 or any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError(f'{kind} key times must be strictly ordered within 0..1')
        return times, values

    color_times, colors = keys('color')
    alpha_times, alpha = keys('alpha')
    return color_times, colors, alpha_times, alpha


def _color_over_time(module):
    if not isinstance(module, dict) or not module.get('enabled'):
        return None
    gradient = module.get('gradient') or {}
    mode = int(gradient.get('minMaxState', 0))
    if mode == 0:
        color = _vec(gradient.get('maxColor'), 'rgba', 1)
        if any(value < 0 or value > 1 for value in color):
            raise ValueError('ColorModule: constant color is outside 0..1')
        return {'mode': 'constant', 'color': color}
    if mode != 1:
        raise ValueError(f'ColorModule: unsupported color mode {mode}')
    tree = gradient.get('maxGradient')
    if not isinstance(tree, dict) or int(tree.get('m_Mode', 0)) != 0:
        raise ValueError('ColorModule: fixed-step gradients are unsupported')
    color_times, colors, alpha_times, alpha = _gradient_keys(tree)
    return {
        'mode': 'gradient',
        'color_times': color_times,
        'colors': colors,
        'alpha_times': alpha_times,
        'alpha': alpha,
    }


def _start_color(color):
    mode = int(color.get('minMaxState', 0))
    if mode == 0:
        return None
    if mode == 2:
        minimum = _vec(color.get('minColor'), 'rgba', 1)
        maximum = _vec(color.get('maxColor'), 'rgba', 1)
        if any(value < 0 or value > 1 for value in (*minimum, *maximum)):
            raise ValueError('startColor is outside 0..1')
        return {'mode': 'two_colors', 'min': minimum, 'max': maximum}
    if mode == 4:
        tree = color.get('maxGradient')
        if not isinstance(tree, dict) or int(tree.get('m_Mode', 0)) != 1:
            raise ValueError('unsupported RandomColor gradient mode')
        color_times, colors, alpha_times, alpha = _gradient_keys(tree)
        return {
            'mode': 'random_fixed_gradient',
            'color_times': color_times,
            'colors': colors,
            'alpha_times': alpha_times,
            'alpha': alpha,
        }
    raise ValueError(f'unsupported startColor mode {mode}')


def _slope(value):
    value = float(value)
    if math.isnan(value):
        raise ValueError('curve slope is NaN')
    return value


def _curve(curve):
    if not isinstance(curve, dict):
        raise ValueError('curve is missing')
    mode = int(curve.get('minMaxState', 0))
    if mode not in (0, 1, 2, 3):
        raise ValueError(f'unsupported curve mode {mode}')
    result = {'mode': mode, 'scalar': _number(curve.get('scalar', 1))}
    if mode == 3:
        result['min_scalar'] = _number(curve.get('minScalar', result['scalar']))
        return result
    if mode == 0:
        return result
    for label in ('max', 'min'):
        if mode == 1 and label == 'min':
            continue
        tree = curve.get(f'{label}Curve', {})
        keys = tree.get('m_Curve', ())
        if len(keys) < 2:
            raise ValueError(f'{label}Curve requires at least two keys')
        normalized = []
        previous = None
        for key in keys:
            time = _number(key.get('time'))
            if time < 0 or time > 1 or (previous is not None and time <= previous):
                raise ValueError('curve key times must be strictly ordered within 0..1')
            if int(key.get('weightedMode', 0)) != 0:
                raise ValueError('weighted curve keys are unsupported')
            normalized.append({
                'time': time,
                'value': _number(key.get('value')),
                'inSlope': _slope(key.get('inSlope', 0)),
                'outSlope': _slope(key.get('outSlope', 0)),
                'weightedMode': 0,
            })
            previous = time
        if normalized[0]['time'] != 0 or normalized[-1]['time'] != 1:
            raise ValueError('curve must cover exactly 0..1')
        result[label] = {'keys': normalized}
    return result


def _curve_is_zero(curve):
    if curve is None:
        return True
    normalized = _curve(curve)
    if normalized['mode'] == 0:
        return normalized['scalar'] == 0
    if normalized['mode'] == 3:
        return normalized['min_scalar'] == normalized['scalar'] == 0
    labels = ('max',) if normalized['mode'] == 1 else ('min', 'max')
    return all(key['value'] == 0 for label in labels for key in normalized[label]['keys'])


def _validate_curve(value):
    if value is None:
        return
    if not isinstance(value, dict) or set(value) - {'mode', 'scalar', 'min_scalar', 'max', 'min'}:
        raise ValueError('invalid curve config')
    mode = value.get('mode')
    if mode not in (0, 1, 2, 3):
        raise ValueError('invalid curve mode')
    _number(value.get('scalar'))
    if mode == 3:
        _number(value.get('min_scalar'))
    if mode in (1, 2):
        for label in ('max', 'min'):
            if mode == 1 and label == 'min':
                continue
            keys = value.get(label, {}).get('keys')
            if not isinstance(keys, list) or len(keys) < 2:
                raise ValueError('curve requires at least two keys')
            previous = None
            for key in keys:
                if set(key) != {'time', 'value', 'inSlope', 'outSlope', 'weightedMode'}:
                    raise ValueError('invalid curve key fields')
                time = _number(key['time'])
                if time < 0 or time > 1 or (previous is not None and time <= previous):
                    raise ValueError('curve key times must be strictly ordered within 0..1')
                _number(key['value'])
                _slope(key['inSlope'])
                _slope(key['outSlope'])
                if key['weightedMode'] != 0:
                    raise ValueError('weighted curve keys are unsupported')
                previous = time
            if keys[0]['time'] != 0 or keys[-1]['time'] != 1:
                raise ValueError('curve must cover exactly 0..1')


def _validate_start_color(value):
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError('invalid start_color')
    mode = value.get('mode')
    if mode == 'two_colors':
        if set(value) != {'mode', 'min', 'max'}:
            raise ValueError('invalid two_colors fields')
        for name in ('min', 'max'):
            color = value[name]
            if not isinstance(color, list) or len(color) != 4 or any(not 0 <= _number(component) <= 1 for component in color):
                raise ValueError('invalid two_colors color')
        return
    if mode != 'random_fixed_gradient' or set(value) != {'mode', 'color_times', 'colors', 'alpha_times', 'alpha'}:
        raise ValueError('invalid random_fixed_gradient fields')
    for kind in ('color', 'alpha'):
        times = value[f'{kind}_times']
        items = value[f'{kind}s' if kind == 'color' else 'alpha']
        if not isinstance(times, list) or not isinstance(items, list) or len(times) != len(items) or not 2 <= len(times) <= 8:
            raise ValueError('invalid random gradient length')
        if not 0 <= times[0] <= 1 or not 0 <= times[-1] <= 1 or any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError('invalid random gradient times')
        for item in items:
            components = item if kind == 'color' else [item]
            if kind == 'color' and (not isinstance(item, list) or len(item) != 3):
                raise ValueError('invalid random gradient key')
            if any(not 0 <= _number(component) <= 1 for component in components):
                raise ValueError('invalid random gradient value')


def _validate_color_over_time(value):
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError('invalid color_over_time')
    mode = value.get('mode')
    if mode == 'constant':
        if set(value) != {'mode', 'color'}:
            raise ValueError('invalid constant color_over_time fields')
        color = value['color']
        if not isinstance(color, list) or len(color) != 4:
            raise ValueError('invalid constant color_over_time')
        if any(not 0 <= _number(component) <= 1 for component in color):
            raise ValueError('constant color_over_time is outside 0..1')
        return
    if mode != 'gradient' or set(value) != {'mode', 'color_times', 'colors', 'alpha_times', 'alpha'}:
        raise ValueError('invalid gradient color_over_time fields')
    for kind in ('color', 'alpha'):
        times = value[f'{kind}_times']
        items = value[f'{kind}s' if kind == 'color' else 'alpha']
        if not isinstance(times, list) or not isinstance(items, list) or len(times) != len(items) or not 2 <= len(times) <= 8:
            raise ValueError(f'invalid {kind} gradient length')
        if not 0 <= times[0] <= 1 or not 0 <= times[-1] <= 1 or any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError(f'invalid {kind} gradient times')
        for item in items:
            components = item if kind == 'color' else [item]
            if kind == 'color' and (not isinstance(item, list) or len(item) != 3):
                raise ValueError(f'invalid {kind} gradient key')
            if any(not 0 <= _number(component) <= 1 for component in components):
                raise ValueError(f'invalid {kind} gradient value')


def _noise(module):
    if not isinstance(module, dict) or not module.get('enabled'):
        return None
    if (
        module.get('separateAxes')
        or module.get('damping') is not True
        or int(module.get('octaves', 1)) != 1
        or int(module.get('quality', 2)) != 2
        or module.get('remapEnabled')
    ):
        raise ValueError('NoiseModule: unsupported axes/octaves/quality/remap')
    if _constant(module.get('scrollSpeed'), 'Noise.scrollSpeed') != 0:
        raise ValueError('unsupported Noise scrollSpeed')
    for name in ('rotationAmount', 'sizeAmount'):
        if _constant(module.get(name), f'Noise.{name}') != 0:
            raise ValueError(f'unsupported Noise {name}')
    frequency = _number(module.get('frequency'))
    strength = _constant(module.get('strength'), 'Noise.strength')
    position_amount = _constant(module.get('positionAmount'), 'Noise.positionAmount', 1)
    if frequency <= 0 or strength < 0 or position_amount <= 0:
        raise ValueError('invalid Noise frequency/strength/positionAmount')
    return {
        'strength': strength, 'frequency': frequency,
        'damping': True, 'position_amount': position_amount,
    }


def _validate_noise_config(value, lifetime, max_particles):
    if not isinstance(value, dict) or set(value) != {'strength', 'frequency', 'damping', 'position_amount'}:
        raise ValueError('invalid Noise config')
    strength = _number(value['strength'])
    frequency = _number(value['frequency'])
    position_amount = _number(value['position_amount'])
    if value['damping'] is not True or strength < 0 or frequency <= 0 or position_amount <= 0:
        raise ValueError('invalid Noise strength/frequency/positionAmount')
    steps = math.ceil(lifetime * 120)
    if steps > 100000 or steps * max_particles > 10000000:
        raise ValueError('Noise simulation exceeds bounded static-frame work')


def normalize_system(tree, renderer, seed, transform_chain=()):
    initial = tree.get('InitialModule', {})
    emission = tree.get('EmissionModule', {})
    if initial.get('maxNumParticles') == 0 and not emission.get('enabled', True):
        return {'noop': True}
    supported = {'InitialModule', 'ShapeModule', 'EmissionModule', 'VelocityModule', 'UVModule', 'ColorModule', 'SizeModule', 'RotationModule', 'NoiseModule'}
    for name, module in tree.items():
        if isinstance(module, dict) and module.get('enabled') and name not in supported:
            raise ValueError(f'unsupported active module {name}')
    alignment = int(renderer.get('m_RenderAlignment', 0))
    if alignment not in (0, 2):
        raise ValueError('unsupported renderer m_RenderAlignment')
    render_mode = int(renderer.get('m_RenderMode', 0))
    if render_mode not in (0, 4):
        raise ValueError('unsupported renderer m_RenderMode')
    if int(renderer.get('m_MaskInteraction', 0)) != 0:
        raise ValueError('unsupported renderer m_MaskInteraction')
    if renderer.get('m_UseCustomVertexStreams'):
        raise ValueError('unsupported custom vertex streams')
    for field in ('ringBufferMode', 'moveWithTransform'):
        if int(tree.get(field, 0)) != 0:
            raise ValueError(f'unsupported {field}')
    scaling = int(tree.get('scalingMode', 0))
    if scaling not in (0, 1):
        raise ValueError('unsupported scalingMode')
    if scaling == 1:
        if not transform_chain:
            raise ValueError('scalingMode requires a transform chain')
        if any(tuple(node.local_scale) != (1, 1, 1) for node in transform_chain):
            raise ValueError('unsupported scalingMode with nonidentity scales')
    if _constant(tree.get("startDelay"), "startDelay") != 0:
        raise ValueError("unsupported startDelay")
    initial = tree.get("InitialModule", {})
    if initial.get("rotation3D"):
        raise ValueError("unsupported initial rotation3D")
    size_y = None
    if initial.get("size3D"):
        if not isinstance(initial.get("startSizeY"), dict) or not isinstance(initial.get("startSizeZ"), dict):
            raise ValueError("size3D requires explicit startSizeY/startSizeZ")
        size_y = _range(initial["startSizeY"], "startSizeY", 1)
        if _constant(initial["startSizeZ"], "startSizeZ", 1) != 1:
            raise ValueError("unsupported billboard startSizeZ")
    if _constant(initial.get("startSpeed"), "startSpeed") != 0:
        raise ValueError("unsupported nonzero startSpeed")
    if _number(initial.get("randomizeRotationDirection", 0)) != 0:
        raise ValueError("unsupported randomizeRotationDirection")
    color = initial.get("startColor", {})
    start_color = _start_color(color)
    emission = tree.get("EmissionModule", {})
    if _constant(emission.get("rateOverDistance"), "rateOverDistance") != 0:
        raise ValueError("unsupported distance emission")
    bursts = []
    for burst in emission.get("m_Bursts", ()) if emission.get("enabled", True) else ():
        count = _constant(burst.get("countCurve"), "burst.count")
        item = dict(
            time=_number(burst.get("time", 0)),
            count=count,
            cycles=int(burst.get("cycleCount", 1)),
            interval=_number(burst.get("repeatInterval", 0)),
            probability=_number(burst.get("probability", 1)),
        )
        if item["time"] != 0 or not 0 <= count <= 100000 or count != int(count) or item["cycles"] != 1 or item["probability"] != 1:
            raise ValueError("unsupported burst configuration")
        bursts.append(item)
    shape = tree.get("ShapeModule", {})
    shape_type = int(shape.get("type", -1)) if shape.get("enabled") else -1
    if shape_type not in {-1, 5, 10}:
        raise ValueError("unsupported shape (requires box or evidenced circle)")
    circle_radius = circle_arc = circle_thickness = None
    if shape_type == 10:
        radius, arc = shape.get("radius"), shape.get("arc")
        if not isinstance(radius, dict) or not isinstance(arc, dict):
            raise ValueError("Circle radius/arc is missing")
        circle_thickness = _number(shape.get("radiusThickness", 1))
        if not 0 <= circle_thickness <= 1:
            raise ValueError("Circle radiusThickness must be within 0..1")
        if shape.get("alignToDirection") or _vec(shape.get("m_Scale"), "xyz", 1)[2] != 1:
            raise ValueError("unsupported Circle direction/Z scale")
        if any(int(item.get("mode", -1)) != 0 or _number(item.get("spread", 0)) != 0 for item in (radius, arc)):
            raise ValueError("unsupported Circle radius/arc mode")
        circle_radius = _number(radius.get("value"))
        circle_arc = math.radians(_number(arc.get("value")))
        if circle_radius <= 0 or not 0 < circle_arc <= math.tau:
            raise ValueError("invalid Circle radius/arc")
    if any(_vec(shape.get("m_Rotation"))) or any(
        _number(shape.get(k, 0)) != 0
        for k in ("randomDirectionAmount", "sphericalDirectionAmount", "randomPositionAmount")
    ):
        raise ValueError("unsupported shape rotation/randomization")
    size_over_time = None
    size_module = tree.get('SizeModule', {})
    if size_module.get('enabled'):
        if size_module.get('separateAxes'):
            raise ValueError('separate SizeModule axes are unsupported')
        size_over_time = _curve(size_module.get('curve'))
        if size_over_time['mode'] not in {1, 2}:
            raise ValueError('unsupported SizeModule curve mode')
    rotation_over_time = None
    rotation_module = tree.get('RotationModule', {})
    if rotation_module.get('enabled'):
        if rotation_module.get('separateAxes'):
            raise ValueError('separate RotationModule axes are unsupported')
        rotation_over_time = _curve(rotation_module.get('curve'))
        if rotation_over_time['mode'] not in {1, 2, 3}:
            raise ValueError('unsupported RotationModule curve mode')
    velocity = tree.get("VelocityModule", {}) if tree.get("VelocityModule", {}).get("enabled") else {}
    if velocity.get("inWorldSpace"):
        raise ValueError("unsupported world-space velocity")
    for key in ("orbitalOffsetX", "orbitalOffsetY", "orbitalOffsetZ", "radial"):
        if _constant(velocity.get(key), key) != 0:
            raise ValueError(f"unsupported velocity {key}")
    for key in ("orbitalX", "orbitalY"):
        if not _curve_is_zero(velocity.get(key)):
            raise ValueError(f"unsupported nonzero velocity {key}")
    orbital_z = None
    if not _curve_is_zero(velocity.get("orbitalZ")):
        orbital_z = _curve(velocity.get("orbitalZ"))
    if _constant(velocity.get("speedModifier"), "speedModifier", 1) != 1:
        raise ValueError("unsupported velocity speedModifier")
    ranges = [
        [0, 0] if int((velocity.get(key) or {}).get("minMaxState", 0)) in {1, 2}
        else _range(velocity.get(key), key)
        for key in "xyz"
    ]
    velocity_curves = {
        key: _curve(velocity.get(key))
        for key in "xyz"
        if int((velocity.get(key) or {}).get("minMaxState", 0)) in {1, 2}
    }
    uv = tree.get("UVModule", {})
    tiles, frame_end, cycles = [1, 1], 0, 1
    uv_row, frame_mode, frame_range = 'custom', 'curve', None
    if uv.get("enabled"):
        if (
            any(int(uv.get(k, 0)) != 0 for k in ("mode", "timeMode"))
            or int(uv.get("animationType", 0)) not in {0, 1}
            or int(uv.get("rowMode", 0)) not in {0, 1}
            or uv.get("flipU")
            or uv.get("flipV")
        ):
            raise ValueError("unsupported UV mode")
        if _constant(uv.get("startFrame"), "startFrame") != 0:
            raise ValueError("unsupported UV startFrame")
        curve = uv.get("frameOverTime", {})
        curve_mode = int(curve.get("minMaxState", -1))
        if curve_mode == 1:
            keys = curve.get("maxCurve", {}).get("m_Curve", ())
            if len(keys) != 2:
                raise ValueError("unsupported UV curve")
            if (
                [(k.get("time"), k.get("value")) for k in keys] != [(0, 0), (1, 1)]
                or keys[0].get("outSlope") != 1
                or keys[1].get("inSlope") != 1
                or any(k.get("weightedMode", 0) != 0 for k in keys)
            ):
                raise ValueError("unsupported UV curve interpolation")
            frame_end = _number(curve.get("scalar", 1))
        elif curve_mode == 3:
            frame_mode = 'random_constant'
            frame_range = _range(curve, 'frameOverTime', 1)
            frame_end = _number(curve.get("scalar", 1))
        else:
            raise ValueError("unsupported UV frameOverTime mode")
        tiles = [int(uv.get("tilesX", 1)), int(uv.get("tilesY", 1))]
        uv_row = 'random' if int(uv.get("rowMode", 0)) == 1 else 'custom'
        cycles = _number(uv.get("cycles", 1))
        if min(tiles) < 1 or max(tiles) > 64 or not 0 <= frame_end <= 1 or cycles <= 0:
            raise ValueError("invalid UV tile/range")
        if uv_row == 'custom' and not 0 <= int(uv.get("rowIndex", 0)) < tiles[1]:
            raise ValueError("invalid UV rowIndex")
    gravity = _constant(initial.get("gravityModifier"), "gravityModifier")
    result = dict(
        duration=_number(tree.get("lengthInSec", 5)),
        loop=bool(tree.get("looping", False)),
        prewarm=bool(tree.get("prewarm", False)),
        simulation_speed=_number(tree.get("simulationSpeed", 1)),
        rate=_constant(emission.get("rateOverTime"), "rateOverTime") if emission.get("enabled", True) else 0,
        max_particles=int(initial.get("maxNumParticles", 100)),
        lifetime=_range(initial.get("startLifetime"), "startLifetime", 1),
        size=_range(initial.get("startSize"), "startSize", 1),
        rotation_radians=_range(initial.get("startRotation"), "startRotation"),
        color=_vec(color.get("maxColor"), "rgba", 1),
        shape_size=_vec(shape.get("m_Scale"), "xyz", 1) if shape.get("enabled") else [0, 0, 0],
        shape_position=_vec(shape.get("m_Position")),
        velocity_min=[v[0] for v in ranges],
        velocity_max=[v[1] for v in ranges],
        tiles=tiles,
        uv_frame_end=frame_end,
        cycles=cycles,
        seed=int(seed) & 0xFFFFFFFF,
        gravity_modifier=gravity,
        gravity=[0, -9.81, 0],
        gravity_source="unity_default_assumption" if gravity else "unused",
        bursts=bursts,
        color_over_time=_color_over_time(tree.get("ColorModule")),
        render_alignment=alignment,
        scaling_mode=scaling,
        render_mode=render_mode,
    )
    if size_y is not None:
        result["size_y"] = size_y
    if start_color is not None:
        result["start_color"] = start_color
    if size_over_time is not None:
        result["size_over_time"] = size_over_time
    if rotation_over_time is not None:
        result["rotation_over_time"] = rotation_over_time
    if velocity_curves:
        result["velocity_curves"] = velocity_curves
    if orbital_z is not None:
        result['orbital_z'] = orbital_z
    result["uv_row"] = uv_row
    result["row_index"] = int(uv.get("rowIndex", 0)) if uv.get("enabled") else 0
    result["frame_mode"] = frame_mode
    if frame_range is not None:
        result["frame_range"] = frame_range
    if circle_radius is not None:
        result["circle_radius"] = circle_radius
        result["circle_arc_radians"] = circle_arc
        result["circle_thickness"] = circle_thickness
    if (
        result["duration"] <= 0
        or result["simulation_speed"] <= 0
        or result["rate"] < 0
        or not 0 < result["max_particles"] <= 100000
        or result["lifetime"][0] <= 0
        or result["size"][0] < 0
        or (size_y is not None and size_y[0] < 0)
    ):
        raise ValueError("invalid particle bounds/lifetime/rate")
    noise = _noise(tree.get('NoiseModule'))
    if noise is not None:
        _validate_noise_config(noise, result['lifetime'][1], result['max_particles'])
        result['noise'] = noise
    return result


def normalize_material(tree, shader_name, shader_path_id, shader_tree=None):
    """Select only names AND exact observed shader identities; never material names."""
    props = tree.get("m_SavedProperties", {})
    floats = dict(props.get("m_Floats", ()))
    colors = dict(props.get("m_Colors", ()))
    keywords = set(tree.get("m_ValidKeywords", ()))
    profile = "unsupported"
    if shader_name == "SoulGames/Effects/Additive" and shader_path_id == -2521961875066890481 and not keywords:
        profile = "additive_v1"
    if (
        shader_name == "SoulGames/Effects/AlphaBlend"
        and shader_path_id == 1905315269886197793
        and not keywords
        and floats.get("_SrcBlend") == 1
        and floats.get("_DstBlend") == 0
        and floats.get("_AlphaRatio") == 1
        and floats.get("_Cutoff") == 0.5
    ):
        passes = [
            pass_tree
            for subshader in (shader_tree or {}).get("m_ParsedForm", {}).get("m_SubShaders", ())
            for pass_tree in subshader.get("m_Passes", ())
        ]
        if len(passes) != 1:
            raise ValueError("AlphaBlend requires exactly one evidenced Shader pass")
        state = passes[0].get("m_State", {})
        blend = state.get("rtBlend0", {})
        if (
            all(blend.get(key, {}).get("val") == expected for key, expected in (
                ("srcBlend", 5), ("destBlend", 10), ("srcBlendAlpha", 5), ("destBlendAlpha", 10),
                ("blendOp", 0), ("blendOpAlpha", 0), ("colMask", 15),
            ))
            and state.get("zWrite", {}).get("val") == 0
            and state.get("lighting") is False
        ):
            profile = "alpha_blend_v1"
    expected = {
        "_ADD2BLENDMODE_ADD",
        "_ADD2P_OFF",
        "_ADD_CUSTOM_ON",
        "_DISSBLENDMODE_ADD",
        "_DISSP_OFF",
        "_DISS_CUSTOM_ON",
        "_MAINP_OFF",
        "_MASKP_OFF",
        "_NOISP_OFF",
    }
    if (
        shader_name == "Hym/Particles/hym_Dissolution_Nois_04"
        and shader_path_id == -4015514059479912215
        and keywords == expected
    ):
        if (
            floats.get("_SrcBlend") == 5
            and floats.get("_DstBlend") in {1, 10}
            and floats.get("_MainPow_Min") == 0
            and floats.get("_MainPow_Max") == 1
            and all(
                floats.get(k, 0) == 0
                for k in (
                    "_ScrollX",
                    "_ScrollY",
                    "_MainRotate",
                    "_SoftParticlesEnabled",
                    "_CameraFadingEnabled",
                    "_DistortionEnabled",
                )
            )
        ):
            profile = "additive_alpha_v1" if floats["_DstBlend"] == 1 else "alpha_tint_v1"
    if profile == "unsupported":
        raise ValueError(f"unsupported particle shader/variant: {shader_name}")
    main = dict(props.get("m_TexEnvs", ())).get("_MainTex")
    if not main or main.get("m_Scale", {"x": 1, "y": 1}) != {"x": 1, "y": 1} or any(_vec(main.get("m_Offset"), "xy")):
        raise ValueError("unsupported or missing particle MainTex transform")
    return dict(
        profile=profile,
        tint=_vec(colors.get("_TintColor"), "rgba", 1),
        dark_color=_vec(colors.get("_DarkColorExt"), "rgba", 1),
        factor=2 if profile == "additive_v1" else 1,
        dead_strength=_number(floats.get("_DeadStrength", 0)) if profile == "additive_v1" else 0,
        main_power=_number(floats.get("_MainPow", 1)),
        alpha=_number(floats.get("_Alpha", 1)),
        alpha_ratio=_number(floats.get("_AlphaRatio", 1)),
        blend_amount=_number(floats.get("_BlendAmount", 0)),
    )


def normalize_mesh(mesh, mesh_path_id):
    if mesh_path_id != 7625927331971706890 or mesh.m_IndexFormat != 0 or not mesh.m_IsReadable:
        raise ValueError("unsupported particle Mesh identity/format")
    if len(mesh.m_SubMeshes) != 1:
        raise ValueError("particle Mesh requires exactly one submesh")
    submesh = mesh.m_SubMeshes[0]
    if (
        getattr(submesh, "firstByte", None) != 0
        or getattr(submesh, "indexCount", None) != 6
        or getattr(submesh, "topology", None) != 0
        or getattr(submesh, "baseVertex", None) != 0
        or getattr(submesh, "firstVertex", None) != 0
        or getattr(submesh, "vertexCount", None) != 4
    ):
        raise ValueError("unsupported particle Mesh submesh")
    data = mesh.m_VertexData
    if data.m_VertexCount != 4 or len(data.m_DataSize) != 4 * 48:
        raise ValueError("invalid particle Mesh vertex data")
    channels = data.m_Channels
    expected = ((3, 0), (3, 12), (4, 24), (0, 0), (2, 40))
    if len(channels) < 5 or any(
        (channel.dimension, channel.offset, channel.format, channel.stream) != (*expected[index], 0, 0)
        for index, channel in enumerate(channels[:5])
    ):
        raise ValueError("unsupported particle Mesh channels")
    positions, uvs = [], []
    for index in range(4):
        values = struct.unpack_from("<3f3f4f2f", data.m_DataSize, index * 48)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("non-finite particle Mesh data")
        positions.append(list(values[:3]))
        uvs.append(list(values[10:]))
    raw_indices = bytes(mesh.m_IndexBuffer)
    if len(raw_indices) != 12:
        raise ValueError("invalid particle Mesh index buffer")
    indices = list(struct.unpack("<6H", raw_indices))
    if any(not 0 <= index < 4 for index in indices):
        raise ValueError("invalid particle Mesh indices")
    return {"kind": "mesh_quad_v1", "positions": positions, "uvs": uvs, "indices": indices}


def enrich_particle(part, index):
    """Read source metadata with exact PPtrs; images stay compressed until extraction."""
    state = part.particle
    if state is None:
        return part, ()
    from dataclasses import replace

    diagnostics = []
    renderer = part.renderer
    material = renderer.material if renderer else None
    texture = None
    config = json.loads(state.config_json)
    try:
        if "system" not in config:
            raise ValueError("particle system capability could not be normalized")
        if config["system"].get("noop"):
            state = replace(
                state, profile="noop",
                config_json=json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False),
            )
            return replace(part, particle=state), ()
        if material is None:
            raise ValueError("particle material PPtr is missing")
        material_tree = index.object_tree(material)
        shader = index.resolve_object_pointer(material, material_tree.get("m_Shader"), expected_type="Shader")
        if shader is None:
            raise ValueError("particle shader identity unavailable (CAB/object not verified)")
        shader_tree = index.object_tree(shader)
        shader_name = str(shader_tree.get("m_ParsedForm", {}).get("m_Name", shader_tree.get("m_Name", "")))
        material_config = normalize_material(material_tree, shader_name, shader.path_id, shader_tree=shader_tree)
        if config["system"].get("render_mode") == 4:
            renderer_tree = index.object_tree(part.object_ref)
            mesh = index.resolve_object_pointer(part.object_ref, renderer_tree.get("m_Mesh"), expected_type="Mesh")
            if mesh is None:
                raise ValueError("particle Mesh exact PPtr unavailable")
            with index._environment(index.bundle_path_for(mesh.bundle_hash)) as environment:
                mesh_object = next(obj for obj in environment.objects if obj.path_id == mesh.path_id).read()
            config["mesh"] = normalize_mesh(mesh_object, mesh.path_id)
            renderer = replace(renderer, mesh=mesh)
        main = dict(material_tree.get("m_SavedProperties", {}).get("m_TexEnvs", ())).get("_MainTex", {})
        texture = index.resolve_object_pointer(material, main.get("m_Texture"), expected_type="Texture2D")
        if texture is None:
            raise ValueError("particle MainTex exact PPtr unavailable")
        config["material"] = material_config
        state = replace(
            state,
            material=material,
            texture=texture,
            profile=material_config["profile"],
            config_json=json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False),
        )
        if config["system"].get("gravity_source") == "unity_default_assumption":
            diagnostics.append(
                SceneDiagnostic(
                    "particle_environment_assumed",
                    "Gravity uses Unity default [0,-9.81,0]; game global physics settings were not present in the Prefab",
                    "warning",
                    part_id=part.part_id,
                    source=state.system,
                )
            )
    except (ValueError, KeyError, TypeError, OSError) as exc:
        diagnostics.append(
            SceneDiagnostic("unsupported_particle", str(exc), part_id=part.part_id, source=part.object_ref)
        )
        state = replace(state, material=material, texture=texture, profile="unsupported")
    return replace(part, particle=state, renderer=renderer), tuple(diagnostics)


def _validate_mesh_config(value):
    if not isinstance(value, dict) or set(value) != {"kind", "positions", "uvs", "indices"} or value["kind"] != "mesh_quad_v1":
        raise ValueError("invalid particle Mesh config")
    for name in ("positions", "uvs"):
        rows = value[name]
        if not isinstance(rows, list) or len(rows) != 4:
            raise ValueError("invalid particle Mesh vertices")
        for row in rows:
            if not isinstance(row, list) or len(row) != (3 if name == "positions" else 2):
                raise ValueError("invalid particle Mesh vertex dimensions")
            for component in row:
                _number(component)
    indices = value["indices"]
    if not isinstance(indices, list) or len(indices) != 6:
        raise ValueError("invalid particle Mesh indices")
    if any(type(index) is not int or not 0 <= index < 4 for index in indices):
        raise ValueError("invalid particle Mesh indices")
    if (
        value["positions"] != [[1, -1, 0], [1, 1, 0], [-1, 1, 0], [-1, -1, 0]]
        or value["uvs"] != [[0, 0], [0, 1], [1, 1], [1, 0]]
        or indices != [0, 1, 2, 0, 2, 3]
    ):
        raise ValueError("particle Mesh differs from the evidenced low-plane geometry")


def validate_config(config_json, profile):
    """Validate imported/relocated engineering data independently of parser trust."""
    data = json.loads(config_json)
    if profile == "noop":
        if data.get("version") != 1 or set(data) != {"version", "system"} or data["system"].get("noop") is not True:
            raise ValueError("invalid no-op particle config")
        return data
    if data.get("version") != 1 or profile not in {"additive_v1", "additive_alpha_v1", "alpha_tint_v1", "alpha_blend_v1"}:
        raise ValueError("unsupported particle config version/profile")
    system, material = data["system"], data["material"]
    if material["profile"] != profile:
        raise ValueError("particle material profile disagrees with state")
    system_fields = {
        "duration",
        "loop",
        "prewarm",
        "simulation_speed",
        "rate",
        "max_particles",
        "lifetime",
        "size",
        "rotation_radians",
        "color",
        "shape_size",
        "shape_position",
        "velocity_min",
        "velocity_max",
        "tiles",
        "uv_frame_end",
        "cycles",
        "seed",
    }
    material_fields = {
        "profile",
        "tint",
        "dark_color",
        "factor",
        "dead_strength",
        "main_power",
        "alpha",
        "alpha_ratio",
        "blend_amount",
    }
    if not system_fields <= system.keys() or not material_fields <= material.keys():
        raise ValueError("particle config fields are incomplete")
    if (
        set(system) - system_fields - {
            "gravity_modifier", "gravity", "gravity_source", "bursts",
            "color_over_time", "render_alignment", "scaling_mode", "size_y",
            "circle_radius", "circle_arc_radians", "render_mode",
            "start_color", "size_over_time", "rotation_over_time", "velocity_curves",
            "uv_row", "frame_mode", "frame_range", "row_index", "circle_thickness", "noise", "orbital_z",
        }
        or set(material) - material_fields
        or set(data) - {"version", "system", "material", "mesh"}
    ):
        raise ValueError("unknown particle config fields")
    render_mode = system.get("render_mode", 0)
    if type(render_mode) is not int or render_mode not in {0, 4}:
        raise ValueError("invalid particle render mode")
    if ("mesh" in data) != (render_mode == 4):
        raise ValueError("particle Mesh must be present exactly for Mesh render mode")
    if "mesh" in data:
        _validate_mesh_config(data["mesh"])

    def finite(value):
        if isinstance(value, dict):
            for nested in value.values():
                finite(nested)
        elif isinstance(value, list):
            for nested in value:
                finite(nested)
        elif isinstance(value, (float, int)):
            _number(value)

    finite(data)
    _validate_color_over_time(system.get("color_over_time"))
    _validate_start_color(system.get("start_color"))
    _validate_curve(system.get("size_over_time"))
    _validate_curve(system.get("rotation_over_time"))
    _validate_curve(system.get('orbital_z'))
    for curve in system.get("velocity_curves", {}).values():
        _validate_curve(curve)
    if system.get("uv_row", "custom") not in {"custom", "random"}:
        raise ValueError("invalid UV row mode")
    if system.get("frame_mode", "curve") not in {"curve", "random_constant"}:
        raise ValueError("invalid UV frame mode")
    if "row_index" in system and (type(system["row_index"]) is not int or not 0 <= system["row_index"] < system["tiles"][1]):
        raise ValueError("invalid UV row index")
    if "frame_range" in system:
        frame_range = system["frame_range"]
        if not isinstance(frame_range, list) or len(frame_range) != 2:
            raise ValueError("invalid UV frame range")
        if any(not 0 <= _number(value) <= 1 for value in frame_range) or frame_range[1] < frame_range[0]:
            raise ValueError("invalid UV frame range")
    for name, allowed in (("render_alignment", {0, 2}), ("scaling_mode", {0, 1})):
        if name in system and (type(system[name]) is not int or system[name] not in allowed):
            raise ValueError(f"invalid particle {name}")
    circle_radius = _number(system.get("circle_radius", 0))
    circle_arc = _number(system.get("circle_arc_radians", 0))
    if not ((circle_radius == 0 and circle_arc == 0) or (circle_radius > 0 and 0 < circle_arc <= math.tau)):
        raise ValueError("invalid particle Circle radius/arc")
    circle_thickness = _number(system.get('circle_thickness', 0))
    if not 0 <= circle_thickness <= 1 or circle_radius == 0 and circle_thickness != 0:
        raise ValueError('invalid Circle radiusThickness')
    if 'noise' in system:
        _validate_noise_config(system['noise'], system['lifetime'][1], system['max_particles'])
    for name, length in [
        ("lifetime", 2),
        ("size", 2),
        ("rotation_radians", 2),
        ("color", 4),
        ("shape_size", 3),
        ("shape_position", 3),
        ("velocity_min", 3),
        ("velocity_max", 3),
        ("tiles", 2),
    ]:
        if not isinstance(system[name], list) or len(system[name]) != length:
            raise ValueError(f"invalid particle {name}")
        for value in system[name]:
            _number(value)
    if "size_y" in system:
        size_y = system["size_y"]
        if not isinstance(size_y, list) or len(size_y) != 2:
            raise ValueError("invalid particle size_y")
        for value in size_y:
            _number(value)
        if size_y[0] < 0 or size_y[1] < size_y[0]:
            raise ValueError("invalid particle size_y bounds")
    for name in ("tint", "dark_color"):
        if not isinstance(material[name], list) or len(material[name]) != 4:
            raise ValueError(f"invalid particle material {name}")
        for value in material[name]:
            _number(value)
    if (
        system["duration"] <= 0
        or system["simulation_speed"] <= 0
        or system["rate"] < 0
        or not 0 < system["max_particles"] <= 100000
        or system["lifetime"][0] <= 0
        or system["lifetime"][1] < system["lifetime"][0]
        or system["size"][0] < 0
        or system["size"][1] < system["size"][0]
        or any(not isinstance(n, int) or n < 1 or n > 64 for n in system["tiles"])
        or not 0 <= system["uv_frame_end"] <= 1
        or system["cycles"] <= 0
    ):
        raise ValueError("invalid particle bounds")
    for name in ("duration", "simulation_speed", "rate", "max_particles", "uv_frame_end", "cycles", "seed"):
        _number(system[name])
    for name in ("factor", "dead_strength", "main_power", "alpha", "alpha_ratio", "blend_amount"):
        _number(material[name])
    if material["factor"] != (2 if profile == "additive_v1" else 1):
        raise ValueError("invalid material factor for profile")
    if any(name in system for name in ("gravity_modifier", "gravity", "gravity_source")):
        _number(system["gravity_modifier"])
        gravity = system["gravity"]
        if not isinstance(gravity, list) or len(gravity) != 3:
            raise ValueError("invalid gravity vector")
        for value in gravity:
            _number(value)
        if system["gravity_source"] not in {"unused", "unity_default_assumption"}:
            raise ValueError("unsupported gravity evidence source")
    for burst in system.get("bursts", ()):
        if set(burst) != {"time", "count", "cycles", "interval", "probability"}:
            raise ValueError("invalid burst fields")
        for value in burst.values():
            _number(value)
        if (
            burst["time"] != 0
            or not 0 <= burst["count"] <= 100000
            or burst["count"] != int(burst["count"])
            or burst["cycles"] != 1
            or burst["probability"] != 1
            or burst["interval"] < 0
        ):
            raise ValueError("unsupported burst configuration")
    return data
