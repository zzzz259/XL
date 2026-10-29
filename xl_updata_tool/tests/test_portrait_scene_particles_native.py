"""Native schema2 material, sampling, streaming and validation regressions."""
import json
import subprocess

import pytest
from PIL import Image

import test_portrait_scene_native as legacy
from test_portrait_scene_native import DOTNET, TOOLS, part

asset = legacy.asset
build_native = legacy.build_native
pytestmark = legacy.pytestmark


def particle(asset, profile='alpha_tint_v1', **changes):
    system = dict(duration=2, loop=True, prewarm=True, simulation_speed=1, rate=1,
                  max_particles=1, lifetime=[2, 2], size=[16, 16], rotation_radians=[0, 0],
                  color=[1, 1, 1, 1], shape_size=[0, 0, 0], shape_position=[0, 0, 0],
                  velocity_min=[0, 0, 0], velocity_max=[0, 0, 0], tiles=[1, 1],
                  uv_frame_end=1, cycles=1, seed=42)
    material = dict(profile=profile, tint=[1, 1, 1, 1], dark_color=[1, 1, 1, 1],
                    factor=2 if profile == 'additive_v1' else 1, dead_strength=0,
                    main_power=1, alpha=1, alpha_ratio=1, blend_amount=0)
    value = dict(id='fire', kind='particle', texture=str(asset / 'white.png'),
                 affine=[1, 0, 0, 1, 0, 0], color=[1, 1, 1, 1], unity_order=0,
                 particle=dict(version=1, system=system, material=material))
    value.update(changes)
    return value


def run(asset, parts, **settings):
    output = asset / ('out.mp4' if settings.get('format') == 'Mp4' else 'out.png')
    desc = dict(scene_schema_version=2, parts=parts, output=str(output),
                settings=dict(format='Png', physics='None', max_resolution=128) | settings)
    descriptor = asset / 'descriptor.json'
    descriptor.write_text(json.dumps(desc))
    result = subprocess.run([DOTNET, str(TOOLS / 'PortraitSceneCLI.dll'), str(descriptor)],
                            capture_output=True, text=True, timeout=30)
    return result, output


def test_native_float_hdr_survives_until_final_mapping(asset):
    # HDR red 2 is later attenuated by a black 50% source: final red 1.
    back = particle(asset)
    back['particle']['material']['tint'] = [2, 0, 0, 1]
    front = particle(asset, id='front', unity_order=1)
    front['particle']['material']['tint'] = [0, 0, 0, .5]
    result, output = run(asset, [back, front])
    assert result.returncode == 0, result.stdout + result.stderr
    assert Image.open(output).convert('RGBA').getpixel((8, 8)) == (255, 0, 0, 255)
    assert json.loads(result.stdout.splitlines()[-1])['float_peak'] >= 2


def test_native_additive_does_not_attenuate_destination(asset):
    back = particle(asset)
    back['particle']['material']['tint'] = [.25, 0, 0, 1]
    front = particle(asset, 'additive_v1', id='front', unity_order=1)
    front['particle']['material']['tint'] = [.25, 0, 0, .25]
    result, output = run(asset, [back, front])
    assert result.returncode == 0, result.stdout + result.stderr
    assert Image.open(output).convert('RGBA').getpixel((8, 8))[0] == pytest.approx(128, abs=1)


def test_native_rejects_unknown_particle_modules_and_profiles(asset):
    for key, value in [('noise', True), ('profile', 'arbitrary')]:
        spec = particle(asset)
        target = spec['particle']['material'] if key == 'profile' else spec['particle']['system']
        target[key] = value
        result, output = run(asset, [spec])
        assert result.returncode == 1
        assert not output.exists()
        assert 'unsupported' in result.stdout.lower() or 'unknown' in result.stdout.lower()


def test_native_particle_stream_has_no_frame_staging(asset):
    result, output = run(asset, [particle(asset)], format='Mp4', duration=.2, fps=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.stat().st_size > 100
    assert not list(asset.glob('*.partial*'))
    assert {p.name for p in asset.glob('*.png')} == {'white.png'}

@pytest.mark.parametrize('track', [0, 1])
def test_native_tracks_preserve_base_and_queue_idle(asset, track):
    spec = part(asset, tracks=[dict(track=track, sequence=[dict(animation='in', loop=False),
                                                         dict(animation='idle', loop=True)])])
    result, _ = run(asset, [spec], time_offset=.7)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout.splitlines()[-1])
    assert summary['parts'][0]['animation'] == 'idle'
    assert summary['parts'][0]['tracks'][0]['animation'] == 'idle'
    assert summary['parts'][0]['track_time'] == pytest.approx(.2 if track == 0 else .7, abs=.01)


