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
        db = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=1000')
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

# V1 API remains available only for unregistered rows. Registered executions use
# the fenced V2 methods below; no legacy call can claim or finish their work.
_LegacyPortraitStore = PortraitStore

_V2_CHANGES = (
    'ALTER TABLE metadata ADD COLUMN database_id TEXT',
    '''CREATE TABLE plans (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL UNIQUE,
        manifest TEXT NOT NULL, context TEXT NOT NULL, expected_count INTEGER NOT NULL,
        final TEXT)''',
    '''CREATE TABLE members (
        plan_id TEXT NOT NULL REFERENCES plans(plan_id),
        job_id TEXT NOT NULL REFERENCES jobs(job_id),
        start_attempt INTEGER NOT NULL, final_attempt INTEGER,
        PRIMARY KEY(plan_id,job_id))''',
    'ALTER TABLE jobs ADD COLUMN plan_id TEXT REFERENCES plans(plan_id)',
    'ALTER TABLE jobs ADD COLUMN crash_count INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE jobs ADD COLUMN retry_at REAL NOT NULL DEFAULT 0',
    'ALTER TABLE jobs ADD COLUMN failure_class TEXT',
    'ALTER TABLE events ADD COLUMN plan_id TEXT REFERENCES plans(plan_id)',
    'CREATE INDEX jobs_plan_claim ON jobs(plan_id,state,retry_at,seq)',
    'CREATE INDEX events_plan ON events(plan_id,id)',
)


def _definitions(db):
    return {r[0]: ' '.join(r[1].split()) for r in db.execute(
        "SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL AND type IN ('table','index','view') AND name NOT LIKE 'sqlite_%'")}


def _expected_v2():
    db = sqlite3.connect(':memory:')
    try:
        for sql in _SCHEMA + _V2_CHANGES: db.execute(sql)
        return _definitions(db)
    finally: db.close()


_V2_DEFINITIONS = _expected_v2()


def check_v2(db, instance_id, source_id, database_id=None):
    if db.execute('PRAGMA user_version').fetchone()[0] != 2 or _definitions(db) != _V2_DEFINITIONS:
        raise ValueError('incompatible portrait store schema')
    rows = db.execute('SELECT * FROM metadata').fetchall()
    if len(rows) != 1 or tuple(rows[0])[:4] != (1,2,instance_id,source_id) or not _hash(rows[0][4]):
        raise ValueError('portrait store scope/schema mismatch')
    identity = rows[0][4]
    if database_id is not None and identity != database_id:
        raise ValueError('portrait database identity mismatch')
    return identity


def _metadata_ref(ref,scope=None):
    """Validate frozen MetadataRef wire fields; no file reads or authority claim."""
    if type(ref) is not dict or set(ref)!={'scope_key','pin_digest','locator','size','sha256'}:
        raise ValueError('invalid metadata reference fields')
    if any(not _hash(ref[k]) for k in ('scope_key','pin_digest','sha256')) or (scope is not None and ref['scope_key']!=scope):
        raise ValueError('metadata reference scope/hash mismatch')
    _integer(ref['size'],1)
    if ref['size']>32*1024**2 or not _text(ref['locator']): raise ValueError('invalid metadata reference size/locator')


def _plan_references(p):
    scope=digest([p['instance_id'],p['source_id']]); ref=p['source_capture_ref']
    fields={'schema','instance_id','source_id','version','pin_digest','tool_fingerprint','capture',
            'snapshot_digest','assigned','available','blocked','unassigned'}
    if set(ref)!=fields or ref.get('schema')!='portrait-prepared-v1': raise ValueError('invalid PreparedSourceRef fields')
    for key in ('instance_id','source_id','version','tool_fingerprint'):
        if ref[key]!=p[key]: raise ValueError('prepared reference identity mismatch: '+key)
    if ref['snapshot_digest']!=p['input_snapshot_digest'] or not _hash(ref['pin_digest']):
        raise ValueError('prepared snapshot/pin mismatch')
    _metadata_ref(ref['capture'],scope)
    if ref['capture']['pin_digest']!=ref['pin_digest']: raise ValueError('capture pin mismatch')
    for key in ('assigned','available','blocked','unassigned'): _integer(ref[key],0)
    if ref['assigned']!=ref['available']+ref['blocked']: raise ValueError('prepared availability mismatch')
    # Publication scopes also bind CDN and absolute data root. Its configured
    # read_request authenticates that domain; it is not the prepared scope.
    _metadata_ref(p['core_publication_ref'])
    if p['core_publication_ref']['pin_digest']!=ref['pin_digest']:
        raise ValueError('core publication pin mismatch')
    catalog=p['desired_catalog_ref']
    # Truthful wrapper: revision identity, immutable locator and byte SHA256.
    if set(catalog)!={'revision','locator','sha256'} or not _hash(catalog['sha256']) or not all(_text(catalog[k]) for k in ('revision','locator')):
        raise ValueError('invalid desired catalog reference')


