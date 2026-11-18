"""Build and exercise three APKs against a private HTTPS fixture on an Android emulator."""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree

from generate_fixtures import BUNDLED_VERSION, generate
from verify_requests import verify

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ANDROID = '{http://schemas.android.com/apk/res/android}'
PACKAGE = 'cn.starfall.gacha.visualhttpsqa'
VERSIONS = {
    '0.4.1': ('78e384745f6204925f8978957091c229739a2a8b', 19),
    '0.4.2': ('4a61982c3ac428ffe6eda399deb8ddb84a76f510', 25),
    '0.4.3': ('HEAD', 26),
}
SOURCES = (
    'xl_gacha_android/AndroidManifest.xml', 'xl_gacha_android/baseline.json',
    'xl_gacha_android/build.py', 'xl_gacha_android/toolchain.py', 'xl_gacha_android/src',
    'xl_gacha_android/web', 'xl_gacha_android/tests',
    'xl_gacha_web/static', 'xl_gacha_web/data/app.ico', 'xl_gacha_web/data/catalog.json',
    'xl_gacha_web/data/update-source.json',
)
APK_NAME = 'starfall-gacha-android-qa.apk'
PNG_PATH = r'assets/(?:portraits|account|account-rect)/[a-f0-9]{64}\.png'
JSON_PATH = r'(?:catalog|visual-assets)-([1-9][0-9]{0,19})\.json'


def command(*arguments, cwd=REPO, check=True):
    result = subprocess.run([str(arg) for arg in arguments], cwd=cwd, capture_output=True, timeout=300, check=False)
    if check and result.returncode:
        raise RuntimeError(f'{arguments[0]} exited {result.returncode}:\n'
                           + result.stdout.decode('utf-8', 'replace')[-4000:]
                           + result.stderr.decode('utf-8', 'replace')[-8000:])
    return result


def text(*arguments):
    return command(*arguments).stdout.decode('utf-8', 'replace').strip()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def load_json(path):
    return json.loads(path.read_bytes())


def archive_sources(commit, destination):
    # Only the pinned files used to build the APK are unpacked.
    raw = command('git', 'archive', '--format=tar', commit, *SOURCES).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(destination, filter='data')


def archive_candidate(destination):
    # Candidate includes the workflow's exact checkout, including local edits during developer runs.
    for name in SOURCES:
        source, output = REPO / name, destination / name
        if source.is_dir():
            shutil.copytree(source, output, ignore=shutil.ignore_patterns('visual_https_qa', '__pycache__'))
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)


def make_certificates(folder):
    folder.mkdir()
    cwd = folder
    command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '2',
            '-subj', '/CN=XL Visual QA Ephemeral Root', '-addext', 'basicConstraints=critical,CA:TRUE',
            '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-keyout', 'ca.key', '-out', 'ca.pem', cwd=cwd)
    command('openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=localhost',
            '-keyout', 'server.key', '-out', 'server.csr', cwd=cwd)
    extension = folder / 'server.ext'
    extension.write_text('subjectAltName=DNS:localhost\nbasicConstraints=critical,CA:FALSE\n'
                         'keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n',
                         encoding='ascii')
    command('openssl', 'x509', '-req', '-in', 'server.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key',
            '-CAcreateserial', '-days', '2', '-sha256', '-extfile', 'server.ext', '-out', 'server.pem', cwd=cwd)
    command('openssl', 'verify', '-CAfile', 'ca.pem', '-verify_hostname', 'localhost', 'server.pem', cwd=cwd)
    folder.joinpath('ca.key').unlink()


def configure_qa(source, output, ca):
    manifest = source / 'xl_gacha_android' / 'AndroidManifest.xml'
    xml = manifest.read_text(encoding='utf-8')
    app = ElementTree.fromstring(xml).find('application')
    if app is None or app.attrib.get(ANDROID + 'usesCleartextTraffic') != 'false' or ANDROID + 'networkSecurityConfig' in app.attrib:
        raise ValueError('Unexpected manifest security configuration in APK source')
    xml, replaced = re.subn(r'<application(?=\s)', '<application android:networkSecurityConfig="@xml/visual_qa_network"', xml)
    if replaced != 1:
        raise ValueError('Expected exactly one application element')
    manifest.write_text(xml, encoding='utf-8')
    source.joinpath('xl_gacha_web/data/update-source.json').write_text('{"url":""}\n', encoding='utf-8')
    raw, config = output / 'res/raw', output / 'res/xml'
    raw.mkdir(parents=True)
    config.mkdir()
    shutil.copyfile(ca, raw / 'visual_qa_ca.pem')
    config.joinpath('visual_qa_network.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<network-security-config>\n'
        '  <domain-config cleartextTrafficPermitted="false">\n'
        '    <domain includeSubdomains="false">localhost</domain>\n'
        '    <trust-anchors><certificates src="@raw/visual_qa_ca"/></trust-anchors>\n'
        '  </domain-config>\n</network-security-config>\n', encoding='utf-8',
    )