def test_native_burst_gravity_and_cap_are_deterministic(asset):
    spec = particle(asset)
    system = spec['particle']['system']
    system.update(rate=0, prewarm=False, loop=False, size=[2, 2], lifetime=[10, 10],
                  gravity_modifier=1, gravity=[0, -8, 0], gravity_source='test_explicit',
                  bursts=[dict(time=0, count=4, cycles=1, interval=0, probability=1)])
    first, output = run(asset, [spec], time_offset=0, max_resolution=512)
    assert first.returncode == 0, first.stdout + first.stderr
    initial = Image.open(output).convert('RGBA')
    initial_center = sum(initial.getbbox()[1::2]) / 2
    second, output = run(asset, [spec], time_offset=1, max_resolution=512)
    assert second.returncode == 0, second.stdout + second.stderr
    moved = Image.open(output).convert('RGBA')
    assert sum(moved.getbbox()[1::2]) / 2 > initial_center
    data = output.read_bytes()
    again, output = run(asset, [spec], time_offset=1, max_resolution=512)
    assert again.returncode == 0
    assert output.read_bytes() == data
    # One particle only despite four simultaneous births: alpha .5 stays .5.
    spec['particle']['material']['tint'][3] = .5
    result, output = run(asset, [spec], time_offset=1, max_resolution=512)
    assert result.returncode == 0
    assert Image.open(output).getchannel('A').getextrema()[1] == pytest.approx(128, abs=1)


