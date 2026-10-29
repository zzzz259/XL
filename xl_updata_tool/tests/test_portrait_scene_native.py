"""Opt-in pixel integration tests for the bundled native scene renderer."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools" / "SpineViewer"
DOTNET = shutil.which("dotnet")
pytestmark = pytest.mark.skipif(
    os.environ.get("XL_RUN_NATIVE_SCENE_TESTS") != "1", reason="opt-in native rendering integration"
)


@pytest.fixture(scope="module", autouse=True)
def build_native():
    script = ROOT / "scripts" / "build-portrait-scene-renderer.py"
    assert script.is_file(), "native scene build script is missing"
    subprocess.run([sys.executable, str(script)], check=True, capture_output=True, text=True)


@pytest.fixture
def asset(tmp_path):
    Image.new("RGBA", (16, 16), (255, 255, 255, 255)).save(tmp_path / "white.png")
    (tmp_path / "white.atlas").write_text(
        "white.png\nsize: 16,16\nformat: RGBA8888\nfilter: Nearest,Nearest\nrepeat: none\nwhite\n  rotate: false\n  xy: 0, 0\n  size: 16, 16\n  orig: 16, 16\n  offset: 0, 0\n  index: -1\n"
    )
    skel = {
        "skeleton": {"spine": "3.8.99", "hash": "", "x": -8, "y": -8, "width": 16, "height": 16},
        "bones": [{"name": "root"}],
        "slots": [{"name": "slot", "bone": "root", "attachment": "white"}],
        "skins": [{"name": "default", "attachments": {"slot": {"white": {"width": 16, "height": 16}}}}],
        "animations": {
            "idle": {"bones": {"root": {"translate": [{"time": 0, "x": 0}, {"time": 1, "x": 20}]}}},
            "in": {"bones": {"root": {"translate": [{"time": 0, "x": -20}, {"time": 0.5, "x": 0}]}}},
        },
    }
    (tmp_path / "white.json").write_text(json.dumps(skel))
    return tmp_path


def part(asset, **kw):
    value = dict(
        id="p",
        skeleton=str(asset / "white.json"),
        atlas=str(asset / "white.atlas"),
        animation="idle",
        loop=False,
        skin="default",
        time_scale=1,
        sequence=[],
        affine=[1, 0, 0, 1, 0, 0],
        color=[1, 1, 1, 1],
        fade_in=0,
    )
    value.update(kw)
    return value


def render(asset, parts, name="result", policy="time_offset", **settings):
    output = asset / (name + ".png")
    desc = dict(
        scene_schema_version=1,
        parts=parts,
        output=str(output),
        snapshot_policy=policy,
        settings=dict(
            format="Png",
            static=True,
            scale=1,
            max_resolution=512,
            margin=0,
            transparent=True,
            pma=False,
            fps=20,
            physics="None",
            warm_up=0,
            time_offset=0,
            duration=None,
            speed=1,
        )
        | settings,
    )
    descriptor = asset / (name + ".json")
    descriptor.write_text(json.dumps(desc))
    proc = subprocess.run(
        [DOTNET, str(TOOLS / "PortraitSceneCLI.dll"), str(descriptor)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads(proc.stdout.splitlines()[-1])
    return Image.open(output).convert("RGBA"), summary


def test_identity_layer_order_and_pma(asset):
    image, info = render(asset, [part(asset, color=[1, 0, 0, 1]), part(asset, id="back", color=[0, 0, 1, 1])])
    assert image.getpixel((image.width // 2, image.height // 2)) == (255, 0, 0, 255)
    assert info["parts"][0]["animation"] == "idle"
    legacy = asset / "legacy.png"
    subprocess.run(
        [
            DOTNET,
            str(TOOLS / "SpineViewerCLI.dll"),
            "export",
            str(asset / "white.json"),
            "--atlas",
            str(asset / "white.atlas"),
            "-f",
            "Png",
            "-o",
            str(legacy),
            "-a",
            "idle",
            "--physics",
            "None",
            "--color",
            "#00000000",
            "--max-resolution",
            "512",
            "--no-progress",
        ],
        check=True,
        capture_output=True,
    )
    identity, _ = render(asset, [part(asset)], name="identity")
    assert identity.size == Image.open(legacy).size
    assert identity.tobytes() == Image.open(legacy).convert("RGBA").tobytes()
    pma, _ = render(asset, [part(asset)], name="pma", pma=True)
    assert pma.tobytes() == identity.tobytes()


@pytest.mark.parametrize(
    "affine, expected",
    [
        ([1, 0, 0, 1, 30, 0], (16, 16)),
        ([-1, 0, 0, 1, 0, 0], (16, 16)),
        ([1, 0.5, 0, 0.5, 0, 0], (24, 8)),
        ([0, -1, 1, 0, 0, 0], (16, 16)),
    ],
)
def test_affine(asset, affine, expected):
    image, info = render(asset, [part(asset, affine=affine)])
    assert image.size == expected
    assert image.getbbox() is not None


def test_independent_clocks_and_queued_transition(asset):
    _, info = render(
        asset, [part(asset, id="frozen", time_scale=0), part(asset, id="fast", time_scale=2)], time_offset=0.25
    )
    assert info["parts"][0]["track_time"] == 0
    assert info["parts"][1]["track_time"] == pytest.approx(0.5, abs=1e-5)
    _, info = render(
        asset,
        [part(asset, sequence=[dict(animation="in", loop=False), dict(animation="idle", loop=True)], fade_in=0.8)],
        policy="entrance_complete",
    )
    assert info["snapshot_time"] == pytest.approx(0.8)
    assert info["parts"][0]["animation"] == "idle"
    assert info["parts"][0]["alpha"] == pytest.approx(1)


def test_invalid_clock_preserves_output(asset):
    output = asset / "bad.png"
    output.write_bytes(b"original")
    descriptor = asset / "bad.json"
    descriptor.write_text(
        json.dumps(dict(scene_schema_version=1, output=str(output), parts=[part(asset, time_scale=-1)], settings={}))
    )
    proc = subprocess.run(
        [DOTNET, str(TOOLS / "PortraitSceneCLI.dll"), str(descriptor)], capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert "time_scale" in proc.stdout + proc.stderr
    assert output.read_bytes() == b"original"


def test_relative_translation_and_mirror_pixels(asset):
    texture = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    for x in range(8, 16):
        for y in range(16):
            texture.putpixel((x, y), (0, 0, 255, 255))
    texture.save(asset / "white.png")
    image, _ = render(asset, [part(asset), part(asset, id="mirror", affine=[-1, 0, 0, 1, 24, 0])])
    assert image.size == (40, 16)
    assert image.getpixel((2, 8)) == (255, 0, 0, 255)
    assert image.getpixel((13, 8)) == (0, 0, 255, 255)
    assert image.getpixel((26, 8)) == (0, 0, 255, 255)
    assert image.getpixel((37, 8)) == (255, 0, 0, 255)
    assert image.getpixel((20, 8))[3] == 0


@pytest.mark.parametrize("pma", [False, True])
def test_semialpha_matches_vendor(asset, pma):
    Image.new("RGBA", (16, 16), (64, 128, 192, 128)).save(asset / "white.png")
    actual, _ = render(asset, [part(asset)], pma=pma)
    output = asset / "legacy-alpha.png"
    command = [
        DOTNET,
        str(TOOLS / "SpineViewerCLI.dll"),
        "export",
        str(asset / "white.json"),
        "--atlas",
        str(asset / "white.atlas"),
        "-f",
        "Png",
        "-o",
        str(output),
        "-a",
        "idle",
        "--physics",
        "None",
        "--color",
        "#00000000",
        "--no-progress",
    ]
    if pma:
        command.append("--pma")
    subprocess.run(command, check=True, capture_output=True)
    assert actual.tobytes() == Image.open(output).convert("RGBA").tobytes()


def test_video_duration_uses_slowest_complete_sequence(asset):
    output = asset / "sequence.mp4"
    desc = dict(
        scene_schema_version=1,
        parts=[
            part(
                asset,
                id="slow",
                time_scale=0.5,
                sequence=[dict(animation="in", loop=False), dict(animation="idle", loop=True)],
                fade_in=0.5,
            ),
            part(asset, id="fast", time_scale=2),
        ],
        output=str(output),
        snapshot_policy="entrance_complete",
        settings=dict(
            format="Mp4",
            static=False,
            scale=1,
            max_resolution=512,
            margin=0,
            transparent=True,
            pma=False,
            fps=10,
            physics="None",
            warm_up=0,
            time_offset=0,
            duration=None,
            speed=2,
        ),
    )
    descriptor = asset / "video.json"
    descriptor.write_text(json.dumps(desc))
    proc = subprocess.run(
        [DOTNET, str(TOOLS / "PortraitSceneCLI.dll"), str(descriptor)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads(proc.stdout.splitlines()[-1])
    assert summary["duration"] == pytest.approx(1.5)
    assert summary["parts"][0]["animation"] == "idle"
    probe = subprocess.run([str(TOOLS / "ffmpeg.exe"), "-i", str(output)], capture_output=True, text=True)
    assert "Duration: 00:00:01.60" in probe.stderr
    # The vendor exporter includes t=0 and the endpoint. Verify both fade origin
    # and later independent movement on its stable union canvas.
    subprocess.run(
        [
            str(TOOLS / "ffmpeg.exe"),
            "-y",
            "-i",
            str(output),
            "-vf",
            "select=eq(n\\,0)+eq(n\\,8)+eq(n\\,15)",
            "-fps_mode",
            "passthrough",
            str(asset / "frame-%02d.png"),
        ],
        check=True,
        capture_output=True,
    )
    assert len(list(asset.glob("frame-*.png"))) == 3


def test_frozen_part_clock_remains_zero_in_video(asset):
    output = asset / "frozen.mp4"
    descriptor = asset / "frozen.json"
    descriptor.write_text(
        json.dumps(
            dict(
                scene_schema_version=1,
                output=str(output),
                parts=[part(asset, id="frozen", time_scale=0, fade_in=0.4)],
                settings=dict(format="Mp4", static=False, fps=10, physics="Update", duration=0.5),
            )
        )
    )
    proc = subprocess.run(
        [DOTNET, str(TOOLS / "PortraitSceneCLI.dll"), str(descriptor)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads(proc.stdout.splitlines()[-1])
    assert summary["parts"][0]["clock_time"] == 0
    assert summary["parts"][0]["track_time"] == 0
    assert summary["parts"][0]["alpha"] == 1


def test_entrance_snapshot_reaches_exact_queue_boundary(asset):
    _, summary = render(
        asset,
        [part(asset, sequence=[dict(animation="in", loop=False), dict(animation="idle", loop=True)])],
        policy="entrance_complete",
    )
    assert summary["parts"][0]["animation"] == "idle"
    assert summary["snapshot_time"] == pytest.approx(0.5, abs=0.001)


def test_shear_and_rotation_direction_pixels(asset):
    image, _ = render(asset, [part(asset, affine=[1, 0.5, 0, 0.5, 0, 0])], name="shear")
    assert image.getpixel((2, 1))[3] == 0
    assert image.getpixel((21, 1))[3] == 255
    assert image.getpixel((2, 6))[3] == 255
    assert image.getpixel((21, 6))[3] == 0
    texture = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    for x in range(8, 16):
        for y in range(16):
            texture.putpixel((x, y), (0, 0, 255, 255))
    texture.save(asset / "white.png")
    rotated, _ = render(asset, [part(asset, affine=[0, -1, 1, 0, 0, 0])], name="rotation")
    assert rotated.getpixel((8, 2)) == (0, 0, 255, 255)
    assert rotated.getpixel((8, 13)) == (255, 0, 0, 255)


def test_pipe_cancellation_preserves_final_and_cleans_partial(asset):
    output = asset / "cancel.mp4"
    output.write_bytes(b"original")
    descriptor = asset / "cancel.json"
    descriptor.write_text(
        json.dumps(
            dict(
                scene_schema_version=1,
                output=str(output),
                parts=[part(asset)],
                settings=dict(format="Mp4", static=False, fps=30, physics="None", duration=100),
            )
        )
    )
    proc = subprocess.Popen(
        [DOTNET, str(TOOLS / "PortraitSceneCLI.dll"), str(descriptor)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    while True:
        line = proc.stdout.readline()
        assert line, proc.stderr.read()
        if '"phase":"export_start"' in line:
            break
    stdout, stderr = proc.communicate("cancel\n", timeout=20)
    assert proc.returncode == 130, stdout + stderr
    assert '"phase":"cancelled"' in stdout
    assert output.read_bytes() == b"original"
    assert not list(asset.glob(".*.partial.mp4"))


def test_build_creates_requested_work_directory_and_is_deterministic(tmp_path):
    import hashlib

    script = ROOT / "scripts" / "build-portrait-scene-renderer.py"
    scratch = tmp_path / "not-created-yet"
    before = hashlib.sha256((TOOLS / "PortraitSceneCLI.dll").read_bytes()).hexdigest()
    subprocess.run(
        [sys.executable, str(script), "--work-dir", str(scratch)], check=True, capture_output=True, text=True
    )
    assert scratch.is_dir()
    assert not list(scratch.iterdir())
    assert hashlib.sha256((TOOLS / "PortraitSceneCLI.dll").read_bytes()).hexdigest() == before


@pytest.mark.parametrize("source_y", [40, -40])
def test_nonorigin_identity_vertical_orientation_matches_vendor(asset, source_y):
    skeleton = asset / "white.json"
    data = json.loads(skeleton.read_text())
    data["bones"][0].update(x=12, y=source_y)
    skeleton.write_text(json.dumps(data))
    texture = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    for x in range(16):
        for y in range(8, 16):
            texture.putpixel((x, y), (0, 0, 255, 255))
    texture.save(asset / "white.png")
    image, summary = render(asset, [part(asset)])
    assert image.getbbox() == (0, 0, 16, 16)
    vendor = asset / "vendor-nonorigin.png"
    subprocess.run([DOTNET, str(TOOLS / "SpineViewerCLI.dll"), "export", str(skeleton), "--atlas", str(asset / "white.atlas"), "-f", "Png", "-o", str(vendor), "-a", "idle", "--physics", "None", "--color", "#00000000", "--no-progress"], check=True, capture_output=True)
    expected = Image.open(vendor).convert("RGBA")
    assert image.size == expected.size
    assert image.tobytes() == expected.tobytes()
    assert image.getpixel((8, 2)) == (255, 0, 0, 255)
    assert image.getpixel((8, 13)) == (0, 0, 255, 255)
    assert summary["bounds"][1] == pytest.approx(-source_y - 8)
    assert summary["bounds"][3] == pytest.approx(-source_y + 8)
