"""Durable, scope-bound portrait jobs. No renderer, filesystem publisher or sender.

JSON records are capped at 1 MiB (plans at 16 MiB), depth 64, 100,000
values, and 10,000 jobs per plan. Leases are at most one day. Each operation
owns and closes its connection; write decisions use BEGIN IMMEDIATE.
"""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3

from .model import PROFILE, digest, _hash, _id, _text

MAX_RECORD_BYTES = 1024 * 1024
MAX_PLAN_BYTES = 16 * MAX_RECORD_BYTES
MAX_LEASE_SECONDS = 86400
MAX_INTEGER = 2**63 - 1
TERMINAL = ('success', 'failed', 'blocked', 'cancelled')
_SCHEMA = (
    '''CREATE TABLE metadata (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        schema_version INTEGER NOT NULL,
        instance_id TEXT NOT NULL, source_id TEXT NOT NULL)''',
    '''CREATE TABLE jobs (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
        version TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','running','success','failed','blocked','cancelled')),
        attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0),
        owner TEXT, lease_until REAL, result TEXT)''',
    '''CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_key TEXT NOT NULL UNIQUE,
        job_id TEXT NOT NULL REFERENCES jobs(job_id),
        attempt INTEGER NOT NULL,
        instance_id TEXT NOT NULL, source_id TEXT NOT NULL,
        version TEXT NOT NULL, result TEXT NOT NULL,
        UNIQUE(job_id, attempt))''',
    'CREATE INDEX jobs_claim ON jobs(state, seq)',
)
_COLUMNS = {
    'metadata': ('singleton','schema_version','instance_id','source_id'),
    'jobs': ('seq','job_id','payload','version','state','attempt','owner','lease_until','result'),
    'events': ('id','event_key','job_id','attempt','instance_id','source_id','version','result'),
}


def _json(value, limit):
    """Reject Python-only values and bound traversal before encoding a snapshot."""
    count = 0
    characters = 0
    ancestors = set()
    def walk(item, depth):
        nonlocal count, characters
        count += 1
        if count > 100000 or depth > 64:
            raise ValueError('JSON structure exceeds limit')
        kind = type(item)
        if kind is str:
            characters += len(item)
            if characters > limit: raise ValueError('JSON exceeds size limit')
        elif kind in (dict, list):
            identity = id(item)
            if identity in ancestors: raise ValueError('circular JSON')
            ancestors.add(identity)
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str: raise ValueError('JSON object keys must be strings')
                    walk(key, depth + 1)
                    walk(child, depth + 1)
            else:
                for child in item: walk(child, depth + 1)
            ancestors.remove(identity)
        elif kind is float:
            if not math.isfinite(item): raise ValueError('nonfinite JSON number')
        elif kind not in (int, bool, type(None)):
            raise ValueError('non-JSON value')
    try:
        walk(value, 0)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode('utf-8')) > limit: raise ValueError('JSON exceeds size limit')
        return encoded
    except (OverflowError, RecursionError, UnicodeError) as exc:
        raise ValueError('invalid JSON') from exc


def _integer(value, minimum):
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise ValueError('invalid integer')


def _number(value):
    if type(value) not in (int, float): raise ValueError('invalid time')
    try: result = float(value)
    except OverflowError as exc: raise ValueError('invalid time') from exc
    if not math.isfinite(result): raise ValueError('invalid time')
    return result