def validate_result(job, attempt, result):
    for key in ('schema_version','job_id','instance_id','source_id','version','character_id','skin_id',
                'render_key','profile','input_snapshot_digest','tool_fingerprint','source_capture_ref'):
        if _json(result.get(key),MAX_RECORD_BYTES) != _json(job[key],MAX_RECORD_BYTES): raise ValueError('result identity mismatch: '+key)
    if type(result.get('attempt')) is not int or result['attempt'] != attempt:
        raise ValueError('result attempt mismatch')
    if result.get('status') not in TERMINAL: raise ValueError('invalid terminal result')
    if type(result.get('diagnostics')) is not list or not all(type(x) is str for x in result['diagnostics']):
        raise ValueError('invalid diagnostics')
    if result['status'] != 'success': return
    for key, expected in (('artifact_render_key',job['render_key']),
                          ('artifact_tool_fingerprint',job['tool_fingerprint']),
                          ('source_version',job['version']),('archive_profile',PROFILE)):
        if result.get(key) != expected: raise ValueError('invalid artifact lineage: '+key)
    for path, sha, directory in (('artifact_path','artifact_sha256','artifacts'),('archive_path','archive_sha256','archive')):
        if not _hash(result.get(sha)) or result.get(path) != directory+'/'+result[sha]+'.png':
            raise ValueError('invalid media identity')
    if result.get('delivery_profile') not in ('original','fitted_png'): raise ValueError('invalid delivery profile')
    for key in ('width','height','archive_width','archive_height'): _integer(result.get(key),1)
    if result['archive_width']*result['archive_height'] > 64*1024*1024:
        raise ValueError('archive pixel bound')
    if result['width'] > result['archive_width'] or result['height'] > result['archive_height']:
        raise ValueError('delivery exceeds archive dimensions')
    if result['delivery_profile']=='original' and any(result[a]!=result[b] for a,b in (
            ('artifact_sha256','archive_sha256'),('width','archive_width'),('height','archive_height'))):
        raise ValueError('original media mismatch')


def plan_snapshot(db, plan_id):
    p=db.execute('SELECT * FROM plans WHERE plan_id=?',(plan_id,)).fetchone()
    if p is None: raise KeyError(plan_id)
    if p['final'] is not None: return json.loads(p['final'])
    counts=dict.fromkeys(('pending','running',*TERMINAL),0)
    for row in db.execute('SELECT j.state,count(*) FROM members m JOIN jobs j ON j.job_id=m.job_id AND j.plan_id=m.plan_id WHERE m.plan_id=? GROUP BY j.state',(plan_id,)):
        counts[row[0]]=row[1]
    if sum(counts.values())!=p['expected_count']: raise ValueError('broken plan membership')
    return dict(**json.loads(p['context']),plan_id=plan_id,registration=p['seq'],expected_count=p['expected_count'],sealed=False,counts=counts,
                watermark=db.execute('SELECT coalesce(max(id),0) FROM events WHERE plan_id=?',(plan_id,)).fetchone()[0])


