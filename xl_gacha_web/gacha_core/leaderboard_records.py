"""Decode and collect archived business PB payloads for leaderboard records.

Field numbers and value handling mirror the verified research collectors
(server-exports/leaderboards-20261027/collect_illusion_lineups.py and
probe_guild_reference_waves.py / collect_all_guild_references.py): illusion
lineup 2003 -> 102003 and guild recommendation index 2306 -> 102306.
Both helpers issue exactly one request, stop on the first error, and never
retry. Raw response bytes are delivered to the optional on_response callback
so callers can archive the original PB; int64 identifiers and damage stay
lossless as decimal strings.
"""
from __future__ import annotations

import re

from .gacha import integer
from .leaderboards import _decode

ILLUSION_LINEUP_RPC = 2003
GUILD_RECOMMENDATION_RPC = 2306

_CARD_MAP = {1: ('card_id', 'i32', False), 2: ('skin_id', 'i32', False)}
_LINEUP_ROUND = {1: ('score', 'i64', False), 2: ('cards', _CARD_MAP, True)}
_LINEUP_RESPONSE = {1: ('uin', 'i64', False), 2: ('challenges', _LINEUP_ROUND, True)}

_CARD_SIMPLE = {n: (k, 'i32', False) for n, k in [
    (3, 'cardId'), (4, 'level'), (5, 'quality'), (6, 'grade'),
    (7, 'fashionId'), (8, 'position')]}
_SKILL_LEVEL = {1: ('skillId', 'i32', False), 2: ('level', 'i32', False)}
_FORMATION = {1: ('stageId', 'i32', False), 2: ('fighterUin', 'i64', False),
              3: ('fighterName', 'str', False), 4: ('cardInfos', _CARD_SIMPLE, True),
              6: ('damageHp', 'i64', False), 7: ('battleUid', 'i64', False),
              8: ('skillLevels', _SKILL_LEVEL, True), 9: ('battleTime', 'i32', False),
              10: ('version', 'i32', False)}
_RECOMMENDATION_RESPONSE = {1: ('formations', _FORMATION, True)}

_DECIMAL = re.compile(r'-?\d+')


def _identity_int(value, code):
    if type(value) is int:
        number = value
    elif isinstance(value, str) and _DECIMAL.fullmatch(value):
        number = int(value)
    else:
        raise ValueError(code)
    if number <= 0:
        raise ValueError(code)
    return number


def _checked_callback(on_response):
    if on_response is None:
        return None
    if not callable(on_response):
        raise ValueError('INVALID_RECORD_CALLBACK')  # noqa: TRY004 - ValueError is the protocol contract
    return on_response


def decode_illusion_lineup(body, expected_uin):
    """Decode one 102003 payload into research-aligned rounds.

    Challenges keep server order; card maps are sorted by cardId for a stable
    archive view. Map order is not formation position. Scores, cardId and
    skinId are decimal strings so int64 values stay lossless.
    """
    expected = str(_identity_int(expected_uin, 'INVALID_LINEUP_UIN'))
    decoded = _decode(body, _LINEUP_RESPONSE)
    wire_uin = decoded.get('uin', 0)
    if type(wire_uin) is not int or wire_uin <= 0 or str(wire_uin) != expected:
        raise ValueError('LINEUP_IDENTITY_MISMATCH')
    challenges = decoded.get('challenges', [])
    if len(challenges) > 16:
        raise ValueError('LINEUP_ROUND_LIMIT')
    rounds = []
    for index, record in enumerate(challenges, 1):
        values = record.get('cards', [])
        if len(values) > 32:
            raise ValueError('LINEUP_CARD_LIMIT')
        cards, seen = [], set()
        for item in values:
            card_id, skin_id = item.get('card_id', 0), item.get('skin_id', 0)
            if card_id <= 0 or skin_id < 0 or card_id in seen:
                raise ValueError('INVALID_LINEUP_CARD')
            seen.add(card_id)
            cards.append({'cardId': str(card_id), 'skinId': str(skin_id)})
        rounds.append({'round': index, 'score': str(record.get('score', 0)),
                       'cards': sorted(cards, key=lambda card: int(card['cardId']))})
    return rounds


def decode_guild_recommendations(body, expected_stage_id):
    """Decode one 102306 payload into research-aligned formations.

    fighterUin, battleUid and damageHp become decimal strings (int64 lossless);
    CardSimple/SkillLevel entries and battleTime/version stay numeric. Raw
    damageHp is preserved; share-HP normalization is external config and must
    be applied by the caller without replacing the raw value.
    """
    expected = _identity_int(expected_stage_id, 'INVALID_REFERENCE_STAGE')
    rows = _decode(body, _RECOMMENDATION_RESPONSE).get('formations', [])
    if len(rows) > 100 or any(type(row.get('stageId')) is not int or row.get('stageId') != expected
                              for row in rows):
        raise ValueError('REFERENCE_RESPONSE_IDENTITY_MISMATCH')
    for row in rows:
        if type(row.get('fighterUin')) is not int or row['fighterUin'] <= 0:
            raise ValueError('INVALID_REFERENCE_IDENTITY')
        if type(row.get('battleUid')) is not int or row['battleUid'] <= 0:
            raise ValueError('INVALID_REFERENCE_IDENTITY')
        for key in ('fighterUin', 'battleUid', 'damageHp'):
            if key in row:
                row[key] = str(row[key])
    return rows


def request_illusion_lineup(session, uin, *, on_response=None):
    """Read one player's archived lineup; single attempt, error-stop, no retry."""
    target = _identity_int(uin, 'INVALID_LINEUP_UIN')
    callback = _checked_callback(on_response)
    body = session.request(ILLUSION_LINEUP_RPC, integer(1, target))
    rounds = decode_illusion_lineup(body, target)
    if callback is not None:
        callback(ILLUSION_LINEUP_RPC, body, rounds)
    return rounds


def request_guild_recommendations(session, stage_id, *, on_response=None):
    """Read one stage's top-damage recommendation index; single attempt, no retry."""
    target = _identity_int(stage_id, 'INVALID_REFERENCE_STAGE')
    callback = _checked_callback(on_response)
    body = session.request(GUILD_RECOMMENDATION_RPC, integer(1, target))
    formations = decode_guild_recommendations(body, target)
    if callback is not None:
        callback(GUILD_RECOMMENDATION_RPC, body, formations)
    return formations
