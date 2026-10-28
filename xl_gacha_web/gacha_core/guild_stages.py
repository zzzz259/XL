"""Guild war phase -> stage mapping shipped as a repository resource.

Built from the static protocol evidence in
``server-exports/leaderboards-20261027/guild-reference-protocol.json``
(phaseConfigs + chapterRoundMaps). Phase layouts repeat by design
(consecutive phases reuse the same chapter/stage ids), so stage identity is
unique within one phase only. Unknown phases return ``None`` everywhere and
are never guessed.
"""
from __future__ import annotations

import json
from pathlib import Path

RESOURCE_FORMAT = 'guild-war-stages/1'
_STAGE_KEYS = frozenset(('stage_id', 'chapter_id', 'round', 'boss_index',
                         'boss_id', 'share_hp_count'))


def default_resource_path() -> Path:
    return Path(__file__).resolve().parents[1] / 'data' / 'guild_war_stages.json'


def _positive_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f'Invalid {label} type in guild war stage resource')
    if value <= 0:
        raise ValueError(f'Invalid {label} in guild war stage resource')
    return value


class GuildWarStageMap:
    """Validated, read-only view of one ``guild-war-stages/1`` resource."""

    def __init__(self, resource):
        if not isinstance(resource, dict) or resource.get('format') != RESOURCE_FORMAT:
            raise ValueError('Unsupported guild war stage resource format')
        phases = resource.get('phases')
        if not isinstance(phases, dict) or not phases:
            raise ValueError('Guild war stage resource has no phases')
        self._phases = {}
        for raw_phase, raw_cfg in phases.items():
            if isinstance(raw_phase, bool) or not isinstance(raw_phase, (int, str)):
                raise TypeError('Invalid phase key in guild war stage resource')
            phase = _positive_int(int(raw_phase), 'phase')
            if not isinstance(raw_cfg, dict):
                raise TypeError('Invalid phase entry in guild war stage resource')
            stages = raw_cfg.get('stages')
            stage_count = raw_cfg.get('stage_count')
            if (not isinstance(stages, list) or not stages
                    or type(stage_count) is not int):
                raise ValueError('Invalid phase stage list in guild war stage resource')
            if len(stages) != stage_count:
                raise ValueError(f'Phase {phase} stage count mismatch')
            seen = set()
            ordered = []
            for raw_stage in stages:
                if not isinstance(raw_stage, dict):
                    raise TypeError('Invalid stage entry in guild war stage resource')
                if set(raw_stage) - _STAGE_KEYS:
                    raise ValueError('Unknown stage keys in guild war stage resource')
                stage_id = _positive_int(raw_stage.get('stage_id'), 'stage')
                if stage_id in seen:
                    raise ValueError(f'Duplicate stage {stage_id} within phase {phase}')
                seen.add(stage_id)
                chapter_id = _positive_int(raw_stage.get('chapter_id'), 'chapter')
                round_index = _positive_int(raw_stage.get('round'), 'round')
                boss_index = _positive_int(raw_stage.get('boss_index'), 'boss index')
                boss_id = raw_stage.get('boss_id')
                if boss_id is not None:
                    _positive_int(boss_id, 'boss id')
                share_hp = raw_stage.get('share_hp_count')
                if share_hp is not None:
                    _positive_int(share_hp, 'share HP count')
                ordered.append({'stage_id': stage_id, 'chapter_id': chapter_id,
                                'round': round_index, 'boss_index': boss_index,
                                'boss_id': boss_id, 'share_hp_count': share_hp})
            self._phases[phase] = {
                'config_id': raw_cfg.get('config_id'),
                'notice_begin': raw_cfg.get('notice_begin'),
                'challenge_begin': raw_cfg.get('challenge_begin'),
                'challenge_end': raw_cfg.get('challenge_end'),
                'stage_count': stage_count,
                'stages': ordered,
            }
        self._by_stage = {}
        for phase, cfg in self._phases.items():
            for stage in cfg['stages']:
                self._by_stage.setdefault(stage['stage_id'], stage)

    def phases(self):
        return tuple(sorted(self._phases))

    def stage_ids(self, phase):
        cfg = self._phases.get(phase if type(phase) is int else int(phase))
        if cfg is None:
            return None
        return tuple(stage['stage_id'] for stage in cfg['stages'])

    def expected_stage_count(self, phase):
        cfg = self._phases.get(phase if type(phase) is int else int(phase))
        return None if cfg is None else cfg['stage_count']

    def stage(self, stage_id):
        try:
            key = stage_id if type(stage_id) is int else int(stage_id)
        except (TypeError, ValueError):
            return None
        return self._by_stage.get(key)

    def share_hp_count(self, stage_id):
        stage = self.stage(stage_id)
        return None if stage is None else stage['share_hp_count']


def load_stage_map(path=None) -> GuildWarStageMap:
    resource_path = Path(path) if path is not None else default_resource_path()
    with resource_path.open(encoding='utf-8') as handle:
        return GuildWarStageMap(json.load(handle))


def display_damage_hp(raw_damage_hp, version, share_hp_count):
    """Research-aligned normalization: shared-HP stages divide by the share
    count, rounded up, unless the row already reports version 1.

    Integer arithmetic only: values beyond 2**53 would silently round through
    IEEE754 doubles, so ceil is computed as (raw + share - 1) // share.
    """
    if type(raw_damage_hp) is not int or isinstance(raw_damage_hp, bool):
        raise ValueError('Invalid raw damage HP')
    if type(version) is not int or isinstance(version, bool) or version < 0:
        raise ValueError('Invalid formation version')
    if share_hp_count is None:
        return raw_damage_hp
    if type(share_hp_count) is not int or isinstance(share_hp_count, bool):
        raise ValueError('Invalid share HP count')
    if version == 1 or share_hp_count <= 0:
        return raw_damage_hp
    return (raw_damage_hp + share_hp_count - 1) // share_hp_count
