"""Bounded, immutable source handoff. Capture is never publication authority.

Only trusted updater code constructs this service. No preparation, queue, or native
imports. All locators are beneath the configured data root; versions are exact.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from ..cdn import validate_remote_name
from .model import digest
from .source_io import (
    MetadataRef,
    _directory,
    _pairs,
    _partial,
    _publish,
    _published_file,
    _read_file,
    pin_source,
    read_metadata,
    store_metadata,
)

MAX_RECORD = 1024 * 1024


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _version(value):
    value = str(value)
    if not re.fullmatch(r'[1-9][0-9]{0,63}', value):
        raise ValueError('invalid exact version')
    return value


def _json(raw):
    value = json.loads(raw, object_pairs_hook=_pairs)
    if type(value) is not dict:
        raise ValueError('object required')
    return value


@dataclass(frozen=True)
class RequestEntry:
    sequence: int
    version: str
    ref: MetadataRef


class Publication:
    def __init__(self, data_root, *, instance_id, source_id, cdn_base):
        self.data_root = Path(os.path.abspath(data_root))
        self.root = self.data_root / 'portrait_sources'
        self.instance_id, self.source_id = instance_id, source_id
        self.cdn_base = cdn_base.rstrip('/')
        self.scope = digest([instance_id, source_id, self.cdn_base, str(self.data_root)])
        self._run_handle = None
        self._run_pid = None

    def _read(self, root, relative, limit=MAX_RECORD):
        parent, name = relative.rsplit('/', 1) if '/' in relative else ('', relative)
        with _directory(root, parent) as directory, _read_file(directory, name) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('oversized publication input')
        return raw

    def _record(self, kind, version):
        return _json(self._read(self.root, f'{self.scope}/{kind}/{_version(version)}.json'))

    def _put(self, kind, version, value):
        from .source_io import _sync_directory
        raw = _bytes(value)
        if len(raw) > MAX_RECORD:
            raise ValueError('oversized publication descriptor')
        with _directory(self.root, f'{self.scope}/{kind}', create=True) as directory:
            name = _version(version) + '.json'
            try:
                with _read_file(directory, name) as stream:
                    if stream.read(MAX_RECORD+1) != raw:
                        raise ValueError('immutable publication conflict')
                # An earlier install may have become visible before its final
                # directory fsync failed. Matching bytes alone do not heal it.
                _sync_directory(directory)
                return
            except FileNotFoundError:
                pass
            with _partial(directory) as (partial, output):
                output.write(raw)
                installed = _publish(directory, partial, name, output)
                with _published_file(directory, name, output, installed) as stream:
                    if stream.read(MAX_RECORD+1) != raw:
                        raise ValueError('immutable publication conflict')

    @contextmanager
    def _index(self, *, write=False):
        from .source_io import _sync_directory, _unsafe
        with _directory(self.root, self.scope, create=write) as directory:
            path = directory[0] / 'discovery.sqlite3'
            try:
                if _unsafe(path.lstat()):
                    raise ValueError('unsafe discovery index')
            except FileNotFoundError:
                if not write:
                    raise
            uri = path.as_uri() + ('?mode=rwc' if write else '?mode=ro')
            connection = sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)
            try:
                if write:
                    connection.execute('PRAGMA synchronous=FULL')
                    connection.execute('BEGIN IMMEDIATE')
                    connection.execute('CREATE TABLE IF NOT EXISTS identity(scope TEXT PRIMARY KEY)')
                    connection.execute('CREATE TABLE IF NOT EXISTS pending(sequence INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT UNIQUE, payload TEXT, state TEXT NOT NULL)')
                    connection.execute('CREATE INDEX IF NOT EXISTS pending_state ON pending(state,sequence)')
                    connection.execute('CREATE TABLE IF NOT EXISTS ready(sequence INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT UNIQUE, reference TEXT NOT NULL)')
                    connection.execute('INSERT OR IGNORE INTO identity VALUES(?)', (self.scope,))
                else:
                    connection.execute('PRAGMA query_only=ON')
                    connection.execute('BEGIN')
                if connection.execute('SELECT scope FROM identity LIMIT 2').fetchall() != [(self.scope,)]:
                    raise ValueError('discovery index scope mismatch')
                yield connection
                connection.commit()
                if write:
                    _sync_directory(directory)
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _register_pin(self, version, record):
        payload = _bytes(record).decode()
        with self._index(write=True) as db:
            existing = db.execute('SELECT payload FROM pending WHERE version=?', (version,)).fetchone()
            if existing and existing[0] not in (None, payload):
                raise ValueError('indexed pin conflict')
            db.execute("INSERT OR IGNORE INTO pending(version,payload,state) VALUES(?,?,'pending')", (version, payload))
            db.execute("UPDATE pending SET payload=?,state='pending' WHERE version=? AND payload IS NULL", (payload, version))

    def _register_ready(self, version, ref):
        request = self.read_request(ref)
        if request['version'] != _version(version):
            raise ValueError('ready registration version mismatch')
        # Verify immutable content first, then heal each record's publication
        # barrier before the durable discovery index can advertise authority.
        for kind, key in (('pins', 'pin'), ('proofs', 'core'), ('cards', 'cards')):
            self._put(kind, version, request[key])
        self._put('requests', version, asdict(ref))
        encoded = _bytes(asdict(ref)).decode()
        with self._index(write=True) as db:
            old = db.execute('SELECT reference FROM ready WHERE version=?', (version,)).fetchone()
            if old and old[0] != encoded:
                raise ValueError('indexed request conflict')
            db.execute('INSERT OR IGNORE INTO ready(version,reference) VALUES(?,?)', (version, encoded))
            db.execute("UPDATE pending SET state='ready' WHERE version=?", (version,))

    def _store(self, raw, pin_digest):
        return store_metadata(raw, data_root=self.root, scope_key=self.scope, pin_digest=pin_digest)

    def capture(self, raw, *, version, version_file, bootstrap=True):
        pin = pin_source(raw, instance_id=self.instance_id, source_id=self.source_id,
                         cdn_base=self.cdn_base, version=_version(version), version_file=version_file)
        try:
            existing = self._record('pins', version)
        except FileNotFoundError:
            pass
        else:
            if existing['pin_digest'] != pin.pin_digest:
                raise ValueError('immutable source pin conflict')
        ref = self._store(raw, pin.pin_digest)
        record = {'schema': 1, 'scope': self.scope, 'version': pin.version,
                  'bootstrap': bootstrap, 'version_file': version_file, 'pin_digest': pin.pin_digest, 'raw': asdict(ref)}
        self._register_pin(pin.version, record)
        self._put('pins', version, record)
        return pin

    def check_existing_core(self, version):
        """Check bootstrap authority before any additional CDN acquisition."""
        version = _version(version)
        try:
            pointer = _json(self._read(self.data_root, 'current_version.json'))
        except FileNotFoundError:
            paths = [('character_data/current.json', True)]
        else:
            if str(pointer.get('version')) != version:
                raise ValueError('current core mismatch')
            paths = [(f'versions/{version}/manifest.json', False),
                     (f'versions/{version}/character_data/current.json', True)]
        for path, snapshot in paths:
            payload = _json(self._read(self.data_root, path, 32*1024**2))
            if str(payload.get('version')) != version:
                raise ValueError('existing core version mismatch')
            if snapshot and (type(payload.get('characters')) is not dict or not payload['characters'] or
                             type(payload.get('index')) is not list or type(payload.get('words')) is not dict):
                raise ValueError('invalid existing core schema')

    def read_failure(self, version):
        value = self._record('failures', version)
        if value.get('scope') != self.scope or value.get('version') != _version(version):
            raise ValueError('failure scope mismatch')
        return value

    def load_pin(self, version, *, recover=False):
        try:
            record = self._record('pins', version)
        except FileNotFoundError:
            with self._index() as db:
                row = db.execute('SELECT payload FROM pending WHERE version=?', (_version(version),)).fetchone()
            if not row or row[0] is None:
                raise FileNotFoundError('source pin missing') from None
            record = _json(row[0])
        if record['scope'] != self.scope or record['version'] != _version(version):
            raise ValueError('pin scope mismatch')
        ref = MetadataRef(**record['raw'])
        if ref.scope_key != self.scope or ref.pin_digest != record['pin_digest']:
            raise ValueError('raw metadata scope mismatch')
        raw = read_metadata(ref, data_root=self.root)
        pin = pin_source(raw, instance_id=self.instance_id, source_id=self.source_id,
                         cdn_base=self.cdn_base, version=record['version'], version_file=record['version_file'])
        if pin.pin_digest != record['pin_digest']:
            raise ValueError('pin identity mismatch')
        if recover:
            self._put('pins', version, record)
        return pin

    def failure(self, version, code):
        if code not in ('invalid-pin', 'invalid-core', 'missing-pin', 'invalid-request', 'card-conflict'):
            raise ValueError('unknown failure code')
        self._put('failures', version, {'schema': 1, 'scope': self.scope,
                  'version': _version(version), 'state': 'failed', 'code': code})
        with self._index(write=True) as db:
            db.execute("INSERT OR IGNORE INTO pending(version,state) VALUES(?,'failed')", (_version(version),))
            db.execute("UPDATE pending SET state='failed' WHERE version=? AND state!='ready'", (_version(version),))

    def stage_cards(self, version, *, reason, manifest_bytes=None, expected_batch=False):
        version = _version(version)
        pin = self.load_pin(version)
        try:
            existing = self._record('cards', version)
        except FileNotFoundError:
            existing = None
        if existing is not None and manifest_bytes is None:
            return existing
        try:
            raw = manifest_bytes if manifest_bytes is not None else self._read(self.data_root, f'outbox/{version}/manifest.json')
        except FileNotFoundError:
            if expected_batch:
                raise ValueError('missing required card evidence')
            if reason not in ('baseline', 'disabled-cards', 'no-new-output', 'bootstrap-history-unavailable'):
                raise ValueError('invalid no-card reason')
            cards = {'kind': 'none', 'reason': reason}
        else:
            manifest = _json(raw)
            if str(manifest.get('version')) != version or type(manifest.get('characters')) is not list:
                raise ValueError('invalid card manifest')
            if not 0 < len(manifest['characters']) <= 1000:
                raise ValueError('invalid card batch size')
            files, keys = [], []
            for item in manifest['characters']:
                name, cid = item['filename'], item['character_id']
                if type(name) is not str or '/' in name or not name.endswith('.png') or type(cid) is not str or not cid.isdigit():
                    raise ValueError('invalid card identity')
                validate_remote_name(name)
                content = self._read(self.data_root, f'outbox/{version}/{name}', 32*1024**2)
                files.append({'path': f'outbox/{version}/{name}', 'sha256': hashlib.sha256(content).hexdigest()})
                keys.append(f'game-card:{version}:{cid}:{name}')
            if len(set(keys)) != len(keys):
                raise ValueError('duplicate card identity')
            if existing is not None:
                old_manifest = _json(self.frozen_card_manifest(version)) if existing['kind'] == 'batch' else {}
                old_manifest.pop('created_at', None)
                candidate = dict(manifest)
                candidate.pop('created_at', None)
                if existing['kind'] != 'batch' or candidate != old_manifest or files != existing['files'] or keys != existing['keys']:
                    self._put('card_conflicts', version, {'scope': self.scope, 'version': version, 'state': 'failed'})
                    self.failure(version, 'card-conflict')
                    raise ValueError('conflicting card replay')
                return existing
            cards = {'kind': 'batch', 'manifest': asdict(self._store(raw, pin.pin_digest)),
                     'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'keys': keys, 'files': files}
        self._put('cards', version, cards)
        return cards

    def frozen_card_manifest(self, version):
        cards = self._record('cards', version)
        if cards['kind'] != 'batch':
            raise ValueError('no frozen card batch')
        return read_metadata(MetadataRef(**cards['manifest']), data_root=self.root)

    def _check_card_conflict(self, version):
        try:
            self._record('card_conflicts', version)
        except FileNotFoundError:
            return
        raise ValueError('card evidence permanently conflicted')

    def _artifact(self, relative, pin, *, snapshot=False):
        raw = self._read(self.data_root, relative, 32*1024**2)
        payload = _json(raw)
        if str(payload.get('version')) != pin.version:
            raise ValueError('core version mismatch')
        if snapshot and (type(payload.get('characters')) is not dict or not payload['characters'] or
                         type(payload.get('index')) is not list or type(payload.get('words')) is not dict):
            raise ValueError('invalid core snapshot schema')
        return {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest(),
                'frozen': asdict(self._store(raw, pin.pin_digest))}

    def _prove(self, pin, schedule_enabled):
        version = pin.version
        try:
            pointer = _json(self._read(self.data_root, 'current_version.json'))
        except FileNotFoundError:
            snapshot = self._artifact('character_data/current.json', pin, snapshot=True)
            return {'schema': 1, 'scope': self.scope, 'version': pin.version,
                    'kind': 'legacy-snapshot', 'artifacts': [snapshot]}
        if str(pointer.get('version')) != version:
            raise ValueError('current core mismatch')
        snapshot = self._artifact(f'versions/{version}/character_data/current.json', pin, snapshot=True)
        mirror = self._artifact('character_data/current.json', pin, snapshot=True)
        if snapshot['sha256'] != mirror['sha256']:
            raise ValueError('character mirror mismatch')
        manifest = self._artifact(f'versions/{version}/manifest.json', pin)
        artifacts = [snapshot, manifest]
        # Optional catalog/schedule artifacts must have completed their configured mirror.
        for folder in ('gacha_config', 'rerun_schedule'):
            if folder == 'rerun_schedule' and not schedule_enabled:
                continue
            path = f'versions/{version}/{folder}/current.json'
            try:
                raw = self._read(self.data_root, path)
            except FileNotFoundError:
                continue
            try:
                mirror_raw = self._read(self.data_root, f'{folder}/current.json')
                outcome = 'matched' if raw == mirror_raw else 'last-known-good'
                mirror_sha = hashlib.sha256(mirror_raw).hexdigest()
            except (OSError, ValueError):
                outcome, mirror_sha = 'unavailable', None
            artifacts.append({'path': path, 'mirror_outcome': outcome, 'mirror_sha256': mirror_sha, 'sha256': hashlib.sha256(raw).hexdigest(),
                              'frozen': asdict(self._store(raw, pin.pin_digest))})
        return {'schema': 1, 'scope': self.scope, 'version': pin.version,
                'kind': 'published-version', 'artifacts': artifacts}

    def attest_published(self, version, *, schedule_enabled=False):
        """Trusted pipeline hook, called only after every required core phase returns.

        Recovery never calls this hook: normal pending work requires its durable
        proof. Matching intermediate files cannot substitute for this boundary.
        """
        pin = self.load_pin(version, recover=True)
        core = self._prove(pin, schedule_enabled)
        self._put('proofs', version, core)

    def _write_request(self, version, request, pin):
        ref = self._store(_bytes(request), pin.pin_digest)
        self._put('requests', version, asdict(ref))
        return ref

    def complete(self, version, *, schedule_enabled=False):
        version = _version(version)
        self._check_card_conflict(version)
        pin = self.load_pin(version, recover=True)
        try:
            ref = MetadataRef(**self._record('requests', version))
        except FileNotFoundError:
            pass
        else:
            self.read_request(ref)
            self._register_ready(version, ref)
            return ref
        try:
            core = self._record('proofs', version)
        except FileNotFoundError:
            if not self._record('pins', version).get('bootstrap'):
                raise ValueError('normal publication lacks completed pipeline proof') from None
            core = self._prove(pin, schedule_enabled)
            self._put('proofs', version, core)
        try:
            cards = self._record('cards', version)
        except FileNotFoundError:
            if not self._record('pins', version).get('bootstrap'):
                raise ValueError('missing prepublication card evidence')
            cards = self.stage_cards(version, reason='bootstrap-history-unavailable')
        request = {'schema': 1, 'scope': self.scope, 'instance_id': self.instance_id,
                   'source_id': self.source_id, 'cdn_base': self.cdn_base,
                   'data_root': str(self.data_root), 'version': version,
                   'pin': self._record('pins', version), 'core': core, 'cards': cards}
        request['request_id'] = digest(request)
        ref = self._write_request(version, request, pin)
        self.read_request(ref)
        self._register_ready(version, ref)
        return ref

    def read_request(self, ref):
        if type(ref) is RequestEntry:
            ref = ref.ref
        if type(ref) is not MetadataRef or ref.scope_key != self.scope or ref.size > MAX_RECORD:
            raise ValueError('request scope mismatch')
        request = _json(read_metadata(ref, data_root=self.root))
        identity = request.pop('request_id')
        if identity != digest(request) or request['scope'] != self.scope or request['schema'] != 1:
            raise ValueError('request identity mismatch')
        if any(request.get(key) != value for key, value in (('instance_id', self.instance_id),
                ('source_id', self.source_id), ('cdn_base', self.cdn_base), ('data_root', str(self.data_root)))):
            raise ValueError('request configured identity mismatch')
        self._check_card_conflict(request['version'])
        pin = self.load_pin(request['version'])
        if ref.pin_digest != pin.pin_digest or request['pin'] != self._record('pins', pin.version):
            raise ValueError('request pin mismatch')
        core = request['core']
        if core.get('schema') != 1 or core.get('scope') != self.scope or core.get('version') != pin.version or core.get('kind') not in ('legacy-snapshot', 'published-version'):
            raise ValueError('invalid scoped core proof')
        expected_paths = ({'character_data/current.json'} if core['kind'] == 'legacy-snapshot' else
                          {f'versions/{pin.version}/character_data/current.json', f'versions/{pin.version}/manifest.json'})
        actual_paths = {item['path'] for item in core['artifacts']}
        if not expected_paths <= actual_paths or len(core['artifacts']) > 4:
            raise ValueError('incomplete core proof')
        if request['core'] != self._record('proofs', pin.version):
            raise ValueError('request proof mismatch')
        if request['cards'] != self._record('cards', pin.version):
            raise ValueError('request cards mismatch')
        for artifact in request['core']['artifacts']:
            frozen = MetadataRef(**artifact['frozen'])
            if frozen.scope_key != self.scope or frozen.pin_digest != pin.pin_digest:
                raise ValueError('core metadata scope mismatch')
            raw = read_metadata(frozen, data_root=self.root)
            if artifact['path'] in expected_paths and str(_json(raw).get('version')) != pin.version:
                raise ValueError('frozen core version mismatch')
            if hashlib.sha256(raw).hexdigest() != artifact['sha256']:
                raise ValueError('core evidence corrupt')
            if request['core']['kind'] == 'published-version' and raw != self._read(self.data_root, artifact['path'], 32*1024**2):
                raise ValueError('published core changed')
        if request['cards']['kind'] == 'batch':
            raw = read_metadata(MetadataRef(**request['cards']['manifest']), data_root=self.root)
            if hashlib.sha256(raw).hexdigest() != request['cards']['manifest_sha256']:
                raise ValueError('card evidence corrupt')
        request['request_id'] = identity
        return request

    @staticmethod
    def _page(after, limit):
        if type(limit) is not int or not 1 <= limit <= 100 or type(after) is not int or after < 0:
            raise ValueError('invalid bounded page')

    def list_requests(self, *, after=0, limit=100):
        """Ready sequence, never game-version order; late recovery remains visible."""
        self._page(after, limit)
        try:
            with self._index() as db:
                rows = db.execute('SELECT sequence,version,reference FROM ready WHERE sequence>? ORDER BY sequence LIMIT ?', (after, limit)).fetchall()
        except FileNotFoundError:
            return []
        result = []
        for sequence, version, encoded in rows:
            ref = MetadataRef(**_json(encoded))
            request = self.read_request(ref)
            if request['version'] != version:
                raise ValueError('indexed request version mismatch')
            result.append(RequestEntry(sequence, version, ref))
        return result

    def reconcile(self, *, after=0, limit=10, schedule_enabled=False):
        """Independent pending-registration cursor; callers may retry from zero."""
        self._page(after, limit)
        try:
            with self._index() as db:
                rows = db.execute("SELECT sequence,version FROM pending WHERE state='pending' AND sequence>? ORDER BY sequence LIMIT ?", (after, limit)).fetchall()
        except FileNotFoundError:
            return []
        outcomes = []
        for sequence, version in rows:
            try:
                self.load_pin(version, recover=True)
            except (ValueError, KeyError, TypeError):
                self.failure(version, 'invalid-pin')
                outcomes.append((sequence, int(version), 'failed'))
                continue
            except FileNotFoundError:
                self.failure(version, 'missing-pin')
                outcomes.append((sequence, int(version), 'failed'))
                continue
            except OSError:
                outcomes.append((sequence, int(version), 'retry-storage'))
                continue
            try:
                self.complete(version, schedule_enabled=schedule_enabled)
                state = 'ready'
            except FileNotFoundError:
                state = 'retry-missing-core'
            except (ValueError, KeyError, TypeError):
                self.failure(version, 'invalid-core')
                state = 'failed'
            except OSError:
                state = 'retry-storage'
            outcomes.append((sequence, int(version), state))
        return outcomes

    def acquire_run(self):
        """Nonblocking OS ownership shared by every updater on this data root."""
        from .source_io import _unsafe
        if self._run_handle is not None:
            return False
        with _directory(self.data_root, create=True) as directory:
            path = directory[0] / 'portrait_updater_owner.lock'
            try:
                if _unsafe(path.lstat()):
                    raise ValueError('unsafe updater ownership path')
            except FileNotFoundError:
                pass
            fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                os.close(fd)
                import errno
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    return False
                raise
            try:
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b'0')
                    os.fsync(fd)
            except BaseException:
                os.close(fd)
                raise
            self._run_handle, self._run_pid = fd, os.getpid()
        return True

    def release_run(self):
        if self._run_handle is None or self._run_pid != os.getpid():
            raise ValueError('updater ownership not held')
        fd = self._run_handle
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            self._run_handle, self._run_pid = None, None

    @property
    def guard_path(self):
        return self.data_root / 'portrait_updater_guard.json'

    def set_guard(self, busy):
        """Only the OS owner may change guard/idle state; no lifecycle event."""
        if self._run_handle is None or self._run_pid != os.getpid():
            raise ValueError("updater ownership not held")
        import tempfile

        from .source_io import _sync_directory, _unsafe
        payload = {'schema': 1, 'scope': self.scope, 'updating': bool(busy)}
        with _directory(self.data_root, create=True) as directory:
            try:
                if _unsafe(self.guard_path.lstat()):
                    raise ValueError('unsafe updater guard')
            except FileNotFoundError:
                pass
            with tempfile.NamedTemporaryFile(dir=directory[0], prefix='.guard-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(_bytes(payload))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.guard_path)
            _sync_directory(directory)
            if not busy:
                # Preserve last error/version; repair only the known idle boolean.
                try:
                    status = _json(self._read(self.data_root, 'update_status.json', 16384))
                except (OSError, ValueError):
                    status = {}
                status['updating'] = False
                target = self.data_root / 'update_status.json'
                with tempfile.NamedTemporaryFile(dir=directory[0], prefix='.idle-', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(_bytes(status))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                _sync_directory(directory)
