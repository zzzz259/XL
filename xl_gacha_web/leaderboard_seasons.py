"""Normalized, per-season leaderboard history with full membership and battle identity.

Independent ``seasonal_leaderboards.sqlite3`` storage for one game season at a time:
every run keeps the full visible member list, per-battle lineup/presence/formations,
page evidence, and raw business payloads deduplicated by battleUid/SHA256. Presence
history (first/last seen, first absence, rejoin) is derived only from complete
same-range snapshots; PARTIAL coverage never marks a member as gone. battleUids are
retained permanently and stay linked to the snapshots that observed them.

No login, network, or gacha dependency. Public methods return JSON-compatible values.
"""

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
MAX_PAGE_ENTRIES = 1000
MAX_PAGE_MEMBERS = 1000
MAX_PAGE_BATTLES = 512
MAX_LINEUP_ROWS = 4096
MAX_RAW_PER_PAGE = 128
MAX_RAW_BYTES = 4 * 1024 * 1024

TRIGGERS = ('ROUTINE', 'PRE_END_300S', 'PRE_END_120S', 'PRE_END_60S', 'PRE_END_30S',
            'PRE_END_15S', 'POST_CLOSE_1H', 'POST_CLOSE_UNAVAILABLE')
TRIGGER_SET = frozenset(TRIGGERS)
AVAILABILITY = {'NOT_OPEN', 'NOTICE', 'ACTIVE', 'SETTLING', 'SETTLED_CANDIDATE',
                'CLOSED', 'UNKNOWN', 'EMPTY'}
COVERAGE = {'NO_RANKING', 'PARTIAL', 'INCONSISTENT', 'SERVER_VISIBLE_SLICE',
            'COMPLETE_REPORTED_RANGE'}
SETTLE = {'LIVE', 'LAST_OBSERVED', 'SETTLED_CANDIDATE'}
COMPLETE_COVERAGES = ('COMPLETE_REPORTED_RANGE', 'SERVER_VISIBLE_SLICE')
PRESENCE_STATUS = {'PRESENT', 'ABSENT', 'UNKNOWN'}
FORMATION_KEYS = {
    'boss_name': 'text', 'boss_level': 'int', 'frontline': 'int', 'chapter': 'int',
    'stage': 'int', 'stageId': 'int', 'battleTime': 'int', 'version': 'int',
    'fighterUin': 'digits', 'fighterName': 'text', 'damageHp': 'digits',
    'displayDamageHp': 'digits', 'battleUid': 'digits',
    'cardInfos': 'raw', 'skillLevels': 'raw', 'cards': 'raw',
}

TABLE_NAMES = frozenset((
    'seasons', 'runs', 'snapshots', 'rank_entries', 'snapshot_membership',
    'page_evidence', 'raw_blobs', 'snapshot_blobs', 'battles',
    'snapshot_battles', 'guild_formations', 'lineup', 'presence',
    'schedule_slots'))
COUNTED_TABLES = ('runs', 'snapshots', 'rank_entries', 'snapshot_membership',
                  'page_evidence', 'snapshot_blobs', 'battles',
                  'snapshot_battles', 'guild_formations', 'lineup', 'presence',
                  'schedule_slots', 'raw_blobs')

_META = frozenset(('seasonId', 'season', 'phase', 'state', 'startStamp', 'endStamp',
                   'nextStartStamp', 'settle', 'inGuild', 'isStart', 'fighterCount',
                   'challengeCount', 'highScore', 'curChapter', 'curStage', 'bossHpMax',
                   'bossHpCur', 'rank', 'rankRatio', 'highScoreDiff', 'rewardStages',
                   'schedule', 'time_basis', 'member_scope', 'recheck',
                   'collection_started_at_utc', 'collection_finished_at_utc'))
_RAW = frozenset(('guildUid', 'uin', 'level', 'iconId', 'name', 'rank', 'score',
                  'memberCount', 'actCount', 'round', 'faceId', 'guildName',
                  'scoreDiff', 'damage', 'hero',
                  'stageId', 'fighterUin', 'fighterName', 'cardInfos', 'damageHp',
                  'displayDamageHp', 'battleUid', 'skillLevels', 'battleTime', 'version',
                  'cards', 'cardId', 'skinId', 'skillId', 'position', 'quality',
                  'grade', 'fashionId', 'cardUid'))
_PAGE = frozenset(('request_index', 'echoed_index', 'count', 'source_hash',
                   'request_cursor', 'echoed_cursor', 'reported_total', 'total',
                   'first_rank', 'last_rank', 'source_rpc', 'hash', 'rpc',
                   'entry_count', 'member_count', 'season_id', 'recheck',
                   'is_recheck'))