class PortraitStore:
    def __init__(self, path, instance_id, source_id):
        if not _text(instance_id) or not _text(source_id): raise ValueError('invalid scope')
        self.path = str(Path(path))
        if self.path == ':memory:': raise ValueError('store requires a persistent path')
        self.instance_id, self.source_id = instance_id, source_id
        with self._connection(write=True) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if not tables and version == 0:
                for statement in _SCHEMA: db.execute(statement)
                db.execute('INSERT INTO metadata VALUES (1,1,?,?)', (instance_id, source_id))
                db.execute('PRAGMA user_version=1')
            self._check_schema(db)

    @contextmanager
    def _connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=10000')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _check_schema(self, db):
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if db.execute('PRAGMA user_version').fetchone()[0] != 1 or tables != set(_COLUMNS):
            raise ValueError('incompatible portrait store schema')
        for table, columns in _COLUMNS.items():
            if tuple(row[1] for row in db.execute('PRAGMA table_info('+table+')')) != columns:
                raise ValueError('incompatible portrait store columns')
        # Version 1 has no migrations: require its constraints and index too,
        # rather than accepting a lookalike with the same column names.
        for name, expected in zip(('metadata','jobs','events','jobs_claim'), _SCHEMA):
            row = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
            if row is None or ' '.join(row[0].split()) != ' '.join(expected.split()):
                raise ValueError('incompatible portrait store definition')
        rows = db.execute('SELECT * FROM metadata').fetchall()
        if len(rows) != 1 or tuple(rows[0]) != (1,1,self.instance_id,self.source_id):
            raise ValueError('portrait store scope/schema mismatch')

    def enqueue_published_plan(self, plan):
        frozen = json.loads(_json(plan, MAX_PLAN_BYTES))
        if type(frozen) is not dict or frozen.get('published') is not True:
            raise ValueError('plan must be published')
        if type(frozen.get('schema_version')) is not int or frozen['schema_version'] != 1:
            raise ValueError('invalid plan schema')
        if (frozen.get('instance_id'),frozen.get('source_id')) != (self.instance_id,self.source_id):
            raise ValueError('plan scope mismatch')
        if not _text(frozen.get('version')): raise ValueError('invalid version')
        jobs = frozen.get('jobs')
        if type(jobs) is not list or len(jobs) > 10000: raise ValueError('invalid jobs')
        validated = []
        seen = set()
        for job in jobs:
            if type(job) is not dict: raise ValueError('invalid job')
            if type(job.get('schema_version')) is not int or job['schema_version'] != 1:
                raise ValueError('invalid job schema')
            if any(job.get(key) != frozen[key] for key in ('instance_id','source_id','version')):
                raise ValueError('job scope/version mismatch')
            if not all(_id(job.get(key)) for key in ('character_id','skin_id')):
                raise ValueError('invalid job identity')
            if job.get('profile') != PROFILE or type(job['profile'].get('scale')) is not int:
                raise ValueError('unsupported profile')
            if not all(_hash(job.get(key)) for key in ('job_id','render_key')):
                raise ValueError('invalid hash')
            if job['job_id'] != digest([job[key] for key in ('instance_id','source_id','version','character_id','skin_id','render_key')]):
                raise ValueError('job id mismatch')
            if job.get('reason') not in ('new_character','new_skin','changed_inputs','manual_backfill'):
                raise ValueError('invalid reason')
            if job['job_id'] in seen: raise ValueError('duplicate job')
            seen.add(job['job_id'])
            validated.append((job, _json(job, MAX_RECORD_BYTES)))
        inserted = 0
        with self._connection(write=True) as db:
            self._check_schema(db)
            for job, payload in validated:
                existing = db.execute('SELECT payload FROM jobs WHERE job_id=?', (job['job_id'],)).fetchone()
                if existing:
                    if existing[0] != payload: raise ValueError('conflicting immutable job')
                    continue
                db.execute("INSERT INTO jobs(job_id,payload,version,state) VALUES (?,?,?,'pending')", (job['job_id'],payload,job['version']))
                inserted += 1
        return inserted

    def claim(self, owner, now, lease_seconds=60):
        if not _text(owner): raise ValueError('invalid owner')
        now, lease_seconds = _number(now), _number(lease_seconds)
        until = now + lease_seconds
        if not 0 < lease_seconds <= MAX_LEASE_SECONDS or not math.isfinite(until) or until <= now:
            raise ValueError('invalid lease')
        with self._connection(write=True) as db:
            self._check_schema(db)
            row = db.execute("SELECT * FROM jobs WHERE state='pending' OR (state='running' AND lease_until<=?) ORDER BY seq LIMIT 1", (now,)).fetchone()
            if row is None: return None
            attempt = row['attempt'] + 1
            _integer(attempt, 1)
            db.execute("UPDATE jobs SET state='running', attempt=?, owner=?, lease_until=? WHERE job_id=?", (attempt,owner,until,row['job_id']))
            result = json.loads(row['payload'])
            result.update(attempt=attempt, owner=owner, lease_until=until)
            return result

    def finish(self, job_id, attempt, result):
        if not _hash(job_id): raise ValueError('invalid job id')
        _integer(attempt, 1)
        payload = _json(result, MAX_RECORD_BYTES)
        result = json.loads(payload)
        if type(result) is not dict or result.get('status') not in TERMINAL:
            raise ValueError('invalid terminal result')
        with self._connection(write=True) as db:
            self._check_schema(db)
            row = db.execute("SELECT version FROM jobs WHERE job_id=? AND state='running' AND attempt=?", (job_id,attempt)).fetchone()
            if row is None: return False
            db.execute('UPDATE jobs SET state=?, result=?, owner=NULL, lease_until=NULL WHERE job_id=?', (result['status'],payload,job_id))
            db.execute('INSERT INTO events(event_key,job_id,attempt,instance_id,source_id,version,result) VALUES (?,?,?,?,?,?,?)',
                       (digest([job_id,attempt]),job_id,attempt,self.instance_id,self.source_id,row['version'],payload))
            return True

    def requeue(self, job_id):
        if not _hash(job_id): raise ValueError('invalid job id')
        with self._connection(write=True) as db:
            self._check_schema(db)
            return db.execute("UPDATE jobs SET state='pending', owner=NULL, lease_until=NULL WHERE job_id=? AND state IN ('failed','blocked','cancelled')", (job_id,)).rowcount == 1

    def list_events(self, after_id=0):
        _integer(after_id, 0)
        with self._connection() as db:
            self._check_schema(db)
            result = []
            for row in db.execute('SELECT * FROM events WHERE id>? ORDER BY id', (after_id,)):
                event = dict(row)
                event['result'] = json.loads(event['result'])
                result.append(event)
            return result
