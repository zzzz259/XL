"""Independent, append-only SQLite archive for service-visible ranking evidence.

Only explicitly allowed collector fields are persisted. This module has no login,
network, or gacha archive dependency. Public methods return JSON-compatible values.
"""

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
MAX_RESULTS = 32
MAX_ENTRIES = 1000
MAX_PAGES = 128
MAX_JSON_BYTES = 4 * 1024 * 1024
_META = frozenset(('seasonId', 'season', 'phase', 'state', 'startStamp', 'endStamp',
                   'nextStartStamp', 'settle', 'inGuild', 'isStart', 'fighterCount',
                   'challengeCount', 'highScore', 'curChapter', 'curStage', 'bossHpMax',
                   'bossHpCur', 'rank', 'rankRatio', 'highScoreDiff', 'rewardStages', 'schedule',
                   'time_basis', 'member_scope', 'recheck', 'first_page_changed',
                   'season_changed', 'total_changed', 'collection_started_at_utc',
                   'collection_finished_at_utc'))
_RAW = frozenset(('guildUid', 'uin', 'level', 'iconId', 'name', 'rank', 'score',
                  'memberCount', 'actCount', 'round', 'faceId', 'guildName',
                  'scoreDiff', 'damage'))
_PAGE = frozenset(('request_index', 'echoed_index', 'count', 'source_hash',
                   'request_cursor', 'echoed_cursor', 'reported_total', 'total',
                   'first_rank', 'last_rank', 'source_rpc', 'hash', 'rpc',
                   'entry_count', 'member_count', 'season_id', 'recheck', 'is_recheck'))
_AVAILABILITY = {'NOT_OPEN', 'NOTICE', 'ACTIVE', 'SETTLING', 'SETTLED_CANDIDATE',
                 'CLOSED', 'UNKNOWN', 'EMPTY'}
_COVERAGE = {'NO_RANKING', 'PARTIAL', 'INCONSISTENT', 'SERVER_VISIBLE_SLICE',
             'COMPLETE_REPORTED_RANGE'}
_SETTLE = {'LIVE', 'LAST_OBSERVED', 'SETTLED_CANDIDATE'}


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
        raise ValueError('Invalid identifier')  # noqa: TRY004 - unified archive validation contract
    return _text(str(value), limit=256, nonempty=True)


def _integer(value):
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise ValueError('Integer outside signed int64')
    return value


def _json(value):
    result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(result.encode('utf-8')) > MAX_JSON_BYTES:
        raise ValueError('Archive payload too large')
    return result


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
            if len(item) > MAX_PAGES:
                raise ValueError('Evidence list too large')
            result[key] = [_allowed(x, keys, depth + 1) if isinstance(x, dict)
                           else _scalar(x) for x in item]
        else:
            result[key] = _scalar(item)
    return result