class PortraitStore(_LegacyPortraitStore):
    """V2 writer. SourceReady validation belongs to the trusted coordinator.

    Omitted allowed_plan_ids explicitly selects legacy/unregistered compatibility.
    Runtime MUST provide a nonempty, bounded registered selection after admission.
    Times are finite shared wall-clock seconds; no network/worker work occurs here.
    """
    def __init__(self,path,instance_id,source_id):
        import secrets
        if not _text(instance_id) or not _text(source_id): raise ValueError('invalid scope')
        self.path=str(Path(path))
        if self.path==':memory:': raise ValueError('store requires persistent path')
        self.instance_id,self.source_id=instance_id,source_id
        with self._connection(write=True) as db:
            version=db.execute('PRAGMA user_version').fetchone()[0]
            if version==0 and not _definitions(db):
                for sql in _SCHEMA: db.execute(sql)
                db.execute('INSERT INTO metadata VALUES (1,1,?,?)',(instance_id,source_id))
                db.execute('PRAGMA user_version=1'); version=1
            if version==1:
                _LegacyPortraitStore._check_schema(self,db)
                for sql in _V2_CHANGES: db.execute(sql)
                db.execute('UPDATE metadata SET schema_version=2,database_id=?',(secrets.token_hex(32),))
                db.execute('PRAGMA user_version=2')
            self.database_id=check_v2(db,instance_id,source_id)

    def _check_schema(self,db):
        check_v2(db,self.instance_id,self.source_id,self.database_id)

    def register_closed_plan(self,plan):
        from .model import validate_job
        p=json.loads(_json(plan,MAX_PLAN_BYTES))
        if type(p) is not dict or type(p.get('schema_version')) is not int or p['schema_version']!=2:
            raise ValueError('invalid closed plan schema')
        if (p.get('instance_id'),p.get('source_id'))!=(self.instance_id,self.source_id): raise ValueError('plan scope mismatch')
        if not _text(p.get('version')) or p.get('kind') not in ('automatic','manual'): raise ValueError('invalid generation')
        for key in ('input_snapshot_digest','tool_fingerprint','expected_jobset_digest'):
            if not _hash(p.get(key)): raise ValueError('invalid '+key)
        for key in ('source_capture_ref','core_publication_ref','desired_catalog_ref','comparison_evidence'):
            if type(p.get(key)) is not dict or not p[key]: raise ValueError('missing immutable '+key)
        _plan_references(p)
        if type(p['comparison_evidence'].get('baseline')) is not bool: raise ValueError('missing baseline classification')
        policy=p.get('recovery')
        if type(policy) is not dict: raise ValueError('missing recovery policy')
        _integer(policy.get('max_crashes'),1)
        if policy['max_crashes']>100: raise ValueError('crash budget exceeds bound')
        _number(policy.get('deadline'))
        if not 0<=_number(policy.get('backoff_seconds'))<=MAX_LEASE_SECONDS: raise ValueError('invalid backoff')
        jobs=p.get('jobs')
        if type(jobs) is not list or len(jobs)>10000: raise ValueError('invalid jobs')
        if digest(jobs)!=p['expected_jobset_digest']: raise ValueError('jobset digest mismatch')
        validated=[]; seen=set()
        for j in jobs:
            if type(j) is not dict or type(j.get('schema_version')) is not int or j['schema_version']!=2: raise ValueError('invalid job schema')
            validate_job(dict(j,schema_version=1))
            for key in ('instance_id','source_id','version','input_snapshot_digest','tool_fingerprint','source_capture_ref'):
                if j.get(key)!=p[key]: raise ValueError('job/plan mismatch: '+key)
            if j['job_id'] in seen: raise ValueError('duplicate member')
            seen.add(j['job_id']); validated.append((j,_json(j,MAX_RECORD_BYTES)))
        identity=[self.instance_id,self.source_id,p['kind']]
        if p['kind']=='manual':
            if not _text(p.get('request_id')): raise ValueError('missing request id')
            identity.append(p['request_id'])
        else: identity.extend(p[k] for k in ('version','input_snapshot_digest','tool_fingerprint'))
        pid=digest(identity); payload=_json(p,MAX_PLAN_BYTES)
        with self._connection(write=True) as db:
            self._check_schema(db)
            old=db.execute('SELECT manifest FROM plans WHERE plan_id=?',(pid,)).fetchone()
            if old:
                if old[0]!=payload: raise ValueError('conflicting immutable closed plan')
                return dict(plan_id=pid,inserted=0)
            context={k:p[k] for k in ('kind','version','instance_id','source_id','input_snapshot_digest','tool_fingerprint')}
            context['summary_key']=digest(['portrait_summary',self.instance_id,self.source_id,pid])
            db.execute('INSERT INTO plans(plan_id,manifest,context,expected_count) VALUES (?,?,?,?)',(pid,payload,_json(context,MAX_RECORD_BYTES),len(jobs)))
            for j,body in validated:
                old=db.execute('SELECT * FROM jobs WHERE job_id=?',(j['job_id'],)).fetchone()
                if old:
                    if old['payload']!=body: raise ValueError('conflicting immutable job')
                    if old['plan_id'] is None: raise ValueError('legacy job requires explicit reconciliation')
                    previous=db.execute('SELECT final FROM plans WHERE plan_id=?',(old['plan_id'],)).fetchone()
                    if p['kind']!='manual' or previous is None or previous[0] is None: raise ValueError('overlapping execution ownership')
                    db.execute("UPDATE jobs SET plan_id=?,state='pending',owner=NULL,lease_until=NULL,result=NULL,crash_count=0,retry_at=0,failure_class=NULL WHERE job_id=?",(pid,j['job_id']))
                else:
                    db.execute("INSERT INTO jobs(job_id,payload,version,state,plan_id) VALUES (?,?,?,'pending',?)",(j['job_id'],body,j['version'],pid))
                db.execute('INSERT INTO members VALUES (?,?,?,NULL)',(pid,j['job_id'],old['attempt'] if old else 0))
        return dict(plan_id=pid,inserted=len(jobs))

    def seal_plan(self,plan_id):
        if not _hash(plan_id): raise ValueError('invalid plan id')
        with self._connection(write=True) as db:
            self._check_schema(db); snap=plan_snapshot(db,plan_id)
            if snap['sealed']: return snap
            if snap['counts']['pending'] or snap['counts']['running']: return None
            db.execute('UPDATE members SET final_attempt=(SELECT attempt FROM jobs WHERE jobs.job_id=members.job_id) WHERE plan_id=?',(plan_id,))
            snap['watermark']=db.execute('SELECT coalesce(max(id),0) FROM events').fetchone()[0]
            snap['sealed']=True; snap['counts']={s:snap['counts'][s] for s in TERMINAL}
            db.execute('UPDATE plans SET final=? WHERE plan_id=?',(_json(snap,MAX_RECORD_BYTES),plan_id))
            return snap

    def claim(self,owner,now,lease_seconds=60,*,allowed_plan_ids=None):
        now,until=self._lease(owner,now,lease_seconds)
        if allowed_plan_ids is not None and (type(allowed_plan_ids) not in (list,tuple) or not 1<=len(allowed_plan_ids)<=100 or not all(_hash(x) for x in allowed_plan_ids)):
            raise ValueError('invalid registered plan selection')
        with self._connection(write=True) as db:
            self._check_schema(db)
            params=[now,now]
            where='j.plan_id IS NULL'
            if allowed_plan_ids is not None:
                where='j.plan_id IN ('+','.join('?' for _ in allowed_plan_ids)+') AND p.final IS NULL'
                params+=list(allowed_plan_ids)
            row=db.execute("SELECT j.*,p.manifest FROM jobs j LEFT JOIN plans p ON p.plan_id=j.plan_id WHERE (j.state='pending' OR (j.state='running' AND j.lease_until<=?)) AND j.retry_at<=? AND "+where+' ORDER BY j.seq LIMIT 1',params).fetchone()
            if row is None: return None
            attempt=row['attempt']+1; _integer(attempt,1)
            crashes=row['crash_count']+(row['state']=='running' and row['plan_id'] is not None)
            policy=json.loads(row['manifest'])['recovery'] if row['manifest'] else None
            exhausted=bool(policy and (crashes>=policy['max_crashes'] or now>=policy['deadline']))
            failure='crash_budget_exhausted' if policy and crashes>=policy['max_crashes'] else ('deadline_exhausted' if exhausted else ('worker_expired' if crashes>row['crash_count'] else row['failure_class']))
            if policy and row['state']=='running' and not exhausted and policy['backoff_seconds']>0:
                db.execute("UPDATE jobs SET state='pending',owner=NULL,lease_until=NULL,result=NULL,crash_count=?,failure_class=?,retry_at=? WHERE job_id=?",(crashes,failure,now+policy['backoff_seconds'],row['job_id']))
                return None
            db.execute("UPDATE jobs SET state='running',attempt=?,owner=?,lease_until=?,crash_count=?,failure_class=? WHERE job_id=?",(attempt,owner,until,crashes,failure,row['job_id']))
            out=json.loads(row['payload']); out.update(attempt=attempt,owner=owner,lease_until=until)
            if policy: out.update(plan_id=row['plan_id'],crash_count=crashes,recovery_required=exhausted,failure_class=failure,recovery=policy)
            return out

    @staticmethod
    def _lease(owner,now,seconds):
        if not _text(owner): raise ValueError('invalid owner')
        now,seconds=_number(now),_number(seconds); until=now+seconds
        if not 0<seconds<=MAX_LEASE_SECONDS or not math.isfinite(until) or until<=now: raise ValueError('invalid lease')
        return now,until

    def _owned(self,db,job_id,attempt,owner,now):
        if not _hash(job_id) or not _text(owner): raise ValueError('invalid ownership identity')
        _integer(attempt,1); now=_number(now)
        return db.execute("SELECT j.*,p.manifest FROM jobs j JOIN plans p ON p.plan_id=j.plan_id WHERE job_id=? AND attempt=? AND owner=? AND state='running' AND lease_until>? AND p.final IS NULL",(job_id,attempt,owner,now)).fetchone()

    def renew(self,job_id,attempt,owner,now,lease_seconds=60):
        now,until=self._lease(owner,now,lease_seconds)
        with self._connection(write=True) as db:
            self._check_schema(db); row=self._owned(db,job_id,attempt,owner,now)
            if row is None: return False
            policy=json.loads(row['manifest'])['recovery']
            if now>=policy['deadline'] or row['crash_count']>=policy['max_crashes']: return False
            db.execute('UPDATE jobs SET lease_until=? WHERE job_id=?',(max(until,row['lease_until']),job_id)); return True

    def defer(self,job_id,attempt,owner,now,reason,*,retry_at=None):
        if reason not in ('updater','resource','stop','transport'): raise ValueError('invalid cooperative defer reason')
        now=_number(now); retry_at=now if retry_at is None else _number(retry_at)
        if not now<=retry_at<=now+MAX_LEASE_SECONDS: raise ValueError('invalid retry time')
        with self._connection(write=True) as db:
            self._check_schema(db); row=self._owned(db,job_id,attempt,owner,now)
            if row is None: return False
            policy=json.loads(row['manifest'])['recovery']
            if row['crash_count']>=policy['max_crashes'] or now>=policy['deadline']: return False
            db.execute("UPDATE jobs SET state='pending',owner=NULL,lease_until=NULL,result=NULL,retry_at=? WHERE job_id=?",(retry_at,job_id)); return True

    def finish(self,job_id,attempt,result,*,owner=None,now=None):
        if not _hash(job_id): raise ValueError('invalid job id')
        _integer(attempt,1); payload=_json(result,MAX_RECORD_BYTES); result=json.loads(payload)
        if type(result) is not dict or result.get('status') not in TERMINAL: raise ValueError('invalid terminal result')
        with self._connection(write=True) as db:
            self._check_schema(db)
            if owner is None and now is None:
                row=db.execute("SELECT * FROM jobs WHERE job_id=? AND attempt=? AND state='running' AND plan_id IS NULL",(job_id,attempt)).fetchone()
            else: row=self._owned(db,job_id,attempt,owner,now)
            if row is None: return False
            if row['plan_id'] is not None:
                validate_result(json.loads(row['payload']),attempt,result)
                policy=json.loads(row['manifest'])['recovery']
                failure='crash_budget_exhausted' if row['crash_count']>=policy['max_crashes'] else ('deadline_exhausted' if now>=policy['deadline'] else None)
                if failure and (result['status']!='failed' or result.get('failure_class')!=failure or not result['diagnostics']): raise ValueError('exhausted recovery requires truthful failure')
            db.execute('UPDATE jobs SET state=?,result=?,owner=NULL,lease_until=NULL WHERE job_id=?',(result['status'],payload,job_id))
            db.execute('INSERT INTO events(event_key,job_id,attempt,instance_id,source_id,version,result,plan_id) VALUES (?,?,?,?,?,?,?,?)',(digest([job_id,attempt]),job_id,attempt,self.instance_id,self.source_id,row['version'],payload,row['plan_id']))
            return True

    def requeue(self,job_id):
        if not _hash(job_id): raise ValueError('invalid job id')
        with self._connection(write=True) as db:
            self._check_schema(db)
            # Registered terminal decisions are final for that execution; retry
            # through a new explicit manual plan after sealing.
            return db.execute("UPDATE jobs SET state='pending',owner=NULL,lease_until=NULL WHERE job_id=? AND plan_id IS NULL AND state IN ('failed','blocked','cancelled')",(job_id,)).rowcount==1
