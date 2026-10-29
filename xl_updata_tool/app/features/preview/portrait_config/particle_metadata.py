"""Strict normalization of the evidenced billboard particle capability, version 1."""

from __future__ import annotations

import json
import math

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


def normalize_system(tree, renderer, seed):
    supported = {"InitialModule", "ShapeModule", "EmissionModule", "VelocityModule", "UVModule"}
    for name, module in tree.items():
        if isinstance(module, dict) and module.get("enabled") and name not in supported:
            raise ValueError(f"unsupported active module {name}")
    for key, default in [("m_RenderMode", 0), ("m_RenderAlignment", 0), ("m_MaskInteraction", 0)]:
        if int(renderer.get(key, default)) != default:
            raise ValueError(f"unsupported renderer {key}")
    if renderer.get("m_UseCustomVertexStreams"):
        raise ValueError("unsupported custom vertex streams")
    for field in ("ringBufferMode", "moveWithTransform", "scalingMode"):
        if int(tree.get(field, 0)) != 0:
            raise ValueError(f"unsupported {field}")
    if _constant(tree.get("startDelay"), "startDelay") != 0:
        raise ValueError("unsupported startDelay")
    initial = tree.get("InitialModule", {})
    if initial.get("size3D") or initial.get("rotation3D"):
        raise ValueError("unsupported initial 3D size/rotation")
    if _constant(initial.get("startSpeed"), "startSpeed") != 0:
        raise ValueError("unsupported nonzero startSpeed")
    if _number(initial.get("randomizeRotationDirection", 0)) != 0:
        raise ValueError("unsupported randomizeRotationDirection")
    color = initial.get("startColor", {})
    if int(color.get("minMaxState", 0)) != 0:
        raise ValueError("unsupported startColor gradient")
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
        if item["time"] != 0 or count != 1 or item["cycles"] != 1 or item["probability"] != 1:
            raise ValueError("unsupported burst (only evidenced time-zero single particle)")
        bursts.append(item)
    shape = tree.get("ShapeModule", {})
    if shape.get("enabled") and int(shape.get("type", -1)) != 5:
        raise ValueError("unsupported shape (requires box)")
    if any(_vec(shape.get("m_Rotation"))) or any(
        _number(shape.get(k, 0)) != 0
        for k in ("randomDirectionAmount", "sphericalDirectionAmount", "randomPositionAmount")
    ):
        raise ValueError("unsupported shape rotation/randomization")
    velocity = tree.get("VelocityModule", {}) if tree.get("VelocityModule", {}).get("enabled") else {}
    if velocity.get("inWorldSpace"):
        raise ValueError("unsupported world-space velocity")
    for key in ("orbitalX", "orbitalY", "orbitalZ", "orbitalOffsetX", "orbitalOffsetY", "orbitalOffsetZ", "radial"):
        if _constant(velocity.get(key), key) != 0:
            raise ValueError(f"unsupported velocity {key}")
    if _constant(velocity.get("speedModifier"), "speedModifier", 1) != 1:
        raise ValueError("unsupported velocity speedModifier")
    ranges = [_range(velocity.get(k), k) for k in "xyz"]
    uv = tree.get("UVModule", {})
    tiles, frame_end, cycles = [1, 1], 0, 1
    if uv.get("enabled"):
        if (
            any(int(uv.get(k, 0)) != 0 for k in ("mode", "timeMode", "animationType"))
            or uv.get("flipU")
            or uv.get("flipV")
        ):
            raise ValueError("unsupported UV mode")
        if _constant(uv.get("startFrame"), "startFrame") != 0:
            raise ValueError("unsupported UV startFrame")
        curve = uv.get("frameOverTime", {})
        keys = curve.get("maxCurve", {}).get("m_Curve", ())
        if int(curve.get("minMaxState", -1)) != 1 or len(keys) != 2:
            raise ValueError("unsupported UV curve")
        if (
            [(k.get("time"), k.get("value")) for k in keys] != [(0, 0), (1, 1)]
            or keys[0].get("outSlope") != 1
            or keys[1].get("inSlope") != 1
            or any(k.get("weightedMode", 0) != 0 for k in keys)
        ):
            raise ValueError("unsupported UV curve interpolation")
        tiles = [int(uv.get("tilesX", 1)), int(uv.get("tilesY", 1))]
        frame_end = _number(curve.get("scalar", 1))
        cycles = _number(uv.get("cycles", 1))
        if min(tiles) < 1 or max(tiles) > 64 or not 0 <= frame_end <= 1 or cycles <= 0:
            raise ValueError("invalid UV tile/range")
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
    )
    if (
        result["duration"] <= 0
        or result["simulation_speed"] <= 0
        or result["rate"] < 0
        or not 0 < result["max_particles"] <= 100000
        or result["lifetime"][0] <= 0
        or result["size"][0] < 0
    ):
        raise ValueError("invalid particle bounds/lifetime/rate")
    return result


def normalize_material(tree, shader_name, shader_path_id):
    """Select only names AND exact observed shader identities; never material names."""
    props = tree.get("m_SavedProperties", {})
    floats = dict(props.get("m_Floats", ()))
    colors = dict(props.get("m_Colors", ()))
    keywords = set(tree.get("m_ValidKeywords", ()))
    profile = "unsupported"
    if shader_name == "SoulGames/Effects/Additive" and shader_path_id == -2521961875066890481 and not keywords:
        profile = "additive_v1"
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
            and floats.get("_DstBlend") == 10
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
            profile = "alpha_tint_v1"
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


def enrich_particle(part, index):
    """Read source metadata with exact PPtrs; images stay compressed until extraction."""
    state = part.particle
    if state is None:
        return part, ()
    from dataclasses import replace

    diagnostics = []
    material = part.renderer.material if part.renderer else None
    texture = None
    config = json.loads(state.config_json)
    try:
        if "system" not in config:
            raise ValueError("particle system capability could not be normalized")
        if material is None:
            raise ValueError("particle material PPtr is missing")
        material_tree = index.object_tree(material)
        shader = index.resolve_object_pointer(material, material_tree.get("m_Shader"), expected_type="Shader")
        if shader is None:
            raise ValueError("particle shader identity unavailable (CAB/object not verified)")
        shader_tree = index.object_tree(shader)
        shader_name = str(shader_tree.get("m_ParsedForm", {}).get("m_Name", shader_tree.get("m_Name", "")))
        material_config = normalize_material(material_tree, shader_name, shader.path_id)
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
    return replace(part, particle=state), tuple(diagnostics)


def validate_config(config_json, profile):
    """Validate imported/relocated engineering data independently of parser trust."""
    data = json.loads(config_json)
    if data.get("version") != 1 or profile not in {"additive_v1", "alpha_tint_v1"}:
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
        set(system) - system_fields - {"gravity_modifier", "gravity", "gravity_source", "bursts"}
        or set(material) - material_fields
        or set(data) - {"version", "system", "material"}
    ):
        raise ValueError("unknown particle config fields")

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
            or burst["count"] != 1
            or burst["cycles"] != 1
            or burst["probability"] != 1
            or burst["interval"] < 0
        ):
            raise ValueError("unsupported burst configuration")
    return data