def test_native_cooperative_stream_cancel_preserves_final(asset):
    output = asset / 'preserved.mp4'
    output.write_bytes(b'original')
    descriptor = asset / 'cancel.json'
    descriptor.write_text(json.dumps(dict(scene_schema_version=2, parts=[particle(asset)], output=str(output),
                                          settings=dict(format='Mp4', duration=100, fps=30))))
    process = subprocess.Popen([DOTNET, str(TOOLS / 'PortraitSceneCLI.dll'), str(descriptor)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        line = process.stdout.readline()
        assert line, process.stderr.read()
        if '"phase":"export_start"' in line:
            break
    stdout, stderr = process.communicate('cancel\n', timeout=20)
    assert process.returncode == 130, stdout + stderr
    assert output.read_bytes() == b'original'
    assert not list(asset.glob('.*.partial.mp4'))

@pytest.mark.parametrize('pma', [False, True])
def test_native_schema2_spine_pixels_match_legacy(asset, pma):
    from test_portrait_scene_native import render
    Image.new('RGBA', (16, 16), (192, 64, 32, 128)).save(asset / 'white.png')
    spec = part(asset)
    expected, _ = render(asset, [spec], name='legacy', pma=pma)
    result, output = run(asset, [spec], pma=pma)
    assert result.returncode == 0, result.stdout + result.stderr
    actual = Image.open(output).convert('RGBA')
    assert actual.size == expected.size
    assert actual.tobytes() == expected.tobytes()


def test_native_fractional_json_burst_count_is_accepted(asset):
    spec = particle(asset)
    spec['particle']['system']['bursts'] = [dict(time=0, count=1.0, cycles=1, interval=0, probability=1)]
    result, _ = run(asset, [spec])
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_tile_orientation_and_lifetime_sampling(asset):
    image = Image.new('RGBA', (16, 16), 'red')
    for x in range(16):
        for y in range(8, 16):
            image.putpixel((x, y), (0, 0, 255, 255))
    image.save(asset / 'white.png')
    spec = particle(asset)
    spec['particle']['system'].update(rate=0, loop=False, prewarm=False, tiles=[1, 2],
                                     bursts=[dict(time=0, count=1, cycles=1, interval=0, probability=1)])
    result, output = run(asset, [spec], time_offset=.1)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Image.open(output).convert('RGBA').getpixel((11, 11)) == (0, 0, 255, 255)
    result, output = run(asset, [spec], time_offset=1.5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Image.open(output).convert('RGBA').getpixel((11, 11)) == (255, 0, 0, 255)


def test_native_mp4_resolves_transparency_on_black(asset):
    spec = particle(asset)
    spec['particle']['material']['tint'] = [1, 0, 0, .5]
    result, output = run(asset, [spec], format='Mp4', duration=.1, fps=10)
    assert result.returncode == 0, result.stdout + result.stderr
    decoded = asset / 'decoded.png'
    subprocess.run([str(TOOLS / 'ffmpeg.exe'), '-v', 'error', '-i', str(output), '-frames:v', '1', str(decoded)],
                   check=True, capture_output=True)
    assert Image.open(decoded).convert('RGB').getpixel((11, 11))[0] == pytest.approx(128, abs=5)


def test_review_signed_shape_and_tuple_order(asset):
    spec = particle(asset, unity_order=[0, 5, .125, 7], affine=[80, 20, -15, 100, 13, -22])
    spec['particle']['system'].update(shape_size=[1.48, -.17, 1], size=[.1, .1])
    result, output = run(asset, [spec], time_offset=.25, max_resolution=512)
    assert result.returncode == 0, result.stdout + result.stderr
    first = output.read_bytes()
    spec['particle']['system']['shape_size'][1] *= -1
    result, output = run(asset, [spec], time_offset=.25, max_resolution=512)
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_bytes() == first


@pytest.mark.parametrize('blend', ['multiply', 'screen', 'additive'])
def test_review_spine_slot_blend_matches_legacy(asset, blend):
    from test_portrait_scene_native import render
    skeleton = json.loads((asset / 'white.json').read_text())
    skeleton['slots'][0]['blend'] = blend
    (asset / 'front.json').write_text(json.dumps(skeleton))
    back = part(asset, id='back', color=[1, 0, 0, 1], unity_order=[0, 0, 0., 0])
    front = part(asset, id='front', skeleton=str(asset / 'front.json'), color=[0, 0, 1, 1],
                 unity_order=[0, 1, 0., 0])
    legacy_parts = [{k: v for k, v in p.items() if k != 'unity_order'} for p in [front, back]]
    expected, _ = render(asset, legacy_parts, name='blend-legacy')
    result, output = run(asset, [front, back])
    assert result.returncode == 0, result.stdout + result.stderr
    actual = Image.open(output).convert('RGBA')
    assert actual.getpixel((8, 8)) == expected.getpixel((8, 8))


@pytest.mark.parametrize('blend', ['additive', 'normal'])
def test_review_pma_rgb_survives_zero_source_alpha(asset, blend):
    skeleton = json.loads((asset / 'white.json').read_text())
    skeleton['slots'][0]['blend'] = blend
    (asset / 'glow.json').write_text(json.dumps(skeleton))
    Image.new('RGBA', (16, 16), (255, 0, 0, 0)).save(asset / 'glow.png')
    (asset / 'glow.atlas').write_text((asset / 'white.atlas').read_text().replace('white.png', 'glow.png'))
    back = part(asset, id='back', color=[0, 0, 1, 1], unity_order=[0, 0, 0., 0])
    front = part(asset, id='glow', skeleton=str(asset / 'glow.json'), atlas=str(asset / 'glow.atlas'),
                 unity_order=[0, 1, 0., 0])
    legacy_parts = [{k: v for k, v in p.items() if k != 'unity_order'} for p in [front, back]]
    expected, _ = legacy.render(asset, legacy_parts, name='pma-glow-legacy', pma=True)
    result, output = run(asset, [front, back], pma=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Image.open(output).convert('RGBA').getpixel((8, 8)) == expected.getpixel((8, 8))


def test_native_default_fourfold_canvas_policy_without_allocation(asset):
    result = subprocess.run([DOTNET, str(TOOLS / 'PortraitSceneCLI.dll'), '--check-canvas', '5784', '5552'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert report['pixels'] == 5784 * 5552
    assert report['max_pixels'] == 64 * 1024 * 1024
    assert report['estimated_bytes'] >= report['pixels'] * 40
    for width, height in [('8193', '8192'), ('2147483647', '2147483647'), ('0', '1')]:
        failure = subprocess.run([DOTNET, str(TOOLS / 'PortraitSceneCLI.dll'), '--check-canvas', width, height],
                                 capture_output=True, text=True, timeout=10)
        assert failure.returncode == 1
        assert 'canvas' in failure.stdout.lower()
    insufficient = subprocess.run([DOTNET, str(TOOLS / 'PortraitSceneCLI.dll'), '--check-canvas', '5784', '5552', '1024'],
                                  capture_output=True, text=True, timeout=10)
    assert insufficient.returncode == 1
    assert 'available process memory budget' in insufficient.stdout
    assert not (asset / 'out.png').exists()


@pytest.mark.parametrize('settings,error', [
    ({'scale': 1000, 'max_resolution': 9000}, 'pixel limit'),
    ({'scale': 1e9, 'max_resolution': 2147483647}, 'Canvas dimensions'),
    ({'margin': 1073741824, 'max_resolution': 2048}, 'Invalid max_resolution/margin'),
])
def test_native_canvas_preflight_preserves_previous_output(asset, settings, error):
    output = asset / 'out.png'
    output.write_bytes(b'previous final')
    result, actual = run(asset, [part(asset)], **settings)
    assert result.returncode == 1
    assert error in result.stdout
    assert actual.read_bytes() == b'previous final'
    assert not list(asset.glob('.*.partial.png'))