_SCHEMA = (
    'CREATE TABLE seasons (season_key INTEGER PRIMARY KEY, scope TEXT NOT NULL, mode TEXT NOT NULL, season_id TEXT NOT NULL, created_at_utc TEXT NOT NULL, sealed_at_utc TEXT, UNIQUE(scope,mode,season_id))',
    'CREATE TABLE runs (run_id TEXT PRIMARY KEY, season_key INTEGER NOT NULL REFERENCES seasons(season_key), trigger TEXT NOT NULL, code_version TEXT NOT NULL, content_hash TEXT, status TEXT NOT NULL, pages_count INTEGER NOT NULL DEFAULT 0, checkpoint_page INTEGER NOT NULL DEFAULT 0, checkpoint_payload TEXT, interrupt_reason TEXT, snapshot_id INTEGER, metadata TEXT NOT NULL, collected_at_utc TEXT NOT NULL, started_at_utc TEXT NOT NULL, finished_at_utc TEXT)',
    'CREATE TABLE snapshots (snapshot_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id), season_key INTEGER NOT NULL REFERENCES seasons(season_key), trigger TEXT NOT NULL, availability TEXT NOT NULL, coverage TEXT NOT NULL, settle_evidence TEXT NOT NULL, collected_at_utc TEXT NOT NULL, metadata TEXT NOT NULL)',
    'CREATE TABLE rank_entries (snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id), ordinal INTEGER NOT NULL, entity_id TEXT NOT NULL, rank INTEGER, score INTEGER, display_name TEXT, guild_name TEXT, payload TEXT NOT NULL, PRIMARY KEY(snapshot_id,ordinal), UNIQUE(snapshot_id,entity_id))',
    'CREATE TABLE snapshot_membership (snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id), ordinal INTEGER NOT NULL, member_uid TEXT NOT NULL, payload TEXT NOT NULL, observed_at_utc TEXT NOT NULL, PRIMARY KEY(snapshot_id,ordinal), UNIQUE(snapshot_id,member_uid))',
    'CREATE TABLE page_evidence (snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id), ordinal INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(snapshot_id,ordinal))',
    'CREATE TABLE raw_blobs (sha256 TEXT PRIMARY KEY, byte_length INTEGER NOT NULL, payload BLOB, first_seen_utc TEXT NOT NULL)',
    'CREATE TABLE snapshot_blobs (snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id), sha256 TEXT NOT NULL REFERENCES raw_blobs(sha256), ordinal INTEGER NOT NULL, PRIMARY KEY(snapshot_id,ordinal))',
    'CREATE TABLE battles (battle_uid TEXT NOT NULL, season_key INTEGER NOT NULL REFERENCES seasons(season_key), first_snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id), raw_sha256 TEXT REFERENCES raw_blobs(sha256), created_at_utc TEXT NOT NULL, PRIMARY KEY(battle_uid,season_key))',
    'CREATE TABLE snapshot_battles (snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id), battle_uid TEXT NOT NULL, season_key INTEGER NOT NULL, ordinal INTEGER NOT NULL, PRIMARY KEY(snapshot_id,ordinal), FOREIGN KEY(battle_uid,season_key) REFERENCES battles(battle_uid,season_key))',
    'CREATE TABLE guild_formations (battle_uid TEXT NOT NULL, season_key INTEGER NOT NULL, battle_round INTEGER, payload TEXT NOT NULL, PRIMARY KEY(battle_uid,season_key), FOREIGN KEY(battle_uid,season_key) REFERENCES battles(battle_uid,season_key))',
    'CREATE TABLE lineup (battle_uid TEXT NOT NULL, season_key INTEGER NOT NULL, ordinal INTEGER NOT NULL, member_uid TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(battle_uid,season_key,ordinal), FOREIGN KEY(battle_uid,season_key) REFERENCES battles(battle_uid,season_key))',
    'CREATE TABLE presence (battle_uid TEXT NOT NULL, season_key INTEGER NOT NULL, member_uid TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(battle_uid,season_key,member_uid), FOREIGN KEY(battle_uid,season_key) REFERENCES battles(battle_uid,season_key))',
    'CREATE TABLE schedule_slots (season_key INTEGER NOT NULL REFERENCES seasons(season_key), slot TEXT NOT NULL, fire_at_utc TEXT NOT NULL, status TEXT NOT NULL, run_id TEXT, updated_at_utc TEXT NOT NULL, PRIMARY KEY(season_key,slot))',
)


def _text(value, *, limit=4096, nonempty=False):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise ValueError('Invalid text field')
    if nonempty and not value.strip():
        raise ValueError('Empty identity field')
    return value


