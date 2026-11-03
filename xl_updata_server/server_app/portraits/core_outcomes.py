"""Owned, metadata-only core outcomes; readers never create or repair storage."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path

MAX_RECORD = 65536
_CURRENT = ContextVar('portrait_core_run', default=None)


class CoreJournalError(OSError):
    """Operational journal failure; leave absent terminal rows for owned recovery."""


def current_run():
    return _CURRENT.get()


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid identity')
    return value


def _encode(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    if len(raw.encode()) > MAX_RECORD:
        raise ValueError('record too large')
    return raw


def _decode(raw):
    if not isinstance(raw, str) or len(raw.encode()) > MAX_RECORD:
        raise ValueError('invalid record')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('invalid record')  # noqa: TRY004 - corrupt JSON content, not caller type
    return value


def _hex_digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('invalid digest')
    return value


def _code(value):
    if type(value) is not str or re.fullmatch('[a-z_]{1,64}', value) is None:
        raise ValueError('invalid diagnostic code')


def _reference(value, scope, pin, *, cap=32*1024**2):
    if type(value) is not dict or set(value) != {'scope_key', 'pin_digest', 'locator', 'size', 'sha256'}:
        raise ValueError('invalid metadata reference shape')
    for key in ('scope_key', 'pin_digest', 'sha256'):
        _hex_digest(value[key])
    if value['scope_key'] != scope or value['pin_digest'] != pin:
        raise ValueError('metadata reference domain mismatch')
    if type(value['size']) is not int or not 0 < value['size'] <= cap:
        raise ValueError('metadata reference size invalid')
    if value['locator'] != f"metadata/{scope}/{pin}/{value['sha256']}.jsonbytes":
        raise ValueError('metadata reference locator mismatch')


def _cards(value, scope, pin, *, unresolved=False):
    if type(value) is not dict:
        raise ValueError('invalid card intent')
    kind = value.get('kind')
    if kind == 'handoff_unresolved' and unresolved and set(value) == {'kind'}:
        return
    if kind == 'none' and set(value) == {'kind', 'reason'} and value['reason'] in (
            'baseline', 'disabled-cards', 'no-new-output', 'bootstrap-history-unavailable'):
        return
    if kind != 'batch' or set(value) != {'kind', 'manifest', 'manifest_sha256', 'keys', 'files'}:
        raise ValueError('invalid card intent shape')
    _reference(value['manifest'], scope, pin)
    _hex_digest(value['manifest_sha256'])
    if value['manifest_sha256'] != value['manifest']['sha256']:
        raise ValueError('card manifest digest mismatch')
    keys, files = value['keys'], value['files']
    if type(keys) is not list or type(files) is not list or not 0 < len(keys) == len(files) <= 1000:
        raise ValueError('invalid card batch size')
    seen = set()
    for key, file in zip(keys, files):
        if type(key) is not str or len(key) > 8192 or key in seen:
            raise ValueError('invalid card key')
        seen.add(key)
        if type(file) is not dict or set(file) != {'path', 'sha256'}:
            raise ValueError('invalid card file')
        _hex_digest(file['sha256'])
        parts = key.split(':', 3)
        if len(parts) != 4 or parts[0] != 'game-card' or not parts[1].isdigit() or not parts[2].isdigit():
            raise ValueError('invalid card key identity')
        name = parts[3]
        if not name.endswith('.png') or any(c in name for c in ('/', '\\', ':')) or any(ord(c) < 32 for c in name) or name in ('.', '..'):
            raise ValueError('invalid card filename')
        if file['path'] != f'outbox/{parts[1]}/{name}':
            raise ValueError('card path mismatch')


def _validate(value, scope, run_id, table):
    if value.get('scope') != scope or value.get('run_id') != run_id:
        raise ValueError('malformed journal identity')
    _hex_digest(scope)
    _text(run_id)
    promotion = value.get('promotion')
    if promotion is not None and (type(promotion) is not int or promotion < 1):
        raise ValueError('invalid promotion')
    if table == 'outcomes':
        required = {'run_id', 'scope', 'trigger', 'version', 'classification', 'announcement_eligible',
                    'phase', 'source_state', 'counts', 'warnings', 'cards', 'pin_digest', 'code', 'promotion'}
        if set(value) != required or value['phase'] not in ('published', 'failed', 'interrupted', 'no_change'):
            raise ValueError('malformed outcome')
        if value['trigger'] not in ('manual', 'scheduled') or value['source_state'] not in ('pending', 'failed', 'not_requested'):
            raise ValueError('malformed trigger/source state')
        if value['classification'] not in ('unknown', 'processed', 'bootstrap', 'noop') or type(value['announcement_eligible']) is not bool:
            raise ValueError('malformed classification')
        if value['version'] is not None:
            _text(value['version'])
        if value['phase'] == 'published' and (value['version'] is None or promotion is None):
            raise ValueError('missing published target/promotion')
        if value['phase'] in ('failed', 'interrupted') and promotion is not None:
            raise ValueError('failed core has promotion')
        allowed_counts = {'character_count', 'skin_count', 'card_count', 'card_warning_count',
                          'card_failure_count', 'new_character_count', 'updated_character_count'}
        if type(value['counts']) is not dict or not set(value['counts']) <= allowed_counts or any(type(v) is not int or v < 0 for v in value['counts'].values()):
            raise ValueError('malformed counts')
        if type(value['warnings']) is not list or len(value['warnings']) > 5:
            raise ValueError('malformed warnings')
        for warning in value['warnings']:
            _code(warning)
        if value['code'] is not None:
            _code(value['code'])
        if value['pin_digest'] is not None:
            _hex_digest(value['pin_digest'])
        _cards(value['cards'], scope, value['pin_digest'], unresolved=True)
    elif value.get('state') == 'failed':
        if set(value) != {'run_id', 'scope', 'promotion', 'state', 'code'}:
            raise ValueError('malformed failure link')
        _code(value['code'])
    elif value.get('state') == 'ready':
        if set(value) != {'run_id', 'scope', 'promotion', 'state', 'request_id', 'ref', 'pin_digest', 'cards'}:
            raise ValueError('malformed ready link')
        _hex_digest(value['request_id'])
        _hex_digest(value['pin_digest'])
        _reference(value['ref'], scope, value['pin_digest'], cap=1024**2)
        _cards(value['cards'], scope, value['pin_digest'])
    else:
        raise ValueError('malformed source link')


def _journal_identity(value):
    if type(value) is not str:
        raise ValueError('invalid journal incarnation')
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        raise ValueError('invalid journal incarnation') from None
    if canonical != value:
        raise ValueError('invalid journal incarnation')
    return value


def _file_identity(path):
    st = path.stat()
    return [st.st_dev, st.st_ino]


def _chain(previous, sequence, raw):
    return hashlib.sha256(json.dumps([previous, sequence, raw], separators=(',', ':')).encode()).hexdigest()


def _append(db, table, run_id, raw, promotion=None):
    previous = db.execute(f'SELECT prefix FROM {table} ORDER BY sequence DESC LIMIT 1').fetchone()
    previous = previous[0] if previous else '0'*64
    if table == 'outcomes':
        sequence = db.execute('INSERT INTO outcomes(run_id,promotion,payload,prefix) VALUES(?,?,?,?)',
                              (run_id, promotion, raw, '')).lastrowid
    else:
        sequence = db.execute('INSERT INTO source_links(run_id,payload,prefix) VALUES(?,?,?)',
                              (run_id, raw, '')).lastrowid
    db.execute(f'UPDATE {table} SET prefix=? WHERE sequence=?', (_chain(previous, sequence, raw), sequence))
    return sequence


class CoreReader:
    def __init__(self, path, *, scope, journal_id=None):
        self.path = Path(path).absolute()
        self.scope = _hex_digest(scope)
        self.journal_id = journal_id

    @contextmanager
    def _db(self):
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        file_id = _file_identity(self.path)
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            rows = db.execute('SELECT schema,scope,journal_id FROM identity').fetchall()
            if len(rows) != 1 or rows[0][:2] != (2, self.scope):
                raise ValueError('journal scope/schema mismatch')
            _journal_identity(rows[0][2])
            if self.journal_id is not None and rows[0][2] != self.journal_id:
                raise ValueError('journal replaced')
            if _file_identity(self.path) != file_id:
                raise ValueError('journal file replaced')
            yield db
            if _file_identity(self.path) != file_id:
                raise ValueError('journal file replaced')
        finally:
            db.close()

    def identity(self):
        with self._db() as db:
            return db.execute('SELECT journal_id FROM identity').fetchone()[0]

    def _row(self, db, table, row):
        sequence, run_id, raw, prefix = row
        value = _decode(raw)
        _validate(value, self.scope, run_id, table)
        _hex_digest(prefix)
        prior = db.execute(f'SELECT prefix FROM {table} WHERE sequence<? ORDER BY sequence DESC LIMIT 1', (sequence,)).fetchone()
        if prefix != _chain(prior[0] if prior else '0'*64, sequence, raw):
            raise ValueError('journal row prefix mismatch')
        parent = db.execute('SELECT promotion,substr(payload,1,65537) FROM outcomes WHERE run_id=?', (run_id,)).fetchone()
        if parent is None or value['promotion'] != parent[0]:
            raise ValueError('journal promotion column mismatch')
        if table == 'source_links':
            outcome = _decode(parent[1])
            _validate(outcome, self.scope, run_id, 'outcomes')
            if outcome['promotion'] != parent[0] or outcome['phase'] not in ('published', 'no_change'):
                raise ValueError('source link parent mismatch')
            if value['state'] == 'ready' and outcome['pin_digest'] is not None and outcome['pin_digest'] != value['pin_digest']:
                raise ValueError('source link parent pin mismatch')
        return dict(value, sequence=sequence)

    @staticmethod
    def _bounds(after_sequence, limit, max_bytes):
        if type(after_sequence) is not int or after_sequence < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid cursor/limit')
        if type(max_bytes) is not int or not 1 <= max_bytes <= 1048576:
            raise ValueError('invalid byte cap')

    def _items(self, db, table, after_sequence, limit, max_bytes):
        self._bounds(after_sequence, limit, max_bytes)
        rows = db.execute(f'SELECT sequence,run_id,substr(payload,1,65537),prefix FROM {table} WHERE sequence>? ORDER BY sequence LIMIT ?',
                          (after_sequence, limit)).fetchall()
        result, size = [], 0
        for row in rows:
            item = self._row(db, table, row)
            size += len(row[2].encode())
            if size > max_bytes:
                if not result:
                    raise ValueError('first record exceeds byte cap')
                break
            result.append(item)
        return result

    def _page(self, table, after_sequence, limit, max_bytes):
        # Compatibility snapshots only: numeric cursors do not survive replacement.
        with self._db() as db:
            return self._items(db, table, after_sequence, limit, max_bytes)

    def _anchor(self, db, table, sequence):
        if type(sequence) is not int or sequence < 0:
            raise ValueError('invalid anchor sequence')
        if sequence == 0:
            return '0'*64
        row = db.execute(f'SELECT sequence,run_id,substr(payload,1,65537),prefix FROM {table} WHERE sequence=?', (sequence,)).fetchone()
        if row is None:
            raise ValueError('journal cursor rolled back')
        self._row(db, table, row)
        return row[3]

    def _fenced_page(self, table, cursor, limit, max_bytes):
        with self._db() as db:
            identity = db.execute('SELECT journal_id FROM identity').fetchone()[0]
            file_id = _file_identity(self.path)
            after = 0
            if cursor is not None:
                required = {'schema', 'scope', 'journal_id', 'file_id', 'stream', 'sequence', 'prefix', 'high_water', 'high_prefix'}
                if not isinstance(cursor, dict) or set(cursor) != required or type(cursor['schema']) is not int or cursor['schema'] != 1:
                    raise ValueError('invalid fenced cursor')
                if (cursor['scope'], cursor['journal_id'], cursor['file_id'], cursor['stream']) != (self.scope, identity, file_id, table):
                    raise ValueError('journal cursor incarnation mismatch')
                after = cursor['sequence']
                high = cursor['high_water']
                if type(after) is not int or type(high) is not int or not 0 <= after <= high:
                    raise ValueError('invalid cursor watermarks')
                if self._anchor(db, table, after) != cursor['prefix'] or self._anchor(db, table, high) != cursor['high_prefix']:
                    raise ValueError('journal cursor history replaced')
            items = self._items(db, table, after, limit, max_bytes)
            position = items[-1]['sequence'] if items else after
            high = db.execute(f'SELECT COALESCE(MAX(sequence),0) FROM {table}').fetchone()[0]
            next_cursor = {'schema': 1, 'scope': self.scope, 'journal_id': identity, 'file_id': file_id,
                           'stream': table, 'sequence': position, 'prefix': self._anchor(db, table, position),
                           'high_water': high, 'high_prefix': self._anchor(db, table, high)}
            return {'items': items, 'cursor': next_cursor}

    def read_outcomes_page(self, *, cursor=None, limit=16, max_bytes=262144):
        return self._fenced_page('outcomes', cursor, limit, max_bytes)

    def read_source_links_page(self, *, cursor=None, limit=16, max_bytes=262144):
        return self._fenced_page('source_links', cursor, limit, max_bytes)

    def read_outcomes(self, *, after_sequence=0, limit=16, max_bytes=262144):
        return self._page('outcomes', after_sequence, limit, max_bytes)

    def read_source_links(self, *, after_sequence=0, limit=16, max_bytes=262144):
        return self._page('source_links', after_sequence, limit, max_bytes)

    def read_run(self, run_id):
        with self._db() as db:
            row = db.execute('SELECT sequence,run_id,substr(payload,1,65537),prefix FROM outcomes WHERE run_id=?', (_text(run_id),)).fetchone()
            return self._row(db, 'outcomes', row) if row else None

    def latest_promotion(self):
        with self._db() as db:
            row = db.execute('SELECT sequence,run_id,substr(payload,1,65537),prefix FROM outcomes WHERE promotion IS NOT NULL ORDER BY promotion DESC LIMIT 1').fetchone()
            return self._row(db, 'outcomes', row) if row else None


class CoreJournal:
    def __init__(self, publication):
        self.publication = publication
        self.scope = publication.scope
        self.path = publication.data_root / 'portrait_sources' / self.scope / 'core_outcomes.sqlite'

    def _owned(self):
        from .publication import Publication, digest
        owner = self.publication
        if type(owner) is not Publication or owner.scope != digest([owner.instance_id, owner.source_id, owner.cdn_base, str(owner.data_root)]):
            raise ValueError('invalid Publication owner/domain')
        if owner._run_handle is None or owner._run_pid != os.getpid():
            raise ValueError('actual updater ownership required')
        handle_stat = os.fstat(owner._run_handle)
        path_stat = (owner.data_root / 'portrait_updater_owner.lock').stat()
        if (handle_stat.st_dev, handle_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
            raise ValueError('ownership descriptor mismatch')

    @contextmanager
    def _db(self):
        from .source_io import _directory, _sync_directory, _unsafe
        self._owned()
        # SourceIO heals every ancestor entry even when a previous mkdir/flush failed.
        with _directory(self.publication.data_root, f'portrait_sources/{self.scope}', create=True) as directory:
            for suffix in ('', '-wal', '-shm'):
                try:
                    if _unsafe(Path(str(self.path)+suffix).lstat()):
                        raise ValueError('unsafe core journal path')
                except FileNotFoundError:
                    pass
            db = sqlite3.connect(self.path, timeout=5)
            try:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('PRAGMA synchronous=FULL')
                db.execute('BEGIN IMMEDIATE')
                db.execute('CREATE TABLE IF NOT EXISTS identity(schema INTEGER,scope TEXT,journal_id TEXT)')
                if not db.execute('SELECT 1 FROM identity').fetchone():
                    db.execute('INSERT INTO identity VALUES(2,?,?)', (self.scope, str(uuid.uuid4())))
                if db.execute('SELECT schema,scope FROM identity').fetchall() != [(2, self.scope)]:
                    raise ValueError('journal scope/schema mismatch')
                _journal_identity(db.execute('SELECT journal_id FROM identity').fetchone()[0])
                db.execute('CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS outcomes(sequence INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT UNIQUE,promotion INTEGER UNIQUE,payload TEXT NOT NULL,prefix TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS source_links(sequence INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT UNIQUE,payload TEXT NOT NULL,prefix TEXT NOT NULL)')
                yield db
                # DB/WAL entries exist now, but the terminal row is still uncommitted.
                # No reader can observe success before this barrier passes.
                _sync_directory(directory)
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def begin_run(self, run_id, *, trigger):
        value = {'run_id': _text(run_id), 'trigger': _text(trigger), 'scope': self.scope,
                     'version': None, 'classification': 'unknown', 'announcement_eligible': False}
        with self._db() as db:
            old = db.execute('SELECT payload FROM runs WHERE run_id=?', (run_id,)).fetchone()
            if old:
                if _decode(old[0])['trigger'] != trigger:
                    raise ValueError('run identity conflict')
            else:
                db.execute('INSERT INTO runs VALUES(?,?)', (run_id, _encode(value)))
        return CoreRunContext(self, run_id)

    def recover(self):
        # Bounded batches, each closed independently; never consult VERSION proofs.
        while True:
            with self._db() as db:
                rows = db.execute('SELECT run_id FROM runs WHERE run_id NOT IN (SELECT run_id FROM outcomes) LIMIT 100').fetchall()
            if not rows:
                return
            for (run_id,) in rows:
                CoreRunContext(self, run_id).finish(phase='interrupted', source_state='not_requested', code='run_interrupted')

    def _link(self, run_id, value):
        with self._db() as db:
            outcome = db.execute('SELECT payload FROM outcomes WHERE run_id=?', (run_id,)).fetchone()
            if not outcome or _decode(outcome[0])['phase'] not in ('published', 'no_change'):
                raise ValueError('source link requires completed core')
            value = dict(value, run_id=run_id, scope=self.scope, promotion=_decode(outcome[0])['promotion'])
            _validate(value, self.scope, run_id, 'source_links')
            raw = _encode(value)
            old = db.execute('SELECT payload FROM source_links WHERE run_id=?', (run_id,)).fetchone()
            if old and old[0] != raw:
                raise ValueError('source resolution conflict')
            if not old:
                _append(db, 'source_links', run_id, raw)

    def fail_source(self, run_id, *, code):
        if not re.fullmatch('[a-z_]{1,64}', code):
            raise ValueError('invalid diagnostic code')
        self._link(run_id, {'state': 'failed', 'code': code})

    def link_source(self, run_id, ref):
        request = self.publication.read_request(ref)
        outcome = CoreReader(self.path, scope=self.scope).read_run(run_id)
        if outcome is None or str(request['version']) != outcome['version']:
            raise ValueError('source version mismatch')
        if outcome['pin_digest'] is not None and outcome['pin_digest'] != ref.pin_digest:
            raise ValueError('source pin mismatch')
        self._link(run_id, {'state': 'ready', 'request_id': request['request_id'], 'ref': asdict(ref),
                               'pin_digest': ref.pin_digest, 'cards': request['cards']})


class CoreRunContext:
    def __init__(self, journal, run_id):
        self.journal, self.run_id = journal, run_id

    @contextmanager
    def activate(self):
        self.journal._owned()
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)

    def target_version(self):
        with self.journal._db() as db:
            return _decode(db.execute('SELECT payload FROM runs WHERE run_id=?', (self.run_id,)).fetchone()[0])['version']

    def bind_target(self, version, *, classification, announcement_eligible):
        if classification not in ('processed', 'noop', 'bootstrap'):
            raise ValueError('invalid classification')
        with self.journal._db() as db:
            value = _decode(db.execute('SELECT payload FROM runs WHERE run_id=?', (self.run_id,)).fetchone()[0])
            new = dict(value, version=_text(str(version)), classification=classification,
                       announcement_eligible=bool(announcement_eligible))
            if value['version'] is not None and value != new:
                raise ValueError('target already bound')
            if db.execute('SELECT 1 FROM outcomes WHERE run_id=?', (self.run_id,)).fetchone():
                raise ValueError('run already closed')
            db.execute('UPDATE runs SET payload=? WHERE run_id=?', (_encode(new), self.run_id))

    def finish(self, *, phase, source_state, counts=None, warnings=(), cards=None, pin_digest=None, code=None, bootstrap_ref=None):
        if phase not in ('published', 'failed', 'interrupted', 'no_change') or source_state not in ('pending', 'failed', 'not_requested'):
            raise ValueError('invalid terminal state')
        bootstrap_request = self.journal.publication.read_request(bootstrap_ref) if bootstrap_ref is not None else None
        counts = counts or {}
        if any(type(v) is not int or v < 0 for v in counts.values()):
            raise ValueError('invalid counts')
        with self.journal._db() as db:
            value = _decode(db.execute('SELECT payload FROM runs WHERE run_id=?', (self.run_id,)).fetchone()[0])
            old = db.execute('SELECT sequence,substr(payload,1,65537) FROM outcomes WHERE run_id=?', (self.run_id,)).fetchone()
            promotion = _decode(old[1])['promotion'] if old else None
            if bootstrap_request is not None and str(bootstrap_request['version']) != value['version']:
                raise ValueError('bootstrap target mismatch')
            bootstrap = (bootstrap_request is not None and phase == 'no_change' and value['classification'] == 'bootstrap' and source_state == 'pending'
                         and not db.execute('SELECT 1 FROM outcomes WHERE promotion IS NOT NULL LIMIT 1').fetchone())
            if not old and (phase == 'published' or bootstrap):
                if value['version'] is None:
                    raise ValueError('published run requires target')
                promotion = db.execute('SELECT COALESCE(MAX(promotion),0)+1 FROM outcomes').fetchone()[0]
            value.update(phase=phase, source_state=source_state, counts=counts, warnings=list(warnings)[:5],
                         cards=cards or {'kind': 'handoff_unresolved'}, pin_digest=pin_digest,
                         code=code, promotion=promotion)
            _validate(value, self.journal.scope, self.run_id, 'outcomes')
            raw = _encode(value)
            if old:
                if old[1] != raw:
                    raise ValueError('terminal outcome conflict')
                sequence = old[0]
            else:
                sequence = _append(db, 'outcomes', self.run_id, raw, promotion)
            return dict(value, sequence=sequence)
