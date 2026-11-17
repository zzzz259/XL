"""Seal one mode+season of the season history into a verified tar.gz package.

Builds into a temporary directory, checks disk headroom, then verifies SHA256
digests, per-table counts, and SQLite integrity before re-opening the packaged
database and querying rankings, membership, lineup, presence, and battleUids.
Only a fully verified package is recorded as ARCHIVE_SEALED in the global index
and followed by a transactional purge of exactly that season's runtime rows.
Any failure keeps all runtime data and cleans up every temporary artifact.

Standard library only; no network and no access outside ``packages_dir``.
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import leaderboard_seasons

MODES = frozenset({'guild_war', 'illusion', 'nightmare'})
INDEX_NAME = 'index.jsonl'
PACKAGE_FORMAT = 'season-archive/1'
PACKAGE_NAME = re.compile(r'season__[A-Za-z0-9_.-]+__[A-Za-z0-9_]+__[A-Za-z0-9_.-]+\.tar\.gz')
SHA256_HEX = re.compile(r'[0-9a-f]{64}')
SCHEMA_VERSION = leaderboard_seasons.SCHEMA_VERSION


class SeasonPackageError(Exception):
    """Sealing or opening a season package failed; runtime data is untouched."""


def _utcnow():
    return datetime.now(UTC).isoformat()


def _strict_identity(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f'Invalid {label} identity')
    if '\x00' in value or '..' in value or '/' in value or '\\' in value:
        raise ValueError(f'Unsafe {label} identity')
    return value


def _slug(value):
    slug = re.sub(r'[^A-Za-z0-9_.-]', '_', value)
    if not slug or slug in ('.', '..'):
        raise ValueError('Unsafe package name component')
    return slug


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _read_index(packages_dir):
    entries = {}
    path = Path(packages_dir) / INDEX_NAME
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line:
                entry = json.loads(line)
                entries[(entry['scope'], entry['mode'], entry['season_id'])] = entry
    return entries


class SeasonPackager:
    """Seal seasons from a :class:`leaderboard_seasons.SeasonHistory` into packages_dir."""

    def __init__(self, history, packages_dir):
        self.history = history
        self.packages_dir = Path(packages_dir)
        self.packages_dir.mkdir(parents=True, exist_ok=True)

    def lookup(self, scope, mode, season_id):
        return _read_index(self.packages_dir).get((scope, mode, season_id))

    def list_sealed(self):
        return sorted(_read_index(self.packages_dir).values(),
                      key=lambda entry: (entry['scope'], entry['mode'], entry['season_id']))

    def _write_index(self, entries):
        path = self.packages_dir / INDEX_NAME
        tmp = self.packages_dir / f'.{INDEX_NAME}.{uuid.uuid4().hex}.tmp'
        payload = ''.join(json.dumps(entry, ensure_ascii=False, sort_keys=True) + '\n'
                          for entry in sorted(entries.values(), key=lambda item: (
                              item['scope'], item['mode'], item['season_id'])))
        tmp.write_text(payload, encoding='utf-8')
        os.replace(tmp, path)

    def _append_index(self, entry):
        entries = _read_index(self.packages_dir)
        entries[(entry['scope'], entry['mode'], entry['season_id'])] = entry
        self._write_index(entries)

    def _remove_index(self, scope, mode, season_id):
        entries = _read_index(self.packages_dir)
        if entries.pop((scope, mode, season_id), None) is not None:
            self._write_index(entries)

    def _verify_package(self, package_dir, manifest, counts):
        """Hash every file, check SQLite integrity and counts, then reopen and query."""
        for name, info in manifest['files'].items():
            target = package_dir / name
            if not target.is_file():
                raise SeasonPackageError('Missing package file: ' + name)
            if _sha256_file(target) != info['sha256']:
                raise SeasonPackageError('Digest mismatch: ' + name)
        db = sqlite3.connect(package_dir / 'season.sqlite3')
        db.row_factory = sqlite3.Row
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise SeasonPackageError('Packaged database failed integrity_check')
            if db.execute('PRAGMA foreign_key_check').fetchall():
                raise SeasonPackageError('Packaged database failed foreign_key_check')
            for table, expected in counts.items():
                actual = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                if actual != expected:
                    raise SeasonPackageError(f'Row count mismatch for {table}')
            scope = manifest['scope']
            mode = manifest['mode']
            season_id = manifest['season_id']
            ranking = db.execute('SELECT count(*) FROM rank_entries').fetchone()[0]
            lineup = db.execute('SELECT count(*) FROM lineup').fetchone()[0]
            presence = db.execute('SELECT count(*) FROM presence').fetchone()[0]
            if (ranking, lineup, presence) != (counts['rank_entries'], counts['lineup'],
                                               counts['presence']):
                raise SeasonPackageError('Reopened ranking queries mismatch')
            packaged = [row[0] for row in db.execute('SELECT battle_uid FROM battles ORDER BY battle_uid')]
            runtime = [row['battle_uid'] for row in self.history.list_battles(scope, mode, season_id)]
            if packaged != runtime:
                raise SeasonPackageError('Packaged battleUids mismatch runtime season')
            packaged_history = leaderboard_seasons.compute_member_history(db, scope, mode, season_id)
            runtime_history = self.history.member_history(scope, mode, season_id)
            if packaged_history != runtime_history:
                raise SeasonPackageError('Packaged member history mismatch runtime season')
        finally:
            db.close()

    def _verify_tar(self, tar_path, manifest):
        with tarfile.open(tar_path, 'r:gz') as tar:
            names = set(tar.getnames())
            expected = set(manifest['files']) | {'manifest.json'}
            if names != expected:
                raise SeasonPackageError('Package member list mismatch')
            sqlite_bytes = tar.extractfile('season.sqlite3').read()
            if hashlib.sha256(sqlite_bytes).hexdigest() != manifest['files']['season.sqlite3']['sha256']:
                raise SeasonPackageError('Packaged database digest mismatch')
            if json.loads(tar.extractfile('manifest.json').read()) != manifest:
                raise SeasonPackageError('Packaged manifest mismatch')

    def seal_season(self, scope, mode, season_id, *, min_free_bytes=None):
        """Build, verify, and seal one season package; purge runtime rows only on success.

        sealed_at_utc acts as the season write-lock: it is set before export and
        released only on failure, so no collector write can land between
        verification and purge. A mark without an index entry (crashed seal) is
        taken over; an index entry without runtime rows is an idempotent re-seal;
        an index entry with runtime rows is finished by purging only when the
        current season counts exactly match the sealed manifest counts, otherwise
        the runtime holds data the package does not cover and everything is kept.
        An orphaned package file is replaced, never the runtime data.
        """
        scope = _strict_identity(scope, 'scope')
        if mode not in MODES:
            raise ValueError('Unsupported mode')
        season_id = leaderboard_seasons._season_id(_strict_identity(season_id, 'season'))
        key = (scope, mode, season_id)
        entry = self.lookup(scope, mode, season_id)
        status = self.history.season_status(scope, mode, season_id)
        if entry is not None:
            if status is None:
                return entry
            current = self.history.season_counts(scope, mode, season_id)
            if current != entry['counts']:
                raise SeasonPackageError(
                    f'Runtime season has data not covered by the sealed package: {key!r}')
            token = self.history.mark_sealed(scope, mode, season_id)
            try:
                purge_result = self.history.purge_season(scope, mode, season_id)
            except Exception as exc:
                try:
                    self.history.unseal_season(scope, mode, season_id, token)
                except KeyError:
                    pass
                raise SeasonPackageError('Runtime season purge failed during seal recovery') from exc
            entry = dict(entry, space_reclaimed=bool(purge_result['vacuumed']))
            self._append_index(entry)
            return entry
        if status is None:
            raise SeasonPackageError(f'Unknown season: {key!r}')
        counts = self.history.season_counts(scope, mode, season_id)
        if counts['runs'] == 0:
            raise SeasonPackageError(f'Season has no runs to seal: {key!r}')
        blob_stats = self.history.season_blob_stats(scope, mode, season_id)
        estimate = blob_stats['bytes'] + 4 * 1024 * 1024
        free = shutil.disk_usage(self.packages_dir).free
        required = estimate * 2 + 1024 * 1024
        if min_free_bytes is not None:
            required = max(required, min_free_bytes)
        if free < required:
            raise SeasonPackageError(f'Insufficient disk space for sealing: {free} < {required}')
        final = self.packages_dir / f'season__{_slug(scope)}__{mode}__{_slug(season_id)}.tar.gz'
        if final.exists():
            if not PACKAGE_NAME.fullmatch(final.name):
                raise SeasonPackageError('Refusing to replace an existing package: ' + final.name)
            # Orphan from a seal that crashed between package replace and index
            # replace; the deterministic name makes it safe to rebuild.
            final.unlink()
        token = self.history.mark_sealed(scope, mode, season_id)
        purged = False
        tmp_dir = Path(tempfile.mkdtemp(prefix='.seal-', dir=self.packages_dir))
        tmp_tar = self.packages_dir / f'.seal-{uuid.uuid4().hex}.tar.gz'
        try:
            locked_counts = self.history.season_counts(scope, mode, season_id)
            if locked_counts != counts:
                raise SeasonPackageError(f'Season changed while acquiring the seal lock: {key!r}')
            self.history.export_season(scope, mode, season_id, tmp_dir / 'season.sqlite3')
            blobs_dir = tmp_dir / 'blobs'
            blobs_dir.mkdir()
            files = {}
            blob_names = []
            for sha, data in self.history.iter_season_blobs(scope, mode, season_id):
                (blobs_dir / (sha + '.pb')).write_bytes(data)
                files[f'blobs/{sha}.pb'] = {'sha256': sha, 'bytes': len(data)}
                blob_names.append(sha)
            if len(blob_names) != blob_stats['count']:
                raise SeasonPackageError(f"Blob count changed while sealing: {len(blob_names)} != {blob_stats['count']}")
            sqlite_bytes = (tmp_dir / 'season.sqlite3').stat().st_size
            files['season.sqlite3'] = {'sha256': _sha256_file(tmp_dir / 'season.sqlite3'),
                                       'bytes': sqlite_bytes}
            manifest = {'format': PACKAGE_FORMAT, 'schema_version': SCHEMA_VERSION,
                        'scope': scope, 'mode': mode, 'season_id': season_id,
                        'sealed_at_utc': _utcnow(), 'counts': counts, 'files': files}
            (tmp_dir / 'manifest.json').write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                encoding='utf-8')
            self._verify_package(tmp_dir, manifest, counts)
            with tarfile.open(tmp_tar, 'w:gz') as tar:
                tar.add(tmp_dir / 'season.sqlite3', arcname='season.sqlite3')
                tar.add(tmp_dir / 'manifest.json', arcname='manifest.json')
                for sha in blob_names:
                    tar.add(blobs_dir / (sha + '.pb'), arcname=f'blobs/{sha}.pb')
            self._verify_tar(tmp_tar, manifest)
            os.replace(tmp_tar, final)
            entry = {'scope': scope, 'mode': mode, 'season_id': season_id,
                     'package': final.name, 'sha256': _sha256_file(final),
                     'bytes': final.stat().st_size, 'sealed_at_utc': manifest['sealed_at_utc'],
                     'status': 'ARCHIVE_SEALED', 'counts': counts}
            self._append_index(entry)
            try:
                purge_result = self.history.purge_season(scope, mode, season_id)
                purged = True
            except Exception as exc:
                # The delete transaction failed: the package is rolled back so no
                # verified artifact is kept while runtime data survives.
                self._remove_index(scope, mode, season_id)
                final.unlink(missing_ok=True)
                raise SeasonPackageError('Runtime season purge failed after sealing') from exc
            # Rows are gone and the package is verified; a vacuum failure here
            # must never roll back the package or index — only the reclaimed
            # space is lost, so the outcome is recorded instead.
            entry = dict(entry, space_reclaimed=bool(purge_result['vacuumed']))
            self._append_index(entry)
            return entry
        except SeasonPackageError:
            raise
        except Exception as exc:
            if final.exists() and key not in _read_index(self.packages_dir):
                final.unlink(missing_ok=True)
            raise SeasonPackageError(f'Season sealing failed: {type(exc).__name__}') from exc
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_tar.unlink(missing_ok=True)
            if not purged:
                try:
                    self.history.unseal_season(scope, mode, season_id, token)
                except KeyError:
                    pass

    def open_season(self, scope, mode, season_id):
        return SeasonPackage.open(self.packages_dir, scope, mode, season_id)


class SeasonPackage:
    """Read-only handle over a sealed season package with a reopened SQLite copy."""

    def __init__(self, packages_dir, entry):
        self._entry = entry
        self._tmp = Path(tempfile.mkdtemp(prefix='.open-'))
        package_path = Path(packages_dir) / entry['package']
        self._tar = tarfile.open(package_path, 'r:gz')  # noqa: SIM115 - closed in _close_handles
        try:
            self._manifest = json.loads(self._tar.extractfile('manifest.json').read())
            for label in ('scope', 'mode', 'season_id'):
                if self._manifest[label] != entry[label]:
                    raise SeasonPackageError('Package identity does not match the index')
            self._sqlite_path = self._tmp / 'season.sqlite3'
            self._sqlite_path.write_bytes(self._tar.extractfile('season.sqlite3').read())
            self._db = sqlite3.connect(self._sqlite_path)
            self._db.row_factory = sqlite3.Row
            if self._db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise SeasonPackageError('Packaged database failed integrity_check')
            if self._db.execute('PRAGMA foreign_key_check').fetchall():
                raise SeasonPackageError('Packaged database failed foreign_key_check')
            for table, expected in entry['counts'].items():
                actual = self._db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                if actual != expected:
                    raise SeasonPackageError(f'Row count mismatch for {table}')
        except Exception:
            self._close_handles()
            raise

    @classmethod
    def open(cls, packages_dir, scope, mode, season_id):
        packages_dir = Path(packages_dir)
        scope = _strict_identity(scope, 'scope')
        if mode not in MODES:
            raise ValueError('Unsupported mode')
        season_id = leaderboard_seasons._season_id(_strict_identity(season_id, 'season'))
        entry = _read_index(packages_dir).get((scope, mode, season_id))
        if entry is None or entry.get('status') != 'ARCHIVE_SEALED':
            raise SeasonPackageError(f'No sealed package for season: {(scope, mode, season_id)!r}')
        package_name = entry['package']
        if not PACKAGE_NAME.fullmatch(package_name):
            raise SeasonPackageError('Unsafe package path in index: ' + package_name)
        package_path = packages_dir / package_name
        if package_path.parent.resolve() != packages_dir.resolve() or not package_path.is_file():
            raise SeasonPackageError('Package file missing: ' + package_name)
        if _sha256_file(package_path) != entry['sha256']:
            raise SeasonPackageError('Package digest mismatch: ' + package_name)
        return cls(packages_dir, entry)

    def _close_handles(self):
        db = getattr(self, '_db', None)
        if db is not None:
            db.close()
            self._db = None
        tar = getattr(self, '_tar', None)
        if tar is not None:
            tar.close()
            self._tar = None
        tmp = getattr(self, '_tmp', None)
        if tmp is not None and tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
            self._tmp = None

    def close(self):
        self._close_handles()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def _require_db(self):
        if getattr(self, '_db', None) is None:
            raise SeasonPackageError('Package is closed')
        return self._db

    def manifest(self):
        return self._manifest

    def rank_entries(self):
        db = self._require_db()
        return [json.loads(row[0]) for row in db.execute(
            'SELECT e.payload FROM rank_entries e JOIN snapshots s USING(snapshot_id) '
            'ORDER BY e.snapshot_id, e.ordinal')]

    def snapshot_members(self, snapshot_id):
        db = self._require_db()
        return [json.loads(row[0]) for row in db.execute(
            'SELECT payload FROM snapshot_membership WHERE snapshot_id=? ORDER BY ordinal',
            (leaderboard_seasons._integer(snapshot_id),))]

    def page_evidence(self, snapshot_id=None):
        db = self._require_db()
        if snapshot_id is None:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT p.payload FROM page_evidence p JOIN snapshots s USING(snapshot_id) '
                'ORDER BY p.snapshot_id, p.ordinal')]
        return [json.loads(row[0]) for row in db.execute(
            'SELECT payload FROM page_evidence WHERE snapshot_id=? ORDER BY ordinal',
            (leaderboard_seasons._integer(snapshot_id),))]

    def battles(self):
        db = self._require_db()
        return [{'battle_uid': row['battle_uid'], 'first_snapshot_id': row['first_snapshot_id'],
                 'raw_sha256': row['raw_sha256'], 'round': row['battle_round'],
                 'formation': json.loads(row['payload'])}
                for row in db.execute(
                    'SELECT battles.battle_uid, battles.first_snapshot_id, battles.raw_sha256, '
                    'guild_formations.battle_round, guild_formations.payload FROM battles '
                    'JOIN guild_formations USING(battle_uid, season_key) ORDER BY battles.battle_uid')]

    def lineup(self, battle_uid):
        db = self._require_db()
        battle_uid = leaderboard_seasons._identifier(battle_uid)
        row = db.execute(
            'SELECT guild_formations.battle_round, guild_formations.payload FROM battles '
            'JOIN guild_formations USING(battle_uid, season_key) WHERE battles.battle_uid=?',
            (battle_uid,)).fetchone()
        if row is None:
            raise KeyError(battle_uid)
        return {'battle_uid': battle_uid, 'round': row['battle_round'],
                'formation': json.loads(row['payload']),
                'lineup': [json.loads(slot[0]) for slot in db.execute(
                    'SELECT payload FROM lineup WHERE battle_uid=? ORDER BY ordinal', (battle_uid,))]}

    def presence(self, battle_uid):
        db = self._require_db()
        battle_uid = leaderboard_seasons._identifier(battle_uid)
        return [{'member_uid': row['member_uid'], 'status': row['status'],
                 'raw_data': json.loads(row['payload'])['raw_data']}
                for row in db.execute(
                    'SELECT member_uid, status, payload FROM presence WHERE battle_uid=? '
                    'ORDER BY member_uid', (battle_uid,))]

    def member_history(self):
        db = self._require_db()
        return leaderboard_seasons.compute_member_history(
            db, self._manifest['scope'], self._manifest['mode'], self._manifest['season_id'])

    def blob(self, sha256):
        sha256 = leaderboard_seasons._text(sha256, limit=64, nonempty=True)
        if not SHA256_HEX.fullmatch(sha256):
            raise ValueError('Invalid blob digest')
        if getattr(self, '_tar', None) is None:
            raise SeasonPackageError('Package is closed')
        try:
            return self._tar.extractfile(f'blobs/{sha256}.pb').read()
        except KeyError:
            return None