def _identifier(value, *, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('Invalid identifier')  # noqa: TRY004 - unified season history validation contract  # noqa: TRY004 - unified season history validation contract
    return _text(str(value), limit=256, nonempty=True)


def _season_id(value):
    season = _identifier(value)
    if season == '0' or (season.lstrip('-').isdigit() and int(season) <= 0):
        raise ValueError('Invalid season identity')
    return season


def _integer(value):
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise ValueError('Integer outside signed int64')
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _scalar(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return _integer(value)
    return _text(value)


def _allowed(value, keys, depth=0):
    """Drop unknown keys, recursively; never stringify arbitrary user objects."""
    if not isinstance(value, dict) or len(value) > 256 or depth > 4:
        raise ValueError('Invalid evidence object')
    result = {}
    for key in keys.intersection(value):
        item = value[key]
        if isinstance(item, dict):
            result[key] = _allowed(item, keys, depth + 1)
        elif isinstance(item, list):
            if len(item) > 512:
                raise ValueError('Evidence list too large')
            result[key] = [_allowed(x, keys, depth + 1) if isinstance(x, dict)
                           else _scalar(x) for x in item]
        else:
            result[key] = _scalar(item)
    return result


def _raw(value):
    raw = _allowed(value if value is not None else {}, _RAW)
    for key in ('uin', 'guildUid'):
        if key in raw:
            raw[key] = _identifier(raw[key], nullable=True)
    return raw


_DECIMAL = re.compile(r'-?\d+')


def _digits(value):
    """Lossless decimal identity: ints become decimal strings, decimal strings pass."""
    if type(value) is int:
        return str(_integer(value))
    if isinstance(value, str) and _DECIMAL.fullmatch(value):
        return value
    raise ValueError('Invalid decimal identity field')


def _raw_value(value):
    if isinstance(value, dict):
        return _raw(value)
    if isinstance(value, list):
        if len(value) > 512:
            raise ValueError('Evidence list too large')
        return [_raw(item) if isinstance(item, dict) else _scalar(item) for item in value]
    return _scalar(value)


def _formation(value):
    if not isinstance(value, dict) or len(value) > 256:
        raise ValueError('Invalid formation object')
    clean = {}
    for key, kind in FORMATION_KEYS.items():
        if key not in value:
            continue
        item = value[key]
        if item is None:
            clean[key] = None
        elif kind == 'int':
            clean[key] = _integer(item)
        elif kind == 'text':
            clean[key] = _text(item)
        elif kind == 'digits':
            clean[key] = _digits(item)
        else:
            clean[key] = _raw_value(item)
    return clean


def _rank_entry(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid ranking entry')  # noqa: TRY004 - unified season history validation contract  # noqa: TRY004 - unified season history validation contract
    row = {'entity_id': _identifier(value.get('entity_id'))}
    for key in ('rank', 'score', 'level'):
        if key in value:
            row[key] = None if value[key] is None else _integer(value[key])
    for key in ('display_name', 'guild_name'):
        if key in value:
            row[key] = None if value[key] is None else _text(value[key])
    if 'guild_uid' in value:
        row['guild_uid'] = _identifier(value['guild_uid'], nullable=True)
    row['raw_data'] = _raw(value.get('raw_data'))
    return row


def _member(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid member entry')  # noqa: TRY004 - unified season history validation contract  # noqa: TRY004 - unified season history validation contract
    row = {'member_uid': _identifier(value.get('member_uid'))}
    for key in ('score', 'level'):
        if key in value:
            row[key] = None if value[key] is None else _integer(value[key])
    if 'display_name' in value:
        row['display_name'] = None if value['display_name'] is None else _text(value['display_name'])
    row['raw_data'] = _raw(value.get('raw_data'))
    return row


def _payload(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
    else:
        raise ValueError('Raw payload must be bytes')  # noqa: TRY004 - unified season history validation contract  # noqa: TRY004 - unified season history validation contract
    # b'' is a legal empty business pool; only the size cap is enforced.
    if len(data) > MAX_RAW_BYTES:
        raise ValueError('Raw payload size out of bounds')
    return data


def _battle(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid battle object')  # noqa: TRY004 - unified season history validation contract  # noqa: TRY004 - unified season history validation contract
    row = {'battle_uid': _identifier(value.get('battle_uid'))}
    row['round'] = None if value.get('round') is None else _integer(value['round'])
    row['formation'] = _formation(value.get('formation') if value.get('formation') is not None else {})
    lineup = value.get('lineup') or []
    if not isinstance(lineup, list) or len(lineup) > MAX_LINEUP_ROWS:
        raise ValueError('Invalid battle lineup')
    clean_lineup = []
    for item in lineup:
        if not isinstance(item, dict):
            raise ValueError('Invalid lineup row')  # noqa: TRY004 - unified season history validation contract  # noqa: TRY004 - unified season history validation contract
        slot = dict(item)
        slot['member_uid'] = _identifier(item.get('member_uid'))
        if 'slot' in item:
            slot['slot'] = _integer(item['slot'])
        if 'power' in item:
            slot['power'] = None if item['power'] is None else _integer(item['power'])
        slot['raw_data'] = _raw(item.get('raw_data'))
        clean_lineup.append(slot)
    row['lineup'] = clean_lineup
    presence = value.get('presence') or []
    if not isinstance(presence, list) or len(presence) > MAX_LINEUP_ROWS:
        raise ValueError('Invalid battle presence')
    clean_presence = []
    for item in presence:
        if not isinstance(item, dict):
            raise ValueError('Invalid presence row')  # noqa: TRY004 - unified season history validation contract  # noqa: TRY004 - unified season history validation contract
        slot = {'member_uid': _identifier(item.get('member_uid'))}
        status = item.get('status')
        if not isinstance(status, str) or status not in PRESENCE_STATUS:
            raise ValueError('Invalid presence status')
        slot['status'] = status
        slot['raw_data'] = _raw(item.get('raw_data'))
        clean_presence.append(slot)
    row['presence'] = clean_presence
    row['raw_payload'] = None if value.get('raw_payload') is None else _payload(value['raw_payload'])
    return row


def _page(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid page object')  # noqa: TRY004 - unified season history validation contract  # noqa: TRY004 - unified season history validation contract
    entries = value.get('entries') or []
    if not isinstance(entries, list) or len(entries) > MAX_PAGE_ENTRIES:
        raise ValueError('Too many ranking entries')
    seen = set()
    clean_entries = []
    for item in entries:
        row = _rank_entry(item)
        if row['entity_id'] in seen:
            raise ValueError('Duplicate entity identity')
        seen.add(row['entity_id'])
        clean_entries.append(row)
    members = value.get('membership') or []
    if not isinstance(members, list) or len(members) > MAX_PAGE_MEMBERS:
        raise ValueError('Too many member entries')
    seen = set()
    clean_members = []
    for item in members:
        row = _member(item)
        if row['member_uid'] in seen:
            raise ValueError('Duplicate member identity')
        seen.add(row['member_uid'])
        clean_members.append(row)
    battles = value.get('battles') or []
    if not isinstance(battles, list) or len(battles) > MAX_PAGE_BATTLES:
        raise ValueError('Too many battles')
    seen = set()
    clean_battles = []
    for item in battles:
        row = _battle(item)
        if row['battle_uid'] in seen:
            raise ValueError('Duplicate battle identity')
        seen.add(row['battle_uid'])
        clean_battles.append(row)
    payloads = value.get('raw_payloads') or []
    if not isinstance(payloads, list) or len(payloads) > MAX_RAW_PER_PAGE:
        raise ValueError('Too many raw payloads')
    evidence = value.get('evidence')
    page = {'entries': clean_entries, 'membership': clean_members,
            'battles': clean_battles,
            'evidence': _allowed(evidence if evidence is not None else {}, _PAGE),
            'raw_payloads': [_payload(item) for item in payloads]}
    return page


def _labels(coverage, settle_evidence, availability):
    if availability not in AVAILABILITY:
        raise ValueError('Invalid availability label')
    if coverage not in COVERAGE:
        raise ValueError('Invalid coverage label')
    if settle_evidence not in SETTLE:
        raise ValueError('Invalid settle evidence label')
    return availability, coverage, settle_evidence


def _utcnow():
    return datetime.now(UTC).isoformat()


def compute_member_history(db, scope, mode, season_id):
    """Derive presence history from complete same-range snapshots only.

    PARTIAL/INCONSISTENT snapshots never mark a member as absent. ``db`` is any
    connection with this module's schema (runtime or packaged copy).
    """
    season = db.execute('SELECT season_key FROM seasons WHERE scope=? AND mode=? AND season_id=?',
                        (scope, mode, season_id)).fetchone()
    if season is None:
        raise KeyError((scope, mode, season_id))
    season_key = season[0]
    placeholders = ','.join('?' for _ in COMPLETE_COVERAGES)
    snapshots = db.execute(
        'SELECT snapshot_id, collected_at_utc FROM snapshots WHERE season_key=? '
        'AND coverage IN (' + placeholders + ') ORDER BY snapshot_id',
        (season_key, *COMPLETE_COVERAGES)).fetchall()
    sequence = []
    for snapshot_id, collected_at in snapshots:
        rows = db.execute(
            'SELECT member_uid, observed_at_utc FROM snapshot_membership WHERE snapshot_id=? ORDER BY ordinal',
            (snapshot_id,)).fetchall()
        observed = {row[0]: row[1] for row in rows}
        fallback = max((row[1] for row in rows), default=collected_at)
        sequence.append((snapshot_id, observed, fallback))
    history = []
    for uid in sorted(set().union(*(members for _, members, _ in sequence)) if sequence else set()):
        first = last = None
        previous = None
        events = []
        for snapshot_id, observed, fallback in sequence:
            current = uid in observed
            at_utc = observed.get(uid) or fallback
            if current:
                if first is None:
                    first = (snapshot_id, at_utc)
                last = (snapshot_id, at_utc)
            if previous is True and not current:
                kind = 'FIRST_ABSENCE' if not any(
                    event['type'] in ('FIRST_ABSENCE', 'ABSENT') for event in events) else 'ABSENT'
                events.append({'type': kind, 'snapshot_id': snapshot_id, 'at_utc': fallback})
            elif previous is False and current and first is not None and first[0] != snapshot_id:
                events.append({'type': 'REJOIN', 'snapshot_id': snapshot_id, 'at_utc': at_utc})
            previous = current
        history.append({'member_uid': uid,
                        'first_seen_snapshot_id': first[0], 'first_seen_at_utc': first[1],
                        'last_seen_snapshot_id': last[0], 'last_seen_at_utc': last[1],
                        'events': events})
    return history


class SeasonHistory:
    """Open a dedicated ``seasonal_leaderboards.sqlite3``, initializing schema version 1."""

    def __init__(self, path):
        if str(path) == ':memory:':
            raise ValueError('Season history requires a persistent file path')
        self.path = Path(path)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise ValueError('Database is not an empty season history')
                for statement in _SCHEMA:
                    db.execute(statement)
                db.execute('PRAGMA user_version=1')
            elif version != SCHEMA_VERSION:
                raise ValueError('Unsupported season history schema')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    # -- run lifecycle ------------------------------------------------------

    @staticmethod
    def _season_key(db, scope, mode, season_id):
        db.execute('INSERT OR IGNORE INTO seasons(scope,mode,season_id,created_at_utc) VALUES (?,?,?,?)',
                   (scope, mode, season_id, _utcnow()))
        row = db.execute('SELECT season_key, sealed_at_utc FROM seasons WHERE scope=? AND mode=? AND season_id=?',
                         (scope, mode, season_id)).fetchone()
        if row['sealed_at_utc'] is not None:
            raise ValueError('Season is sealed')
        return row['season_key']

    def _begin_run(self, db, scope, mode, season_id, *, trigger, code_version,
                   run_id, collected_at_utc, metadata):
        season_key = self._season_key(db, scope, mode, season_id)
        last = db.execute('SELECT run_id, status, checkpoint_page, snapshot_id FROM runs '
                          'WHERE season_key=? ORDER BY rowid DESC LIMIT 1', (season_key,)).fetchone()
        checkpoint = None
        snapshot_id = None
        checkpoint_page = 0
        if last is not None and last['status'] == 'INTERRUPTED':
            checkpoint_page = last['checkpoint_page']
            snapshot_id = last['snapshot_id']
            checkpoint = {'resume_from': last['run_id'], 'next_page': checkpoint_page}
        db.execute('INSERT INTO runs(run_id,season_key,trigger,code_version,status,checkpoint_page,'
                   'checkpoint_payload,snapshot_id,metadata,collected_at_utc,started_at_utc) '
                   'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (run_id, season_key, trigger, code_version, 'RUNNING', checkpoint_page,
                    _json(checkpoint) if checkpoint else None, snapshot_id,
                    _json(metadata), collected_at_utc, _utcnow()))
        return run_id

    def _run(self, db, run_id):
        row = db.execute('SELECT runs.*, seasons.scope, seasons.mode, seasons.season_id, '
                         'seasons.sealed_at_utc AS season_sealed FROM runs '
                         'JOIN seasons USING(season_key) WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row

    @staticmethod
    def _check_unlocked(run):
        if run['season_sealed'] is not None:
            raise ValueError('Season is sealed')

    @staticmethod
    def _append_page(db, run, page, page_index):
        snapshot_id = run['snapshot_id']
        if snapshot_id is None:
            snapshot_id = db.execute(
                'INSERT INTO snapshots(run_id,season_key,trigger,availability,coverage,'
                'settle_evidence,collected_at_utc,metadata) VALUES (?,?,?,?,?,?,?,?)',
                (run['run_id'], run['season_key'], run['trigger'], 'UNKNOWN', 'PARTIAL',
                 'LAST_OBSERVED', run['collected_at_utc'], run['metadata'])).lastrowid
            db.execute('UPDATE runs SET snapshot_id=? WHERE run_id=?', (snapshot_id, run['run_id']))
        season_key = run['season_key']
        now = _utcnow()

        def count(table):
            return db.execute(f'SELECT count(*) FROM "{table}" WHERE snapshot_id=?',
                              (snapshot_id,)).fetchone()[0]

        ordinal = count('rank_entries')
        for item in page['entries']:
            db.execute('INSERT OR IGNORE INTO rank_entries VALUES (?,?,?,?,?,?,?,?)',
                       (snapshot_id, ordinal, item['entity_id'], item.get('rank'), item.get('score'),
                        item.get('display_name'), item.get('guild_name'), _json(item)))
            ordinal += 1
        ordinal = count('snapshot_membership')
        for item in page['membership']:
            db.execute('INSERT OR IGNORE INTO snapshot_membership VALUES (?,?,?,?,?)',
                       (snapshot_id, ordinal, item['member_uid'], _json(item),
                        run['collected_at_utc']))
            ordinal += 1
        if page['evidence']:
            db.execute('INSERT INTO page_evidence VALUES (?,?,?)',
                       (snapshot_id, page_index, _json(page['evidence'])))

        blob_ordinal = count('snapshot_blobs')
        linked = set()

        def add_blob(data):
            nonlocal blob_ordinal
            sha = hashlib.sha256(data).hexdigest()
            db.execute('INSERT OR IGNORE INTO raw_blobs VALUES (?,?,?,?)',
                       (sha, len(data), data, now))
            if sha not in linked:
                linked.add(sha)
                db.execute('INSERT INTO snapshot_blobs VALUES (?,?,?)',
                           (snapshot_id, sha, blob_ordinal))
                blob_ordinal += 1
            return sha

        for data in page['raw_payloads']:
            add_blob(data)
        link_ordinal = count('snapshot_battles')
        for item in page['battles']:
            sha = add_blob(item['raw_payload']) if item['raw_payload'] is not None else None
            db.execute('INSERT OR IGNORE INTO battles VALUES (?,?,?,?,?)',
                       (item['battle_uid'], season_key, snapshot_id, sha, now))
            db.execute('INSERT INTO snapshot_battles VALUES (?,?,?,?)',
                       (snapshot_id, item['battle_uid'], season_key, link_ordinal))
            link_ordinal += 1
            db.execute('INSERT OR IGNORE INTO guild_formations VALUES (?,?,?,?)',
                       (item['battle_uid'], season_key, item['round'], _json(item['formation'])))
            for slot_ordinal, slot in enumerate(item['lineup']):
                db.execute('INSERT OR IGNORE INTO lineup VALUES (?,?,?,?,?)',
                           (item['battle_uid'], season_key, slot_ordinal,
                            slot['member_uid'], _json(slot)))
            for present in item['presence']:
                db.execute('INSERT OR IGNORE INTO presence VALUES (?,?,?,?,?)',
                           (item['battle_uid'], season_key, present['member_uid'],
                            present['status'], _json({'raw_data': present['raw_data']})))
        db.execute('UPDATE runs SET pages_count=pages_count+1, checkpoint_page=? WHERE run_id=?',
                   (page_index + 1, run['run_id']))
        return snapshot_id

    def begin_run(self, scope, mode, season_id, *, trigger, code_version='unknown',
                  run_id=None, collected_at_utc=None, metadata=None):
        """Start a RUNNING run; resumes the checkpoint of the last INTERRUPTED run."""
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        if trigger not in TRIGGER_SET:
            raise ValueError('Invalid trigger label')
        run_id = _text(run_id, limit=256, nonempty=True) if run_id is not None else uuid.uuid4().hex
        collected_at_utc = _text(collected_at_utc, limit=128, nonempty=True) if collected_at_utc else _utcnow()
        metadata = _allowed(metadata if metadata is not None else {}, _META)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._begin_run(db, scope, mode, season_id, trigger=trigger,
                            code_version=_text(code_version, limit=256, nonempty=True),
                            run_id=run_id, collected_at_utc=collected_at_utc, metadata=metadata)
        return run_id

    def append_page(self, run_id, *, page_index=None, entries=(), membership=(),
                    battles=(), evidence=None, raw_payloads=()):
        """Append one checked page; the run checkpoint advances transactionally."""
        page = _page({'entries': list(entries), 'membership': list(membership),
                      'battles': list(battles),
                      'evidence': evidence if evidence is not None else {},
                      'raw_payloads': list(raw_payloads)})
        run_id = _text(run_id, limit=256, nonempty=True)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._run(db, run_id)
            if run['status'] != 'RUNNING':
                raise ValueError('Run is not open for pages')
            self._check_unlocked(run)
            expected = run['checkpoint_page']
            if page_index is None:
                page_index = expected
            page_index = _integer(page_index)
            if page_index != expected:
                raise ValueError('Page index does not match the run checkpoint')
            snapshot_id = self._append_page(db, run, page, page_index)
            return {'run_id': run_id, 'snapshot_id': snapshot_id,
                    'page_index': page_index, 'next_page': page_index + 1}

    def _finish(self, db, run_id, *, coverage, settle_evidence, availability, status):
        availability, coverage, settle_evidence = _labels(coverage, settle_evidence, availability)
        run = self._run(db, run_id)
        if run['status'] != 'RUNNING':
            raise ValueError('Run is not running')
        self._check_unlocked(run)
        db.execute('UPDATE runs SET status=?, finished_at_utc=? WHERE run_id=?',
                   (status, _utcnow(), run_id))
        if run['snapshot_id'] is not None:
            db.execute('UPDATE snapshots SET availability=?, coverage=?, settle_evidence=? '
                       'WHERE snapshot_id=?', (availability, coverage, settle_evidence,
                                               run['snapshot_id']))

    def finish_run(self, run_id, *, coverage, settle_evidence, availability):
        """Close a run with final coverage/settle labels; coverage and settle stay separate."""
        run_id = _text(run_id, limit=256, nonempty=True)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._finish(db, run_id, coverage=coverage, settle_evidence=settle_evidence,
                         availability=availability, status='COMPLETE')
        return self.run_state(run_id)

    def interrupt_run(self, run_id, reason):
        """Mark an interrupted run; its snapshot can never claim complete coverage."""
        run_id = _text(run_id, limit=256, nonempty=True)
        reason = _text(reason, limit=128, nonempty=True)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._run(db, run_id)
            if run['status'] != 'RUNNING':
                raise ValueError('Run is not running')
            self._check_unlocked(run)
            db.execute('UPDATE runs SET status=?, interrupt_reason=?, finished_at_utc=? WHERE run_id=?',
                       ('INTERRUPTED', reason, _utcnow(), run_id))
            if run['snapshot_id'] is not None:
                db.execute("UPDATE snapshots SET coverage='PARTIAL' WHERE snapshot_id=?",
                           (run['snapshot_id'],))
        return self.run_state(run_id)

    def run_state(self, run_id):
        run_id = _text(run_id, limit=256, nonempty=True)
        with self._connection() as db:
            run = self._run(db, run_id)
            checkpoint = json.loads(run['checkpoint_payload']) if run['checkpoint_payload'] else None
            return {'run_id': run_id, 'status': run['status'], 'trigger': run['trigger'],
                    'scope': run['scope'], 'mode': run['mode'], 'season_id': run['season_id'],
                    'snapshot_id': run['snapshot_id'], 'pages_count': run['pages_count'],
                    'next_page': run['checkpoint_page'], 'checkpoint': checkpoint,
                    'interrupt_reason': run['interrupt_reason'],
                    'started_at_utc': run['started_at_utc'], 'finished_at_utc': run['finished_at_utc']}

    def last_run_state(self, scope, mode, season_id):
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        with self._connection() as db:
            row = db.execute('SELECT run_id FROM runs JOIN seasons USING(season_key) '
                             'WHERE scope=? AND mode=? AND season_id=? ORDER BY runs.rowid DESC LIMIT 1',
                             (scope, mode, season_id)).fetchone()
        return self.run_state(row['run_id']) if row else None

    def save_run(self, run_id=None, *, scope, mode, season_id, trigger, code_version='unknown',
                 coverage, settle_evidence, availability, collected_at_utc=None,
                 pages=(), metadata=None):
        """Atomically save a whole run; identical content under the same run_id is idempotent."""
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        if trigger not in TRIGGER_SET:
            raise ValueError('Invalid trigger label')
        _labels(coverage, settle_evidence, availability)
        run_id = _text(run_id, limit=256, nonempty=True) if run_id is not None else uuid.uuid4().hex
        collected_at_utc = _text(collected_at_utc, limit=128, nonempty=True) if collected_at_utc else _utcnow()
        metadata = _allowed(metadata if metadata is not None else {}, _META)
        if not isinstance(pages, (list, tuple)) or len(pages) > 512:
            raise ValueError('Invalid page list')
        pages = [_page(page) for page in pages]
        stable_pages = [dict(page, raw_payloads=[hashlib.sha256(data).hexdigest() for data in page['raw_payloads']],
                             battles=[dict(battle, raw_payload=hashlib.sha256(battle['raw_payload']).hexdigest()
                                         if battle['raw_payload'] is not None else None)
                                      for battle in page['battles']]) for page in pages]
        fingerprint = hashlib.sha256(_json({
            'scope': scope, 'mode': mode, 'season_id': season_id, 'trigger': trigger,
            'coverage': coverage, 'settle_evidence': settle_evidence,
            'availability': availability, 'collected_at_utc': collected_at_utc,
            'metadata': metadata, 'pages': stable_pages}).encode('utf-8')).hexdigest()
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT content_hash, snapshot_id, pages_count FROM runs WHERE run_id=?',
                             (run_id,)).fetchone()
            if old is not None:
                if old['content_hash'] != fingerprint:
                    raise ValueError('Run identity conflicts with saved content')
                return {'run_id': run_id, 'snapshot_id': old['snapshot_id'],
                        'pages': old['pages_count']}
            self._begin_run(db, scope, mode, season_id, trigger=trigger,
                            code_version=_text(code_version, limit=256, nonempty=True),
                            run_id=run_id, collected_at_utc=collected_at_utc, metadata=metadata)
            db.execute('UPDATE runs SET content_hash=? WHERE run_id=?', (fingerprint, run_id))
            snapshot_id = None
            for page in pages:
                run = self._run(db, run_id)
                snapshot_id = self._append_page(db, run, page, run['checkpoint_page'])
            self._finish(db, run_id, coverage=coverage, settle_evidence=settle_evidence,
                         availability=availability, status='COMPLETE')
            return {'run_id': run_id, 'snapshot_id': snapshot_id, 'pages': len(pages)}

    # -- reads ---------------------------------------------------------------

    def export_snapshot(self, snapshot_id):
        snapshot_id = _integer(snapshot_id)
        with self._connection() as db:
            row = db.execute('SELECT snapshots.*, runs.run_id, seasons.scope, seasons.mode, '
                             'seasons.season_id FROM snapshots JOIN seasons USING(season_key) '
                             'JOIN runs USING(run_id) WHERE snapshots.snapshot_id=?', (snapshot_id,)).fetchone()
            if row is None:
                raise KeyError(snapshot_id)
            result = {'snapshot_id': snapshot_id, 'run_id': row['run_id'],
                      'scope': row['scope'], 'mode': row['mode'], 'season_id': row['season_id'],
                      'trigger': row['trigger'], 'availability': row['availability'],
                      'coverage': row['coverage'], 'settle_evidence': row['settle_evidence'],
                      'collected_at_utc': row['collected_at_utc'],
                      'metadata': json.loads(row['metadata'])}
            result['entries'] = [json.loads(item[0]) for item in db.execute(
                'SELECT payload FROM rank_entries WHERE snapshot_id=? ORDER BY ordinal', (snapshot_id,))]
            result['membership'] = [json.loads(item[0]) for item in db.execute(
                'SELECT payload FROM snapshot_membership WHERE snapshot_id=? ORDER BY ordinal', (snapshot_id,))]
            result['page_evidence'] = [json.loads(item[0]) for item in db.execute(
                'SELECT payload FROM page_evidence WHERE snapshot_id=? ORDER BY ordinal', (snapshot_id,))]
            battles = []
            for battle in db.execute(
                    'SELECT battles.battle_uid, battles.first_snapshot_id, battles.raw_sha256, '
                    'guild_formations.battle_round, guild_formations.payload '
                    'FROM snapshot_battles JOIN battles ON snapshot_battles.battle_uid=battles.battle_uid '
                    'AND snapshot_battles.season_key=battles.season_key '
                    'JOIN guild_formations ON guild_formations.battle_uid=battles.battle_uid '
                    'AND guild_formations.season_key=battles.season_key '
                    'WHERE snapshot_battles.snapshot_id=? ORDER BY snapshot_battles.ordinal', (snapshot_id,)):
                item = {'battle_uid': battle['battle_uid'],
                        'first_snapshot_id': battle['first_snapshot_id'],
                        'raw_sha256': battle['raw_sha256'],
                        'round': battle['battle_round'],
                        'formation': json.loads(battle['payload'])}
                item['lineup'] = [json.loads(slot[0]) for slot in db.execute(
                    'SELECT payload FROM lineup WHERE battle_uid=? AND season_key=? ORDER BY ordinal',
                    (battle['battle_uid'], row['season_key']))]
                item['presence'] = [dict(json.loads(slot[0]), status=slot[1]) for slot in db.execute(
                    'SELECT payload, status FROM presence WHERE battle_uid=? AND season_key=? ORDER BY member_uid',
                    (battle['battle_uid'], row['season_key']))]
                battles.append(item)
            result['battles'] = battles
            return result

    def snapshots(self, scope=None, mode=None, season_id=None):
        clauses, args = [], []
        for key, value in (('scope', scope), ('mode', mode), ('season_id', season_id)):
            if value is not None:
                clauses.append('seasons.' + key + '=?')
                args.append(_identifier(value) if key != 'season_id' else _season_id(value))
        sql = ('SELECT snapshots.snapshot_id, snapshots.run_id, seasons.scope, seasons.mode, '
               'seasons.season_id, snapshots.trigger, snapshots.availability, snapshots.coverage, '
               'snapshots.settle_evidence, snapshots.collected_at_utc, '
               '(SELECT count(*) FROM rank_entries e WHERE e.snapshot_id=snapshots.snapshot_id) AS entries_count, '
               '(SELECT count(*) FROM snapshot_membership m WHERE m.snapshot_id=snapshots.snapshot_id) AS membership_count, '
               '(SELECT count(*) FROM snapshot_battles b WHERE b.snapshot_id=snapshots.snapshot_id) AS battles_count '
               'FROM snapshots JOIN seasons USING(season_key)')
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        with self._connection() as db:
            return [dict(row) for row in db.execute(sql + ' ORDER BY snapshots.snapshot_id', args)]

    def list_seasons(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                'SELECT seasons.scope, seasons.mode, seasons.season_id, seasons.created_at_utc, '
                'seasons.sealed_at_utc, count(snapshots.snapshot_id) AS snapshot_count '
                'FROM seasons LEFT JOIN snapshots USING(season_key) '
                'GROUP BY seasons.season_key ORDER BY seasons.scope, seasons.mode, seasons.season_id')]

    def list_battles(self, scope, mode, season_id):
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        with self._connection() as db:
            season = db.execute('SELECT season_key FROM seasons WHERE scope=? AND mode=? AND season_id=?',
                                (scope, mode, season_id)).fetchone()
            if season is None:
                raise KeyError((scope, mode, season_id))
            return [dict(row) for row in db.execute(
                'SELECT battle_uid, first_snapshot_id, raw_sha256 FROM battles '
                'WHERE season_key=? ORDER BY battle_uid', (season['season_key'],))]

    def member_history(self, scope, mode, season_id):
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        with self._connection() as db:
            return compute_member_history(db, scope, mode, season_id)

    # -- schedule slots -------------------------------------------------------

    def _slot(self, scope, mode, season_id, slot, fire_at_utc, status, run_id):
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        if slot not in TRIGGER_SET:
            raise ValueError('Invalid schedule slot')
        fire_at_utc = _text(fire_at_utc, limit=128, nonempty=True)
        run_id = _identifier(run_id, nullable=True)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            season_key = self._season_key(db, scope, mode, season_id)
            db.execute('INSERT INTO schedule_slots(season_key,slot,fire_at_utc,status,run_id,updated_at_utc) '
                       'VALUES (?,?,?,?,?,?) ON CONFLICT(season_key,slot) DO UPDATE SET '
                       'fire_at_utc=excluded.fire_at_utc, status=excluded.status, '
                       'run_id=excluded.run_id, updated_at_utc=excluded.updated_at_utc',
                       (season_key, slot, fire_at_utc, status, run_id, _utcnow()))
            return {'slot': slot, 'fire_at_utc': fire_at_utc, 'status': status, 'run_id': run_id}

    def plan_schedule_slot(self, scope, mode, season_id, slot, fire_at_utc):
        return self._slot(scope, mode, season_id, slot, fire_at_utc, 'SCHEDULED', None)

    def fire_schedule_slot(self, scope, mode, season_id, slot, *, run_id=None, status='FIRED'):
        if status not in ('FIRED', 'SKIPPED', 'UNAVAILABLE'):
            raise ValueError('Invalid slot status')
        return self._slot(scope, mode, season_id, slot, _utcnow(), status, run_id)

    def schedule_slots(self, scope, mode, season_id):
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        with self._connection() as db:
            season = db.execute('SELECT season_key FROM seasons WHERE scope=? AND mode=? AND season_id=?',
                                (scope, mode, season_id)).fetchone()
            if season is None:
                raise KeyError((scope, mode, season_id))
            return [dict(row) for row in db.execute(
                'SELECT slot, fire_at_utc, status, run_id, updated_at_utc FROM schedule_slots '
                'WHERE season_key=? ORDER BY slot', (season['season_key'],))]

    # -- sealing support --------------------------------------------------------

    def season_status(self, scope, mode, season_id):
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        with self._connection() as db:
            row = db.execute('SELECT sealed_at_utc FROM seasons WHERE scope=? AND mode=? AND season_id=?',
                             (scope, mode, season_id)).fetchone()
        if row is None:
            return None
        return {'sealed': row['sealed_at_utc'] is not None}

    def mark_sealed(self, scope, mode, season_id):
        """Set the seal write-lock; returns an ownership token for unseal_season."""
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        token = f'{_utcnow()}#{uuid.uuid4().hex}'
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            cursor = db.execute('UPDATE seasons SET sealed_at_utc=? WHERE scope=? AND mode=? AND season_id=?',
                                (token, scope, mode, season_id))
            if cursor.rowcount == 0:
                raise KeyError((scope, mode, season_id))
        return token

    def unseal_season(self, scope, mode, season_id, token):
        """Release the write-lock, but only when ``token`` is the current mark."""
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        token = _text(token, limit=256, nonempty=True)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            cursor = db.execute('UPDATE seasons SET sealed_at_utc=NULL WHERE scope=? AND mode=? '
                                'AND season_id=? AND sealed_at_utc=?',
                                (scope, mode, season_id, token))
            if cursor.rowcount:
                return True
            present = db.execute('SELECT 1 FROM seasons WHERE scope=? AND mode=? AND season_id=?',
                                 (scope, mode, season_id)).fetchone()
            if present is None:
                raise KeyError((scope, mode, season_id))
            return False

    def season_counts(self, scope, mode, season_id):
        """Per-table row counts scoped to one season (raw_blobs = referenced payloads)."""
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        with self._connection() as db:
            row = db.execute('SELECT season_key FROM seasons WHERE scope=? AND mode=? AND season_id=?',
                             (scope, mode, season_id)).fetchone()
            if row is None:
                return {table: 0 for table in COUNTED_TABLES}
            season_key = row['season_key']
            sids = [r[0] for r in db.execute('SELECT snapshot_id FROM snapshots WHERE season_key=?',
                                            (season_key,))]
            counts = {}
            in_clause = f"({','.join('?' for _ in sids)})" if sids else '(NULL)'
            counts['rank_entries'] = db.execute(
                'SELECT count(*) FROM rank_entries WHERE snapshot_id IN ' + in_clause, sids).fetchone()[0]
            counts['snapshot_membership'] = db.execute(
                'SELECT count(*) FROM snapshot_membership WHERE snapshot_id IN ' + in_clause, sids).fetchone()[0]
            counts['page_evidence'] = db.execute(
                'SELECT count(*) FROM page_evidence WHERE snapshot_id IN ' + in_clause, sids).fetchone()[0]
            counts['snapshot_blobs'] = db.execute(
                'SELECT count(*) FROM snapshot_blobs WHERE snapshot_id IN ' + in_clause, sids).fetchone()[0]
            counts['snapshot_battles'] = db.execute(
                'SELECT count(*) FROM snapshot_battles WHERE snapshot_id IN ' + in_clause, sids).fetchone()[0]
            for table in ('runs', 'snapshots', 'battles', 'guild_formations', 'lineup',
                          'presence', 'schedule_slots'):
                counts[table] = db.execute(f'SELECT count(*) FROM "{table}" WHERE season_key=?',
                                           (season_key,)).fetchone()[0]
            counts['raw_blobs'] = db.execute(
                'SELECT count(*) FROM raw_blobs WHERE sha256 IN '
                '(SELECT sha256 FROM snapshot_blobs WHERE snapshot_id IN ' + in_clause + ') '
                'OR sha256 IN (SELECT raw_sha256 FROM battles WHERE season_key=? AND raw_sha256 IS NOT NULL)',
                (*sids, season_key)).fetchone()[0]
            return counts

    def season_blob_stats(self, scope, mode, season_id):
        """COUNT/SUM aggregates over the season's referenced raw payloads.

        Lets the packager estimate size and check headroom without loading any
        payload bytes into memory.
        """
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        with self._connection() as db:
            season = db.execute('SELECT season_key FROM seasons WHERE scope=? AND mode=? AND season_id=?',
                                (scope, mode, season_id)).fetchone()
            if season is None:
                raise KeyError((scope, mode, season_id))
            season_key = season['season_key']
            sids = [r[0] for r in db.execute('SELECT snapshot_id FROM snapshots WHERE season_key=?',
                                            (season_key,))]
            in_clause = f"({','.join('?' for _ in sids)})" if sids else '(NULL)'
            count, total = db.execute(
                'SELECT count(*), coalesce(sum(byte_length), 0) FROM raw_blobs WHERE sha256 IN '
                '(SELECT sha256 FROM snapshot_blobs WHERE snapshot_id IN ' + in_clause + ') '
                'OR sha256 IN (SELECT raw_sha256 FROM battles WHERE season_key=? '
                'AND raw_sha256 IS NOT NULL)', (*sids, season_key)).fetchone()
            return {'count': count, 'bytes': total}

    def iter_season_blobs(self, scope, mode, season_id):
        """Yield (sha256, payload) for the season's referenced raw payloads, one at a time.

        Ordered by digest so packaged manifests stay deterministic; only the
        currently yielded payload is held in memory.
        """
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            season = db.execute('SELECT season_key FROM seasons WHERE scope=? AND mode=? AND season_id=?',
                                (scope, mode, season_id)).fetchone()
            if season is None:
                raise KeyError((scope, mode, season_id))
            season_key = season[0]
            sids = [r[0] for r in db.execute('SELECT snapshot_id FROM snapshots WHERE season_key=?',
                                             (season_key,))]
            in_clause = f"({','.join('?' for _ in sids)})" if sids else '(NULL)'
            cursor = db.execute(
                'SELECT sha256, payload FROM raw_blobs WHERE sha256 IN '
                '(SELECT sha256 FROM snapshot_blobs WHERE snapshot_id IN ' + in_clause + ') '
                'OR sha256 IN (SELECT raw_sha256 FROM battles WHERE season_key=? '
                'AND raw_sha256 IS NOT NULL) ORDER BY sha256', (*sids, season_key))
            for sha, payload in cursor:
                yield sha, bytes(payload)
        finally:
            db.close()

    def _reclaim_space(self):
        """Run VACUUM outside any transaction so the file shrinks after a purge.

        Returns False instead of raising: callers must treat a vacuum failure as
        "rows are already gone, only the free space remains unreclaimed".
        """
        try:
            db = sqlite3.connect(self.path, timeout=10)
            try:
                db.execute('VACUUM')
            finally:
                db.close()
        except sqlite3.Error:
            return False
        return True

    def purge_season(self, scope, mode, season_id):
        """Delete exactly one season's runtime rows; requires the seal write-lock.

        The sealed_at_utc mark must be present (set by seal_season or
        mark_sealed); blobs shared with other seasons survive. After the delete
        transaction commits, VACUUM reclaims the file space. The result carries
        a ``vacuumed`` flag so a vacuum failure is distinguishable from a failed
        purge: rows are deleted either way, but unreclaimed space is reported.
        """
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            season = db.execute('SELECT season_key, sealed_at_utc FROM seasons WHERE scope=? AND mode=? AND season_id=?',
                                (scope, mode, season_id)).fetchone()
            if season is None:
                raise KeyError((scope, mode, season_id))
            if season['sealed_at_utc'] is None:
                raise ValueError('Season is not sealed; refusing to purge')
            season_key = season['season_key']
            sids = [r[0] for r in db.execute('SELECT snapshot_id FROM snapshots WHERE season_key=?',
                                            (season_key,))]
            refs = {r[0] for r in db.execute(
                f"SELECT sha256 FROM snapshot_blobs WHERE snapshot_id IN ({','.join('?' for _ in sids) or 'NULL'})", sids)}
            refs |= {r[0] for r in db.execute(
                'SELECT raw_sha256 FROM battles WHERE season_key=? AND raw_sha256 IS NOT NULL',
                (season_key,))}
            in_clause = f"({','.join('?' for _ in sids)})" if sids else '(NULL)'
            deleted = {}
            for table in ('rank_entries', 'snapshot_membership', 'page_evidence',
                          'snapshot_blobs', 'snapshot_battles'):
                deleted[table] = db.execute(f'DELETE FROM "{table}" WHERE snapshot_id IN {in_clause}',
                                            sids).rowcount
            for table in ('lineup', 'presence', 'guild_formations', 'battles', 'snapshots',
                          'runs', 'schedule_slots', 'seasons'):
                deleted[table] = db.execute(f'DELETE FROM "{table}" WHERE season_key=?',
                                            (season_key,)).rowcount
            if refs:
                placeholders = ','.join('?' for _ in refs)
                deleted['raw_blobs'] = db.execute(
                    f'DELETE FROM raw_blobs WHERE sha256 IN ({placeholders}) AND sha256 NOT IN '
                    '(SELECT sha256 FROM snapshot_blobs) AND sha256 NOT IN '
                    '(SELECT raw_sha256 FROM battles WHERE raw_sha256 IS NOT NULL)',
                    tuple(refs)).rowcount
            else:
                deleted['raw_blobs'] = 0
        result = {table: deleted[table] for table in COUNTED_TABLES}
        result['vacuumed'] = self._reclaim_space()
        return result

    def export_season(self, scope, mode, season_id, target):
        """Write a self-contained SQLite copy of exactly one season to ``target``."""
        scope = _identifier(scope)
        mode = _identifier(mode)
        season_id = _season_id(season_id)
        target = Path(target)
        if target.exists():
            raise ValueError('Refusing to overwrite an existing file')
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE')
            season = db.execute('SELECT season_key FROM seasons WHERE scope=? AND mode=? AND season_id=?',
                                (scope, mode, season_id)).fetchone()
            if season is None:
                raise KeyError((scope, mode, season_id))
            season_key = season[0]
            db.execute('ATTACH DATABASE ? AS pkg', (str(target),))
            try:
                for statement in _SCHEMA:
                    db.execute(statement.replace('CREATE TABLE ', 'CREATE TABLE pkg.', 1))
                db.execute(f'PRAGMA pkg.user_version={SCHEMA_VERSION}')
                db.execute('INSERT INTO pkg.seasons SELECT * FROM main.seasons '
                           'WHERE scope=? AND mode=? AND season_id=?', (scope, mode, season_id))
                db.execute('INSERT INTO pkg.runs SELECT * FROM main.runs WHERE season_key=?', (season_key,))
                sids = [r[0] for r in db.execute('SELECT snapshot_id FROM main.snapshots WHERE season_key=?',
                                                 (season_key,))]
                in_clause = f"({','.join('?' for _ in sids)})" if sids else '(NULL)'
                db.execute('INSERT INTO pkg.raw_blobs(sha256,byte_length,payload,first_seen_utc) '
                           'SELECT sha256, byte_length, NULL, first_seen_utc FROM main.raw_blobs WHERE sha256 IN '
                           '(SELECT sha256 FROM main.snapshot_blobs WHERE snapshot_id IN ' + in_clause + ') '
                           'OR sha256 IN (SELECT raw_sha256 FROM main.battles WHERE season_key=? '
                           'AND raw_sha256 IS NOT NULL)', (*sids, season_key))
                db.execute('INSERT INTO pkg.snapshots SELECT * FROM main.snapshots WHERE season_key=?',
                           (season_key,))
                for table in ('rank_entries', 'snapshot_membership', 'page_evidence', 'snapshot_blobs'):
                    db.execute(f'INSERT INTO pkg."{table}" SELECT * FROM main."{table}" WHERE snapshot_id IN {in_clause}', sids)
                for table in ('battles', 'snapshot_battles', 'guild_formations', 'lineup',
                              'presence', 'schedule_slots'):
                    db.execute(f'INSERT INTO pkg."{table}" SELECT * FROM main."{table}" WHERE season_key=?', (season_key,))
                db.commit()
            finally:
                try:
                    db.execute('DETACH DATABASE pkg')
                except sqlite3.OperationalError:
                    pass
            db.commit()
        except Exception:
            db.rollback()
            if target.exists():
                target.unlink()
            raise
        finally:
            db.close()
