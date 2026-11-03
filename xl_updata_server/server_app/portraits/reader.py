"""Bounded stdlib-only, read-only evidence from an existing V2 WAL database.

No writer construction, migrations, mkdir, journal-mode changes or network I/O.
Cursors are scoped to the durable database identity. Legacy events are explicitly
lineage_unknown and never become eligible new-format accepted artifacts.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from .model import digest, _hash, _text, validate_job
from .store import (MAX_RECORD_BYTES, MAX_PLAN_BYTES, _integer, _json,
                    check_v2, plan_snapshot, validate_result, TERMINAL)


class PortraitReader:
    def __init__(self,path,instance_id,source_id):
        if not _text(instance_id) or not _text(source_id): raise ValueError('invalid scope')
        self.path=Path(path).resolve()
        self.instance_id,self.source_id=instance_id,source_id
        self.database_id=None
        self.incarnation=self._file_identity()
        with self._connection() as db:
            self.database_id=check_v2(db,instance_id,source_id)

    def _file_identity(self):
        try:
            info=self.path.stat()
        except OSError as exc: raise ValueError('portrait database unavailable') from exc
        if not info.st_ino: raise ValueError('database file identity unavailable')
        return [info.st_dev,info.st_ino]

    def _check_file(self):
        if self._file_identity()!=self.incarnation:
            raise ValueError('portrait database incarnation mismatch')

    @contextmanager
    def _connection(self):
        self._check_file()
        try:
            db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=1,isolation_level=None)
        except sqlite3.Error as exc: raise ValueError('portrait database unavailable') from exc
        db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            check_v2(db,self.instance_id,self.source_id,self.database_id)
            self._check_file()
            yield db
            db.commit()
            self._check_file()
        except sqlite3.Error as exc:
            raise ValueError('invalid portrait database') from exc
        finally: db.close()

    def _cursor_base(self,stream):
        return dict(database_id=self.database_id,instance_id=self.instance_id,source_id=self.source_id,
                    incarnation=self.incarnation,stream=stream)

    @staticmethod
    def _anchor(db,stream,position):
        if position==0: return None
        if stream=='events':
            row=db.execute('SELECT * FROM events WHERE id=?',(position,)).fetchone()
        else:
            row=db.execute('SELECT seq,plan_id,context,expected_count FROM plans WHERE seq=?',(position,)).fetchone()
        if row is None: raise ValueError('cursor anchor missing: database rolled back or truncated')
        return digest(dict(row))

    def _cursor(self,db,stream,position):
        table,column=('events','id') if stream=='events' else ('plans','seq')
        highwater=db.execute('SELECT coalesce(max('+column+'),0) FROM '+table).fetchone()[0]
        return dict(self._cursor_base(stream),position=position,anchor=self._anchor(db,stream,position),
                    highwater=highwater,highwater_anchor=self._anchor(db,stream,highwater))

    def _position(self,db,stream,position,cursor):
        if cursor is not None:
            if type(cursor) is not dict or any(cursor.get(k)!=v for k,v in self._cursor_base(stream).items()):
                raise ValueError('cursor database/scope/stream/incarnation mismatch')
            position=cursor.get('position')
            _integer(cursor.get('highwater'),0); _integer(position,0)
            if cursor['highwater']<position: raise ValueError('invalid cursor highwater')
            for key,anchor in (('position','anchor'),('highwater','highwater_anchor')):
                if self._anchor(db,stream,cursor[key])!=cursor.get(anchor):
                    raise ValueError('cursor continuity mismatch')
        _integer(position,0)
        # Raw positions also cannot silently point beyond a restored history.
        self._anchor(db,stream,position)
        return position

    @staticmethod
    def _bounds(limit,max_bytes):
        _integer(limit,1); _integer(max_bytes,1)
        if limit>100 or max_bytes>16*MAX_RECORD_BYTES: raise ValueError('page exceeds bound')

    def read_events(self,after_id=0,limit=16,max_bytes=4*MAX_RECORD_BYTES,*,watermark=None,cursor=None):
        self._bounds(limit,max_bytes)
        if watermark is not None: _integer(watermark,0)
        events=[]; used=0; position=after_id
        with self._connection() as db:
            after_id=self._position(db,'events',after_id,cursor); position=after_id
            rows=db.execute('SELECT * FROM events WHERE id>? AND id<=? ORDER BY id LIMIT ?',
                            (after_id,watermark if watermark is not None else 2**63-1,limit))
            for row in rows:
                size=len(row['result'].encode('utf-8'))
                if size>MAX_RECORD_BYTES: raise ValueError('event record exceeds bound')
                if used+size>max_bytes:
                    if not events: raise ValueError('first event exceeds byte budget')
                    break
                e=dict(row)
                if (e['instance_id'],e['source_id'])!=(self.instance_id,self.source_id) or e['event_key']!=digest([e['job_id'],e['attempt']]):
                    raise ValueError('invalid event identity')
                _integer(e['attempt'],1)
                job=db.execute('SELECT payload,version,attempt,plan_id FROM jobs WHERE job_id=?',(e['job_id'],)).fetchone()
                if job is None or job['version']!=e['version']: raise ValueError('event job mismatch')
                if len(job['payload'].encode('utf-8'))>MAX_RECORD_BYTES: raise ValueError('job record exceeds bound')
                try: r=json.loads(e['result']); j=json.loads(job['payload'])
                except (ValueError,RecursionError) as exc: raise ValueError('malformed event') from exc
                _json(r,MAX_RECORD_BYTES); _json(j,MAX_RECORD_BYTES)
                if type(r) is not dict or r.get('status') not in TERMINAL: raise ValueError('malformed terminal result')
                if type(j.get('schema_version')) is not int or j['schema_version'] not in (1,2): raise ValueError('invalid job wire schema')
                validate_job(dict(j,schema_version=1))
                if any(j.get(k)!=e[k] for k in ('job_id','version','instance_id','source_id')): raise ValueError('event payload identity mismatch')
                if e['plan_id'] is not None:
                    member=db.execute('SELECT start_attempt,final_attempt FROM members WHERE plan_id=? AND job_id=?',(e['plan_id'],e['job_id'])).fetchone()
                    if member is None or not member['start_attempt']<e['attempt']<= (member['final_attempt'] if member['final_attempt'] is not None else job['attempt']):
                        raise ValueError('event membership attempt mismatch')
                    if member['final_attempt'] is None and job['plan_id']!=e['plan_id']:
                        raise ValueError('event current execution mismatch')
                    validate_result(j,e['attempt'],r)
                    e['provenance']='registered'
                else:
                    membership=db.execute('SELECT 1 FROM members WHERE job_id=? LIMIT 1',(e['job_id'],)).fetchone()
                    if j['schema_version']!=1 or job['plan_id'] is not None or membership is not None:
                        raise ValueError('registered event lost plan attribution')
                    e['provenance']='legacy_lineage_unknown'
                e['result']=r
                size=len(_json(e,MAX_RECORD_BYTES+4096).encode('utf-8'))
                if used+size>max_bytes:
                    if not events: raise ValueError('first event exceeds byte budget')
                    break
                events.append(e); used+=size; position=e['id']
            next_cursor=self._cursor(db,'events',position)
        return dict(events=events,cursor=next_cursor,bytes=used)

    def list_plans(self,after_registration=0,limit=16,*,cursor=None,sealed=None):
        self._bounds(limit,MAX_RECORD_BYTES)
        if sealed is not None and type(sealed) is not bool: raise ValueError('invalid seal filter')
        plans=[]
        with self._connection() as db:
            position=self._position(db,'plans',after_registration,cursor)
            where='' if sealed is None else (' AND final IS NOT NULL' if sealed else ' AND final IS NULL')
            for row in db.execute('SELECT seq,plan_id,expected_count,final FROM plans WHERE seq>?'+where+' ORDER BY seq LIMIT ?',(position,limit)):
                plans.append(dict(plan_id=row['plan_id'],registration=row['seq'],expected_count=row['expected_count'],sealed=row['final'] is not None))
                position=row['seq']
            next_cursor=self._cursor(db,'plans',position)
        return dict(plans=plans,cursor=next_cursor)

    def read_plan_snapshot(self,plan_id):
        if not _hash(plan_id): raise ValueError('invalid plan id')
        with self._connection() as db: return plan_snapshot(db,plan_id)

    def read_manifest(self,plan_id):
        """Explicit bounded recovery read; ordinary polling never returns jobs."""
        if not _hash(plan_id): raise ValueError('invalid plan id')
        with self._connection() as db:
            row=db.execute('SELECT manifest FROM plans WHERE plan_id=?',(plan_id,)).fetchone()
            if row is None: raise KeyError(plan_id)
            if len(row[0].encode('utf-8'))>MAX_PLAN_BYTES: raise ValueError('oversize manifest')
            p=json.loads(row[0]); _json(p,MAX_PLAN_BYTES)
            return p