def prepare(work, sdk, java, remote):
    work.mkdir(parents=True)
    sdk, java = sdk.resolve(), java.resolve()
    tls = work / 'tls'
    make_certificates(tls)
    missing = [sha for sha, _ in VERSIONS.values() if sha != 'HEAD'
               and command('git', 'cat-file', '-e', sha + '^{commit}', check=False).returncode]
    if missing:
        command('git', 'fetch', '--no-tags', '--depth=1', remote, *missing)
    build_metadata = {}
    tools = sdk / 'build-tools/35.0.0'
    suffix = '.exe' if os.name == 'nt' else ''
    for version, (reference, code) in VERSIONS.items():
        commit = text('git', 'rev-parse', reference + '^{commit}')
        source = work / 'sources' / version
        source.mkdir(parents=True)
        if reference == 'HEAD':
            archive_candidate(source)
        else:
            archive_sources(commit, source)
        original = ElementTree.parse(source / 'xl_gacha_android' / 'AndroidManifest.xml').getroot()
        if (original.attrib[ANDROID + 'versionName'], int(original.attrib[ANDROID + 'versionCode'])) != (version, code):
            raise ValueError(f'Version mismatch in source {reference}: expected {version}/{code}')
        output = work / 'apk' / version
        configure_qa(source, output, tls / 'ca.pem')
        command(sys.executable, '-B', source / 'xl_gacha_android/build.py', '--sdk', sdk, '--jdk', java,
                '--output', output, '--application-id', PACKAGE, '--development-key', '--debug')
        apk = output / APK_NAME
        badging = text(tools / ('aapt2' + suffix), 'dump', 'badging', apk)
        expected = f"package: name='{PACKAGE}' versionCode='{code}' versionName='{version}'"
        if expected not in badging or 'application-debuggable' not in badging:
            raise ValueError(f'Unexpected APK identity: {badging.splitlines()[0]}')
        signing = text(java / 'bin' / ('java' + suffix), '-jar', tools / 'lib/apksigner.jar',
                       'verify', '--print-certs', apk)
        fingerprint = re.search(r'certificate SHA-256 digest: ([0-9a-f]{64})', signing)
        if not fingerprint:
            raise ValueError('APK signing fingerprint missing')
        with zipfile.ZipFile(apk) as zipped:
            if (load := json.loads(zipped.read('assets/update-source.json'))) != {'url': ''}:
                raise ValueError(f'Unexpected default source: {load}')
            if zipped.read('res/raw/visual_qa_ca.pem') != (tls / 'ca.pem').read_bytes():
                raise ValueError('QA CA is missing from the signed APK')
            if 'res/xml/visual_qa_network.xml' not in zipped.namelist():
                raise ValueError('QA trust configuration is missing from the signed APK')
        build_metadata[version] = {'commit': commit, 'version_code': code, 'package': PACKAGE,
                                   'signer_sha256': fingerprint[1], 'apk_sha256': hashlib.sha256(apk.read_bytes()).hexdigest()}
        print(f'Prepared {version}/{code} from {commit}', flush=True)
    if len({entry['signer_sha256'] for entry in build_metadata.values()}) != 1:
        raise ValueError('The three QA APKs must have the same development signature')
    candidate = work / 'sources/0.4.3/xl_gacha_web'
    catalog = load_json(candidate / 'data/catalog.json')
    demo = load_json(candidate / 'static/demo.json')
    generate(catalog, demo, work / 'fixtures', max(BUNDLED_VERSION, int(catalog['version'])) + 1, four_groups=True)
    save_json(work / 'build.json', build_metadata)