def _scalar(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return _integer(value)
    return _text(value)


def _entries(values):
    if not isinstance(values, list) or len(values) > MAX_ENTRIES:
        raise ValueError('Too many ranking entries')
    output, seen = [], set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError('Invalid ranking entry')  # noqa: TRY004 - unified archive validation contract
        entity = _identifier(value.get('entity_id'))
        if entity in seen:
            raise ValueError('Duplicate entity identity')
        seen.add(entity)
        row = {'entity_id': entity}
        for key in ('rank', 'score', 'level'):
            if key in value:
                row[key] = None if value[key] is None else _integer(value[key])
        for key in ('display_name', 'guild_name'):
            if key in value:
                row[key] = None if value[key] is None else _text(value[key])
        if 'guild_uid' in value:
            row['guild_uid'] = _identifier(value['guild_uid'], nullable=True)
        row['raw_data'] = _allowed(value.get('raw_data', {}), _RAW)
        for key in ('uin', 'guildUid'):
            if key in row['raw_data']:
                row['raw_data'][key] = _identifier(row['raw_data'][key], nullable=True)
        output.append(row)
    return output


def _result(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid collector result')  # noqa: TRY004 - unified archive validation contract
    result = {key: _text(value.get(key), limit=256, nonempty=True)
              for key in ('scope', 'mode', 'entity_kind')}
    season = _identifier(value.get('season_id'), nullable=True)
    if season is not None and (season == '0' or (season.lstrip('-').isdigit() and int(season) <= 0)):
        raise ValueError('Invalid season identity')
    result['season_id'] = season
    for key, allowed in [('availability', _AVAILABILITY), ('coverage', _COVERAGE),
                         ('settle_evidence', _SETTLE)]:
        item = value.get(key)
        if not isinstance(item, str) or item not in allowed:
            raise ValueError('Invalid ranking evidence label')
        result[key] = item
    error = value.get('collection_error')
    if error is not None and (not isinstance(error, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', error)):
        raise ValueError('Expected safe collection error code')
    result['collection_error'] = error
    if error and result['coverage'] == 'COMPLETE_REPORTED_RANGE':
        result['coverage'] = 'PARTIAL'
    result['metadata'] = _allowed(value.get('metadata', {}), _META)
    result['entries'] = _entries(value.get('entries', []))
    result['member_entries'] = _entries(value.get('member_entries', []))
    if len(result['entries']) + len(result['member_entries']) > MAX_ENTRIES:
        raise ValueError('Too many ranking entries')
    pages = value.get('page_evidence', [])
    if not isinstance(pages, list) or len(pages) > MAX_PAGES:
        raise ValueError('Too many ranking pages')
    result['page_evidence'] = [_allowed(page, _PAGE) for page in pages]
    result['pages_fetched'] = _integer(value.get('pages_fetched', 0))
    if not 0 <= result['pages_fetched'] <= MAX_PAGES:
        raise ValueError('Invalid page count')
    result['collected_at_utc'] = _text(value.get('collected_at_utc'), limit=128, nonempty=True)
    rpc = value.get('source_rpc')
    if isinstance(rpc, list):
        if len(rpc) > MAX_PAGES:
            raise ValueError('Too many source RPCs')
        rpc = [_scalar(x) for x in rpc]
    else:
        rpc = _scalar(rpc)
    result['source_rpc'] = rpc
    result['source_hash'] = None if value.get('source_hash') is None else _text(value['source_hash'], limit=256)
    return result


_SCHEMA = (
    'CREATE TABLE runs (run_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, code_version TEXT NOT NULL, created_at_utc TEXT NOT NULL)',
    'CREATE TABLE attempts (attempt_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), ordinal INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(run_id,ordinal))',
    'CREATE TABLE seasons (season_key INTEGER PRIMARY KEY, scope TEXT NOT NULL, mode TEXT NOT NULL, season_id TEXT NOT NULL, entity_kind TEXT NOT NULL, UNIQUE(scope,mode,season_id,entity_kind))',
    'CREATE TABLE snapshots (snapshot_id INTEGER PRIMARY KEY, season_key INTEGER NOT NULL REFERENCES seasons(season_key), attempt_id INTEGER NOT NULL REFERENCES attempts(attempt_id), payload TEXT NOT NULL)',
    'CREATE TABLE entries (snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id), ordinal INTEGER NOT NULL, entity_id TEXT NOT NULL, score INTEGER, payload TEXT NOT NULL, PRIMARY KEY(snapshot_id,ordinal), UNIQUE(snapshot_id,entity_id))',
    'CREATE TABLE page_evidence (snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id), ordinal INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(snapshot_id,ordinal))',
)


class LeaderboardArchive:
    """Open a dedicated file database, initializing independent schema version 1."""

    def __init__(self, path):
        if str(path) == ':memory:':
            raise ValueError('Archive requires a persistent file path')
        self.path = Path(path)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise ValueError('Database is not an empty leaderboard archive')
                for statement in _SCHEMA:
                    db.execute(statement)
                db.execute('PRAGMA user_version=1')
            elif version != SCHEMA_VERSION:
                raise ValueError('Unsupported leaderboard archive schema')

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

    def save_run(self, run_id, results, *, code_version='unknown'):
        """Atomically save attempts and eligible snapshots; same normalized run is idempotent.

        Conflicting run content raises ValueError. Skipped observations never create
        seasons. Valid partial entries may survive a later page error.
        """
        run_id = _text(run_id, limit=256, nonempty=True)
        code_version = _text(code_version, limit=256, nonempty=True)
        if not isinstance(results, list) or not 1 <= len(results) <= MAX_RESULTS:
            raise ValueError('Invalid number of collector results')
        results = [_result(result) for result in results]
        fingerprint = hashlib.sha256(_json({'results': results, 'code_version': code_version}).encode('utf-8')).hexdigest()
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT content_hash FROM runs WHERE run_id=?', (run_id,)).fetchone()
            if old:
                if old['content_hash'] != fingerprint:
                    raise ValueError('Run identity conflicts with archived content')
                ids = [row[0] for row in db.execute('SELECT snapshot_id FROM snapshots JOIN attempts USING(attempt_id) WHERE run_id=? ORDER BY snapshot_id', (run_id,))]
                return {'run_id': run_id, 'snapshot_ids': ids, 'attempts': len(results)}
            db.execute('INSERT INTO runs VALUES (?,?,?,?)', (run_id, fingerprint, code_version, datetime.now(UTC).isoformat()))
            ids = []
            for ordinal, result in enumerate(results):
                base = {key: item for key, item in result.items() if key not in ('entries', 'member_entries', 'page_evidence')}
                observation = dict(base, page_evidence=result['page_evidence'],
                                   entries_count=len(result['entries']),
                                   member_entries_count=len(result['member_entries']))
                attempt = db.execute('INSERT INTO attempts(run_id,ordinal,payload) VALUES (?,?,?)', (run_id, ordinal, _json(observation))).lastrowid
                eligible = (result['season_id'] is not None
                            and result['availability'] in {'ACTIVE', 'SETTLING', 'SETTLED_CANDIDATE', 'EMPTY'}
                            and result['coverage'] != 'NO_RANKING'
                            and (result['entries'] or result['member_entries']
                                 or (result['pages_fetched'] > 0 and result['page_evidence']))
                            and (not result['collection_error'] or result['entries'] or result['member_entries']))
                if not eligible:
                    continue
                ids.append(self._snapshot(db, attempt, base, result['entries'], result['page_evidence']))
                if result['member_entries']:
                    member_coverage = (base['coverage'] if base['coverage'] in {'PARTIAL', 'INCONSISTENT'}
                                       else 'SERVER_VISIBLE_SLICE')
                    members = dict(base, entity_kind='guild_member', coverage=member_coverage)
                    members['metadata'] = dict(base['metadata'], member_scope='UNKNOWN')
                    ids.append(self._snapshot(db, attempt, members, result['member_entries'], result['page_evidence']))
            return {'run_id': run_id, 'snapshot_ids': ids, 'attempts': len(results)}

    @staticmethod
    def _snapshot(db, attempt, base, entries, pages):
        identity = tuple(base[key] for key in ('scope', 'mode', 'season_id', 'entity_kind'))
        db.execute('INSERT OR IGNORE INTO seasons(scope,mode,season_id,entity_kind) VALUES (?,?,?,?)', identity)
        key = db.execute('SELECT season_key FROM seasons WHERE scope=? AND mode=? AND season_id=? AND entity_kind=?', identity).fetchone()[0]
        sid = db.execute('INSERT INTO snapshots(season_key,attempt_id,payload) VALUES (?,?,?)', (key, attempt, _json(base))).lastrowid
        for ordinal, entry in enumerate(entries):
            db.execute('INSERT INTO entries VALUES (?,?,?,?,?)', (sid, ordinal, entry['entity_id'], entry.get('score'), _json(entry)))
        for ordinal, page in enumerate(pages):
            db.execute('INSERT INTO page_evidence VALUES (?,?,?)', (sid, ordinal, _json(page)))
        return sid

    def list_seasons(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute('SELECT scope,mode,season_id,entity_kind,count(snapshot_id) AS snapshot_count FROM seasons JOIN snapshots USING(season_key) GROUP BY season_key ORDER BY scope,mode,season_id,entity_kind')]

    def snapshots(self, scope=None, mode=None, season_id=None):
        clauses, args = [], []
        for key, value in [('scope', scope), ('mode', mode), ('season_id', season_id)]:
            if value is not None:
                clauses.append('seasons.' + key + '=?')
                args.append(_identifier(value))
        sql = 'SELECT snapshot_id,run_id,code_version,snapshots.payload FROM snapshots JOIN seasons USING(season_key) JOIN attempts USING(attempt_id) JOIN runs USING(run_id)'
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        with self._connection() as db:
            return [dict(json.loads(row['payload']), snapshot_id=row['snapshot_id'], run_id=row['run_id'], code_version=row['code_version']) for row in db.execute(sql + ' ORDER BY snapshot_id', args)]

    def export_snapshot(self, snapshot_id):
        snapshot_id = _integer(snapshot_id)
        with self._connection() as db:
            row = db.execute('SELECT snapshots.payload,run_id,code_version FROM snapshots JOIN attempts USING(attempt_id) JOIN runs USING(run_id) WHERE snapshot_id=?', (snapshot_id,)).fetchone()
            if row is None:
                raise KeyError(snapshot_id)
            result = dict(json.loads(row['payload']), snapshot_id=snapshot_id, run_id=row['run_id'], code_version=row['code_version'])
            entries = [json.loads(item[0]) for item in db.execute('SELECT payload FROM entries WHERE snapshot_id=? ORDER BY ordinal', (snapshot_id,))]
            result['entries'] = [] if result['entity_kind'] == 'guild_member' else entries
            result['member_entries'] = entries if result['entity_kind'] == 'guild_member' else []
            result['page_evidence'] = [json.loads(item[0]) for item in db.execute('SELECT payload FROM page_evidence WHERE snapshot_id=? ORDER BY ordinal', (snapshot_id,))]
            return result
