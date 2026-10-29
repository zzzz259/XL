import json
from pathlib import Path
from app.features.preview.portrait_config import particle_metadata
import pytest


def _tree():
    def curve(v):
        return {"minMaxState": 0, "scalar": v}

    return {
        "lengthInSec": 5,
        "looping": True,
        "prewarm": True,
        "simulationSpeed": 1,
        "InitialModule": {
            "enabled": True,
            "startLifetime": curve(2),
            "startSize": curve(1),
            "startRotation": curve(0),
            "startSpeed": curve(0),
            "startColor": {"minMaxState": 0, "maxColor": dict(r=1, g=1, b=1, a=1)},
            "gravityModifier": curve(0),
            "maxNumParticles": 50,
        },
        "ShapeModule": {"enabled": True, "type": 5, "m_Scale": dict(x=1, y=1, z=1)},
        "EmissionModule": {"enabled": True, "rateOverTime": curve(10)},
        "VelocityModule": {"enabled": False},
        "UVModule": {"enabled": False},
    }


def test_strict_particle_rejects_unknown_active_modules_and_curves():
    tree = _tree()
    tree["NoiseModule"] = {"enabled": True}
    with pytest.raises(ValueError, match="NoiseModule"):
        particle_metadata.normalize_system(tree, {}, 1)
    tree = _tree()
    tree["InitialModule"]["startLifetime"] = {"minMaxState": 1}
    with pytest.raises(ValueError, match="startLifetime"):
        particle_metadata.normalize_system(tree, {}, 1)


def test_particle_normalizes_constants_and_never_drops_gravity_or_burst():
    tree = _tree()
    tree["InitialModule"]["gravityModifier"]["scalar"] = -0.1
    tree["EmissionModule"]["m_Bursts"] = [
        dict(time=0, countCurve={"minMaxState": 0, "scalar": 1}, cycleCount=1, repeatInterval=0.01, probability=1)
    ]
    result = particle_metadata.normalize_system(tree, {"m_RenderMode": 0}, 17)
    assert result["gravity_modifier"] == -0.1
    assert result["gravity_source"] == "unity_default_assumption"
    assert result["bursts"][0]["count"] == 1
    assert result["seed"] == 17
    assert result["lifetime"] == [2, 2]


def test_evidenced_fire_modules_material_hdr_and_invalid_uv_regression():
    import copy

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/portrait_fire_particle_v1.json").read_text(encoding="utf-8")
    )
    objects = fixture["objects"]
    systems = [rec["tree"] for rec in objects.values() if rec["type"] == "ParticleSystem"]
    renderers = [rec["tree"] for rec in objects.values() if rec["type"] == "ParticleSystemRenderer"]
    for tree, renderer in zip(systems, renderers, strict=True):
        result = particle_metadata.normalize_system(tree, renderer, 123)
        assert result["tiles"] == [4, 4] and result["max_particles"] == 50
        assert result["rate"] == pytest.approx(17.2)
        assert result["bursts"][0]["count"] == 1
        assert result["lifetime"] == [1, 2]
        broken = copy.deepcopy(tree)
        broken["UVModule"]["frameOverTime"]["maxCurve"]["m_Curve"][0]["outSlope"] = 0
        with pytest.raises(ValueError, match="interpolation"):
            particle_metadata.normalize_system(broken, renderer, 123)
    configs = [
        particle_metadata.normalize_material(r["material"], r["shader_name"], r["material"]["m_Shader"]["m_PathID"])
        for r in fixture["materials"]
    ]
    assert {c["profile"] for c in configs} == {"additive_v1", "alpha_tint_v1"}
    assert next(c for c in configs if c["profile"] == "alpha_tint_v1")["tint"][0] > 1.5
    assert next(c for c in configs if c["profile"] == "additive_v1")["factor"] == 2
    material = copy.deepcopy(fixture["materials"][0]["material"])
    material["m_ValidKeywords"].append("_NOISE_ON")
    with pytest.raises(ValueError, match="shader/variant"):
        particle_metadata.normalize_material(
            material, fixture["materials"][0]["shader_name"], material["m_Shader"]["m_PathID"]
        )


def test_relocated_particle_config_rejects_unknown_optional_bursts_and_nonfinite_values():
    tree = _tree()
    system = particle_metadata.normalize_system(tree, {}, 1)
    material = dict(
        profile="additive_v1",
        tint=[1, 1, 1, 1],
        dark_color=[1, 1, 1, 1],
        factor=2,
        dead_strength=0,
        main_power=1,
        alpha=1,
        alpha_ratio=1,
        blend_amount=0,
    )
    data = dict(version=1, system=system, material=material)
    particle_metadata.validate_config(json.dumps(data), "additive_v1")
    data["system"]["bursts"] = [dict(time=0, count=12, cycles=1, interval=0, probability=1)]
    with pytest.raises(ValueError, match="burst"):
        particle_metadata.validate_config(json.dumps(data), "additive_v1")
    data["system"]["bursts"] = []
    data["material"]["alpha"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        particle_metadata.validate_config(json.dumps(data), "additive_v1")
