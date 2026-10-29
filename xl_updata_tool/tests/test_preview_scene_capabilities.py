from types import SimpleNamespace as Obj

from app.features.preview.portrait_config.model import SceneDiagnostic


def scene(*diagnostics, particle=None, status=''):
    spine = Obj(skeleton_json='body', atlases=('atlas',), atlas=None)
    body = Obj(part_id='body', renderer=Obj(enabled=True), spine=spine, sprite=None, particle=None)
    parts = [body]
    if particle:
        parts.append(Obj(part_id='fire', renderer=Obj(enabled=True), spine=None, sprite=None, particle=particle))
    return Obj(parts=tuple(parts), interactions=(), config_metadata=(('export_status', status),),
               validate=lambda: diagnostics)


def test_full_and_partial_are_not_conflated():
    from app.features.preview.scene_capabilities import portrait_scene_capabilities
    ready = portrait_scene_capabilities(scene())
    assert ready.state == 'complete' and ready.can_export
    partial = portrait_scene_capabilities(scene(SceneDiagnostic('unsupported_particle_module', '粒子模块未支持', part_id='fire')))
    assert partial.state == 'partial' and not partial.can_export and partial.can_export_basic


def test_legacy_packages_need_refresh_without_discarding_base_scene():
    from app.features.preview.scene_capabilities import portrait_scene_capabilities
    caps = portrait_scene_capabilities(scene(SceneDiagnostic('portrait_engineering_upgrade_required', '旧版工程', severity='warning')))
    assert caps.state == 'legacy' and caps.needs_refresh
    assert caps.can_export_basic and not caps.can_export


def test_missing_base_assets_must_not_be_offered_as_basic_export():
    from app.features.preview.scene_capabilities import portrait_scene_capabilities
    caps = portrait_scene_capabilities(scene(SceneDiagnostic('missing_asset', '角色骨骼缺失', part_id='body')))
    assert caps.state == 'missing' and not caps.can_export_basic


def test_shader_capability_failure_is_explicit_even_if_no_error_diagnostic():
    from app.features.preview.scene_capabilities import portrait_scene_capabilities
    caps = portrait_scene_capabilities(scene(particle=Obj(profile='unsupported')))
    assert caps.state == 'partial' and caps.has_effects and caps.can_export_basic


def test_region_not_applicable_is_not_a_failure_or_refresh_request():
    from app.features.preview.scene_capabilities import portrait_scene_capabilities
    caps = portrait_scene_capabilities(scene(status='not_applicable'))
    assert caps.state == 'not_applicable'
    assert not caps.can_export and not caps.can_export_basic and not caps.needs_refresh