def fixture_request(path, active, versions):
    match = re.fullmatch(r'/([a-z0-9-]+)/(.+)', path)
    if not match or match[1] not in active:
        return None
    case, filename = match.groups()
    stage = active[case]
    if filename == 'current.json' or re.fullmatch(PNG_PATH, filename):
        return case, stage, filename
    found = re.fullmatch(JSON_PATH, filename)
    if found:
        for revision, version in versions.items():
            if version == found[1] and int(version) <= int(versions[stage]):
                return case, revision, filename
    return None


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, work, index, access_log):
        super().__init__(('127.0.0.1', 0), FixtureHandler)
        self.fixtures = work / 'fixtures'
        self.active = {}
        self.versions = {stage: row['version'] for stage, row in index['stages'].items()}
        self.events = []
        self.access_log = access_log
        self.lock = threading.Lock()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(work / 'tls/server.pem', work / 'tls/server.key')
        self.socket = context.wrap_socket(self.socket, server_side=True)

    def read_fixture(self, path):
        with self.lock:
            item = fixture_request(path, self.active, self.versions)
        if item:
            _, stage, name = item
            path = self.fixtures / stage / name
            if path.is_file():
                return path.read_bytes(), 'image/png' if name.endswith('.png') else 'application/json'
        return None, 'text/plain'

    def activate(self, case, stage):
        with self.lock:
            self.active[case] = stage
            return len(self.events)

    def record(self, path, status, size):
        with self.lock:
            row = {'method': 'GET', 'uri': path, 'status': status, 'bytes': size}
            self.events.append(row)
            with self.access_log.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row) + '\n')


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        data, mime = self.server.read_fixture(self.path)
        status = 200 if data is not None else 404
        payload = data or b''
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Encoding', 'identity')
        self.end_headers()
        self.wfile.write(payload)
        self.server.record(self.path, status, len(payload))

    def log_message(self, format, *arguments):
        pass


class Runner:
    def __init__(self, work, evidence, serial):
        self.work, self.evidence, self.serial = work, evidence, serial
        self.index = load_json(work / 'fixtures/qa-index.json')
        self.server = FixtureServer(work, self.index, evidence / 'https-requests.jsonl')
        self.port = 'tcp:' + str(self.server.server_port)
        self.results = {'apk_builds': load_json(work / 'build.json'), 'cases': []}
        self.demo = work / 'sources/0.4.3/xl_gacha_web/static/demo.json'
        self.reversed = False
        self.case = None
        self.step = None

    def adb(self, *arguments):
        return text('adb', '-s', self.serial, *arguments)

    def harness(self, action, *arguments):
        self.step = action
        result = text('node', HERE / 'android_qa.mjs', action, '--serial', self.serial, '--package', PACKAGE,
                      '--fixtures', self.work / 'fixtures', *arguments)
        return json.loads(result) if result else None

    def reverse(self, enabled):
        if enabled:
            self.adb('reverse', self.port, self.port)
        else:
            self.adb('reverse', '--remove', self.port)
        self.reversed = enabled

    @contextmanager
    def serving(self):
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        try:
            self.reverse(True)
            yield
        finally:
            try:
                if self.reversed:
                    self.reverse(False)
            finally:
                self.server.shutdown()
                self.server.server_close()
                thread.join()

    def install(self, version):
        code = VERSIONS[version][1]
        observed = self.harness('install', '--apk', self.work / 'apk' / version / APK_NAME,
                                '--version', version, '--version-code', code)
        self.case['installed'].append(observed)
        self.harness('launch')

    def begin(self, name, version):
        self.case = {'name': name, 'installed': [], 'phases': []}
        self.results['cases'].append(self.case)
        self.output = self.evidence / name
        self.output.mkdir()
        self.url = f'https://localhost:{self.server.server_port}/{name}/current.json'
        installed = self.adb('shell', 'pm', 'list', 'packages', PACKAGE)
        if 'package:' + PACKAGE in installed.splitlines():
            result = self.adb('uninstall', PACKAGE)
            if result != 'Success':
                raise RuntimeError(f'Could not reset the disposable QA app: {result}')
        self.install(version)

    def phase(self, name, stage, images, action, *, current=1, catalogs=1, mappings=1, offline=False):
        case = self.case['name']
        start = self.server.activate(case, stage)
        self.step = name
        if offline:
            self.reverse(False)
            try:
                self.harness('restart')
                state = self.harness('wait', '--url', self.url, '--stage', stage, '--offline')
            finally:
                self.reverse(True)
        elif callable(action):
            state = action()
        else:
            state = self.harness(action, '--url', self.url, '--stage', stage)
        prefix = '/' + case + '/'
        with self.server.lock:
            rows = [(row['uri'][len(prefix):], row['method'], row['status'], row['bytes'])
                    for row in self.server.events[start:] if row['uri'].startswith(prefix)]
        rows = [row for row in rows if row[0] == 'current.json' or row[0].startswith(('catalog-', 'visual-assets-', 'assets/'))]
        marked = self.index['stages'][stage]
        expected = {'current.json': (marked['current']['size'], current),
                    marked['catalog']['file']: (marked['catalog']['size'], catalogs),
                    marked['visual']['file']: (marked['visual']['size'], mappings)}
        for label in images:
            image = self.index['images'][label]
            expected[image['path']] = (image['size'], 1)
        self.step = name + ' request assertions'
        requests = verify(rows, expected)
        self.case['phases'].append({'name': name, 'stage': stage, 'expected_images': images,
                                    'requests': requests, 'webview': state})
        print(f'PASS {case}/{name}: {stage} images={images or "-"}', flush=True)

    def old(self, version):
        self.begin('old-' + version.replace('.', ''), version)
        self.phase('initial', 'V1', 'AB', 'source')
        self.phase('legacy-full', 'V2', 'ABC', 'refresh')

    def upgrade(self, version):
        self.begin('upgrade-' + version.replace('.', ''), version)
        self.phase('initial', 'V1', 'AB', 'source')
        self.harness('import', '--demo', self.demo)
        before, after = self.output / 'before.json', self.output / 'after.json'
        self.harness('export', '--output', before)

        def replace_apk():
            self.install('0.4.3')
            return self.harness('wait', '--url', self.url, '--stage', 'V1')

        self.phase('upgrade-cache-reuse', 'V1', '', replace_apk, current=0, catalogs=0, mappings=0)
        self.phase('delta', 'V2', 'C', 'refresh')
        self.harness('export', '--output', after)
        comparison = self.harness('compare', '--before', before, '--after', after, '--demo', self.demo)
        if comparison != {'records': 49, 'pulls': 464}:
            raise ValueError(f'Unexpected synthetic archive totals: {comparison}')
        self.case['archive'] = comparison
        self.harness('long-image', '--output', self.output / 'long-image.png')
        if version == '0.4.2':
            self.harness('corrupt', '--stage', 'V2', '--label', 'C')
            self.phase('repair', 'V2', 'C', 'refresh', catalogs=0)
            self.phase('offline-restart', 'V2', '', 'wait', current=0, catalogs=0, mappings=0, offline=True)

    def exercise(self):
        self.old('0.4.1')
        self.old('0.4.2')
        self.upgrade('0.4.1')
        self.upgrade('0.4.2')
        self.begin('skip', '0.4.3')
        self.phase('initial', 'V1', 'AB', 'source')
        self.phase('skip-v2', 'V3', 'CD', 'refresh')
        self.begin('cold', '0.4.3')
        self.phase('cold-v3', 'V3', 'ABCD', 'source')


