"""User-facing scene capabilities, distinct from resource publication status."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SceneCapabilities:
    state: str
    label: str
    reason: str = ''
    can_export: bool = False
    can_export_basic: bool = False
    needs_refresh: bool = False
    has_effects: bool = False
    interaction_count: int = 0


def scene_render_diagnostics(scene):
    inactive = {p.part_id for p in scene.parts if p.renderer is not None and not p.renderer.enabled}
    inactive_refs = {p.object_ref for p in scene.parts if p.part_id in inactive}
    inactive_refs.update(p.particle.system for p in scene.parts if p.part_id in inactive and p.particle)
    return tuple(note for note in scene.validate() if note.part_id not in inactive
                 and not (not note.part_id and note.source is not None and note.source in inactive_refs))


def portrait_scene_capabilities(scene) -> SceneCapabilities:
    if dict(scene.config_metadata).get('export_status') == 'not_applicable':
        return SceneCapabilities('not_applicable', '当前版本不适用')
    active = tuple(p for p in scene.parts if p.renderer is None or p.renderer.enabled)
    particles = tuple(p for p in active if getattr(p, 'particle', None) is not None)
    base = tuple(p for p in active if p.spine or p.sprite)
    notes = scene_render_diagnostics(scene)
    errors = tuple(n for n in notes if n.severity == 'error')
    base_ids = {p.part_id for p in base}
    base_errors = tuple(n for n in errors if n.part_id in base_ids or (
        not n.part_id and not n.code.startswith(('unsupported_', 'particle_', 'material_'))
    ))
    basic = bool(base) and not base_errors
    for part in base:
        if part.spine:
            basic = basic and bool(part.spine.skeleton_json and (part.spine.atlases or part.spine.atlas))
        if part.sprite:
            basic = basic and bool(part.sprite.texture)
    interactions = {(
        event.gesture, event.finger_count, event.callback, event.animation,
        getattr(event, 'track', 0), event.collider,
    ) for event in scene.interactions}
    common = {'can_export_basic': bool(basic), 'has_effects': bool(particles),
              'interaction_count': len(interactions)}
    legacy = any(n.code == 'portrait_engineering_upgrade_required' for n in notes)
    if legacy:
        return SceneCapabilities('legacy', '旧工程待更新', '更新同版工程后可核验完整场景能力',
                                 needs_refresh=True, **common)
    if base_errors or not basic:
        reason = '；'.join(n.message for n in base_errors[:3]) or '缺少可用的场景基础资源'
        return SceneCapabilities('missing', '缺少资源', reason, **common)
    unsupported = tuple(p for p in particles if p.particle.profile not in {'additive_v1', 'alpha_tint_v1'})
    if errors or unsupported:
        reason = '；'.join(n.message for n in errors[:3]) or '场景含暂不支持的特效材质'
        return SceneCapabilities('partial', '特效暂不支持', reason, **common)
    return SceneCapabilities('complete', '可完整导出', can_export=True, **common)