def exercise(work, evidence, serial):
    devices = re.findall(r'^(emulator-\d+)\s+device\s*$', text('adb', 'devices'), re.MULTILINE)
    if not serial and len(devices) == 1:
        serial = devices[0]
    if serial not in devices:
        raise ValueError('Select a running disposable Android emulator with --serial')
    evidence.mkdir(parents=True)
    runner = Runner(work, evidence, serial)
    save_json(evidence / 'fixture-index.json', runner.index)
    started = time.monotonic()
    try:
        with runner.serving():
            runner.exercise()
        runner.results['status'] = 'pass'
        print('PASS: Android HTTPS visual upgrades, archives, cache repair and offline restart')
    except Exception as error:
        runner.results.update(status='fail', case=runner.case and runner.case['name'], step=runner.step, error=str(error))
        raise
    finally:
        runner.results['seconds'] = round(time.monotonic() - started, 3)
        save_json(evidence / 'results.json', runner.results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    build = sub.add_parser('prepare', help='Prepare three signed QA APKs and HTTPS fixtures before booting the AVD')
    build.add_argument('--work', type=Path, required=True)
    build.add_argument('--sdk', type=Path, default=os.environ.get('ANDROID_HOME'))
    build.add_argument('--jdk', type=Path, default=os.environ.get('JAVA_HOME'))
    build.add_argument('--remote', default='origin')
    test = sub.add_parser('exercise', help='Run after the AVD boots')
    test.add_argument('--work', type=Path, required=True)
    test.add_argument('--serial')
    test.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    work = args.work.resolve()
    if work.is_relative_to(REPO):
        parser.error('--work must be outside the source checkout.')
    if args.action == 'prepare':
        if not args.sdk or not args.jdk:
            parser.error('Set ANDROID_HOME and JAVA_HOME, or use --sdk and --jdk.')
        prepare(work, args.sdk, args.jdk, args.remote)
    else:
        exercise(work, args.evidence or work / 'evidence', args.serial)


if __name__ == '__main__':
    main()
