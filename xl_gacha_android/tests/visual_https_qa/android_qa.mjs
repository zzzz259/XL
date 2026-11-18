import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import { readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const MAIN = 'cn.starfall.gacha.local.MainActivity';
const DEFAULT_PACKAGE = 'cn.starfall.gacha.visualqa';
const APP_ORIGIN = 'https://appassets.androidplatform.net/';
const WAIT = 45_000;

const help = `node android_qa.mjs ACTION [options]

Device actions (require --serial, default package ${DEFAULT_PACKAGE})
  install --apk PATH --version 0.4.1 --version-code 19
  launch
  restart
  source --url HTTPS_TEST_CURRENT_JSON [--stage V1]
  wait --stage V1 [--url HTTPS_TEST_CURRENT_JSON] [--offline]
  refresh --stage V2 [--url HTTPS_TEST_CURRENT_JSON]
  import --demo SYNTHETIC_DEMO_JSON
  export --output REPORT_JSON
  long-image --output PREVIEW_PNG
  corrupt --stage V2 --label B
  screenshot --output PNG

Offline action
  compare --before OLD_EXPORT --after NEW_EXPORT [--demo SYNTHETIC_DEMO_JSON]

Common options: --adb PATH --serial SERIAL --package PACKAGE --fixtures FIXTURE_ROOT
Run only against a dedicated disposable Android QA environment.`;

function required(options, key) {
  assert.ok(options[key], `--${key} is required`);
  return options[key];
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
}

export function validateSource(value) {
  assert.match(value, /^https:\/\/[^/?#%\\\s]+(?:\/[A-Za-z0-9_-]+)+\/current\.json$/,
    'Expected an isolated HTTPS source ending in /current.json');
  const url = new URL(value);
  assert.ok(url.hostname && !url.username && !url.password, 'Invalid HTTPS source');
  assert.notEqual(url.pathname.split('/')[1], 'gacha-config', 'Use an isolated test prefix');
  return url.href;
}

function rawRecords(report) {
  assert.ok(Array.isArray(report.records), 'Expected a report with records');
  return report.records.map(record => ({
    key: [record.pool_id, record.timestamp, record.index, record.record_id],
    drops: record.drops.map(drop => [drop.item_id, drop.count]),
  })).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
}

export function compareArchive(before, after, demo) {
  assert.ok(before.account?.id && after.account?.id, 'Both exports must contain a stored account');
  for (const field of ['id', 'label', 'uid', 'channel']) {
    assert.equal(after.account[field], before.account[field], 'Account identity changed: ' + field);
  }
  const rows = rawRecords(before);
  assert.ok(rows.length, 'The report must contain records');
  assert.deepEqual(rawRecords(after), rows, 'Exported raw records changed across upgrade');
  if (demo) assert.deepEqual(rows, rawRecords(demo), 'Export differs from synthetic demo records');
  const pulls = rows.reduce((sum, row) => sum + row.drops.reduce((n, [, count]) => n + count, 0), 0);
  return { records: rows.length, pulls };
}

function device(options) {
  const serial = required(options, 'serial');
  const pkg = options.package ?? DEFAULT_PACKAGE;
  assert.match(pkg, /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/, 'Invalid Android package');
  // execFile keeps the Windows ADB path and APK paths as arguments.
  const bin = options.adb ?? 'adb';
  const raw = (...args) => execFileSync(bin, ['-s', serial, ...args], { timeout: 120_000, maxBuffer: 32 * 1024 * 1024 });
  const adb = (...args) => raw(...args).toString('utf8').trim();
  return {
    pkg, adb, raw,
    launch() {
      const result = adb('shell', 'am', 'start', '-W', '-n', `${pkg}/${MAIN}`);
      assert.ok(/(?:Status:\s+ok|has been delivered)/i.test(result), result);
    },
    stop() {
      adb('shell', 'am', 'force-stop', pkg);
    },
  };
}

async function until(check, description, wait = WAIT) {
  const deadline = Date.now() + wait;
  let detail;
  do {
    try {
      const result = await check();
      if (result) return result;
    } catch (error) {
      detail = error.message;
    }
    await pause(350);
  } while (Date.now() < deadline);
  throw new Error(`Timed out ${description}${detail ? ': ' + detail : ''}`);
}

class DevTools {
  constructor(socket) {
    this.socket = socket;
    this.serial = 0;
    this.pending = new Map();
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      const request = this.pending.get(message.id);
      if (!request) return;
      this.pending.delete(message.id);
      clearTimeout(request.timer);
      if (message.error) request.reject(new Error(message.error.message));
      else request.resolve(message.result);
    });
    socket.addEventListener('close', () => {
      for (const request of this.pending.values()) {
        clearTimeout(request.timer);
        request.reject(new Error('WebView DevTools disconnected'));
      }
      this.pending.clear();
    });
  }

  call(method, params) {
    const id = ++this.serial;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`WebView DevTools ${method} timed out`));
      }, 15_000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async run(fn, ...args) {
    const expression = '(' + fn.toString() + ')(' + args.map(value => JSON.stringify(value)).join(',') + ')';
    const result = await this.call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, userGesture: true });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
    }
    return result.result.value;
  }
}

async function withWebView(target, action) {
  const pid = await until(() => {
    const value = target.adb('shell', 'pidof', '-s', target.pkg);
    return /^\d+$/.test(value) && value;
  }, `finding ${target.pkg}; run launch first`);
  const port = target.adb('forward', 'tcp:0', `localabstract:webview_devtools_remote_${pid}`);
  assert.match(port, /^\d+$/, 'ADB failed to allocate a DevTools port');
  let socket;
  try {
    const page = await until(async () => {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(1500) });
      const pages = await response.json();
      return pages.find(tab => tab.type === 'page' && tab.url.startsWith(APP_ORIGIN) && tab.webSocketDebuggerUrl);
    }, 'waiting for the app WebView; install a build built with --debug');
    const endpoint = new URL(page.webSocketDebuggerUrl);
    socket = new WebSocket(`ws://127.0.0.1:${port}${endpoint.pathname}`);
    await once(socket, 'open', { signal: AbortSignal.timeout(5000) });
    const browser = new DevTools(socket);
    await until(() => browser.run(() => window.mobileReady === true), 'waiting for the app UI');
    return await action(browser);
  } finally {
    socket?.close();
    target.adb('forward', '--remove', `tcp:${port}`);
  }
}

function fixture(options) {
  const root = options.fixtures ?? join(import.meta.dirname, 'fixtures');
  const index = readJson(join(root, 'qa-index.json'));
  const name = required(options, 'stage');
  assert.ok(Object.hasOwn(index.stages, name), 'Use --stage V1, V2 or V3');
  const stage = index.stages[name];
  return { stage, images: Array.from(stage.labels, label => ({ label, ...index.images[label] })) };
}

async function waitStage(browser, expected, { url, offline, refreshedAfter } = {}) {
  const { stage, images } = expected;
  const active = await until(async () => {
    const value = await browser.run(() => MobileServices.api('/catalog'));
    const visual = value.visual_assets;
    assert.equal(value.version, stage.version, JSON.stringify(value));
    if (url) assert.equal(value.source, url, JSON.stringify(value));
    assert.equal(value.revision, stage.catalog.sha256, JSON.stringify(value));
    if (!offline) {
      assert.equal(value.error, '', JSON.stringify(value));
      assert.equal(visual.error, '', JSON.stringify(value));
      assert.equal(visual.phase, 'current', JSON.stringify(value));
    }
    assert.equal(visual.version, stage.version, JSON.stringify(value));
    assert.equal(visual.revision, stage.visual.sha256, JSON.stringify(value));
    assert.equal(visual.origin, 'cache', JSON.stringify(value));
    if (refreshedAfter) assert.ok(value.checked_at > refreshedAfter, 'Waiting for a new forced catalog check');
    return !value.checking && !visual.checking && value;
  }, `waiting for catalog and visual ${stage.version}`);

  const observations = await browser.run(async (revision, targets) => {
    const manifest = await fetch('/assets/manifest.json', { cache: 'no-store' }).then(response => response.json());
    if (manifest._revision !== revision) throw new Error('WebView serves a different mapping revision');
    return Promise.all(targets.map(async image => {
      const entry = manifest[image.group ?? 'characters']?.[image.item_id];
      const path = entry?.[image.entry_key ?? 'path'];
      const expectedPath = 'assets/remote/' + revision + '/' + image.path;
      if (path !== expectedPath) throw new Error('Unexpected WebView portrait mapping for ' + image.label + ': ' + path);
      for (const [field, value] of Object.entries(image.entry ?? {})) {
        if (field !== image.entry_key && JSON.stringify(entry[field]) !== JSON.stringify(value)) {
          throw new Error('Unexpected WebView mapping field ' + image.label + '/' + field);
        }
      }
      const response = await fetch('/' + path, { cache: 'no-store' });
      if (!response.ok) throw new Error('WebView portrait failed: ' + response.status + ' ' + path);
      const payload = await response.arrayBuffer();
      const digest = await crypto.subtle.digest('SHA-256', payload);
      const sha256 = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
      const bitmap = await createImageBitmap(new Blob([payload], { type: 'image/png' }));
      const observed = { label: image.label, group: image.group, id: image.item_id, path, sha256, size: payload.byteLength,
        width: bitmap.width, height: bitmap.height };
      bitmap.close();
      return observed;
    }));
  }, stage.visual.sha256, images);
  for (const [idx, image] of images.entries()) {
    const actual = observations[idx];
    for (const key of ['sha256', 'size', 'width', 'height']) {
      assert.equal(actual[key], image[key], `WebView image ${image.label} differs in ${key}`);
    }
  }
  return { version: stage.version, source: active.source, mapping: stage.visual.sha256, images: observations };
}

async function importDemo(browser, file) {
  const raw = readFileSync(file, 'utf8');
  const demo = JSON.parse(raw);
  assert.ok(demo.demo === true && !demo.account && demo.records?.length, 'Provide the existing synthetic demo without account data');
  await browser.run(async content => {
    const library = await MobileServices.api('/library');
    if (library.accounts.length) throw new Error('The disposable app data must be empty before importing the demo');
    document.querySelector('[data-tab="profile"]').click();
    document.querySelector('#profile-import').click();
    document.querySelector('#import-label').value = 'Visual HTTPS QA';
    const file = new File([content], 'gacha-visual-demo.json', { type: 'application/json' });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    const input = document.querySelector('#import-file');
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    document.querySelector('#import-submit').click();
  }, raw);
  return until(() => browser.run(async () => {
    const error = document.querySelector('#import-error');
    if (!error.hidden) throw new Error(error.textContent);
    const result = await MobileServices.api('/library');
    const account = result.accounts[0];
    return result.accounts.length === 1 && account.label === 'Visual HTTPS QA' && account.pull_count;
  }), 'waiting for synthetic demo import');
}

async function exportReport(browser) {
  return browser.run(async () => {
    document.querySelector('[data-tab="profile"]').click();
    const button = document.querySelector('#profile-export-json');
    if (button.disabled) throw new Error('Export requires a saved account');
    // Capture the actual export payload at the file picker boundary.
    const original = MobileServices.saveBlob;
    try {
      const result = new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Export did not produce a JSON file')), 5000);
        MobileServices.saveBlob = (blob, filename) => {
          clearTimeout(timer);
          if (blob.type !== 'application/json' || !filename.endsWith('.json')) reject(new Error('Unexpected export format'));
          else blob.text().then(resolve, reject);
        };
      });
      button.click();
      return await result;
    } finally {
      MobileServices.saveBlob = original;
    }
  });
}

async function longImage(browser) {
  await browser.run(() => {
    document.querySelector('[data-tab="profile"]').click();
    document.querySelector('#profile-image').click();
  });
  const preview = await until(() => browser.run(() => {
    const node = document.querySelector('#image-previews img');
    return node?.complete && node.naturalWidth === 1080 && node.naturalHeight > 120
      && { url: node.src, width: node.naturalWidth, height: node.naturalHeight };
  }), 'waiting for a 1080px-wide rendered long image', 90_000);
  const dataURL = await browser.run(async url => {
    const blob = await fetch(url).then(response => response.blob());
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }, preview.url);
  assert.ok(dataURL.startsWith('data:image/png;base64,'), 'Expected the actual long image preview PNG');
  await browser.run(() => document.querySelector('#image-close').click());
  return Buffer.from(dataURL.split(',')[1], 'base64');
}

function corruptImage(target, options) {
  const { stage, images } = fixture(options);
  const image = images.find(value => value.label === required(options, 'label'));
  assert.ok(image, 'The chosen file does not exist at this stage');
  const runAs = (...args) => target.raw('exec-out', 'run-as', target.pkg, ...args);
  const cache = 'files/visual-cache';
  const active = JSON.parse(runAs('cat', `${cache}/active.json`).toString('utf8'));
  assert.equal(active.current.sha256, stage.visual.sha256, 'Unexpected active snapshot: refusing to alter its files');
  assert.match(stage.visual.sha256, /^[a-f0-9]{64}$/);
  assert.match(image.sha256, /^[a-f0-9]{64}$/);
  const path = `${cache}/${stage.visual.sha256}/${image.sha256}`;
  const before = runAs('cat', path);
  assert.equal(createHash('sha256').update(before).digest('hex'), image.sha256, 'Image is already missing or damaged');
  target.adb('shell', `run-as ${target.pkg} sh -c 'printf QA_BROKEN > ${path}'`);
  assert.equal(runAs('cat', path).toString('utf8'), 'QA_BROKEN', 'Failed to alter the synthetic cached image');
  return { corrupted: image.label, path };
}

async function main(argv) {
  const textOptions = ['adb', 'serial', 'package', 'fixtures', 'apk', 'version', 'version-code', 'url', 'stage',
    'demo', 'output', 'label', 'before', 'after'];
  const configuration = Object.fromEntries(textOptions.map(key => [key, { type: 'string' }]));
  const flags = { offline: { type: 'boolean' }, help: { type: 'boolean' } };
  const { values, positionals } = parseArgs({ args: argv, options: { ...configuration, ...flags }, allowPositionals: true });
  if (values.help || !positionals.length) return console.log(help);
  assert.equal(positionals.length, 1, help);
  const [action] = positionals;
  if (action === 'compare') {
    return console.log(JSON.stringify(compareArchive(readJson(required(values, 'before')), readJson(required(values, 'after')),
      values.demo && readJson(values.demo))));
  }
  const target = device(values);
  switch (action) {
    case 'install': {
      const apk = required(values, 'apk');
      const version = required(values, 'version');
      const code = required(values, 'version-code');
      assert.match(target.adb('install', '-r', apk), /\bSuccess\b/);
      const installed = target.adb('shell', 'dumpsys', 'package', target.pkg);
      assert.equal(installed.match(/^\s*versionName=(\S+)/m)?.[1], version, 'Wrong installed version');
      assert.equal(installed.match(/^\s*versionCode=(\d+)/m)?.[1], code, 'Wrong installed versionCode');
      return console.log(JSON.stringify({ installed: target.pkg, version, versionCode: code }));
    }
    case 'restart':
      target.stop();
      // fall through to launch
    case 'launch':
      return target.launch();
    case 'corrupt':
      return console.log(JSON.stringify(corruptImage(target, values)));
    case 'screenshot':
      return writeFileSync(required(values, 'output'), target.raw('exec-out', 'screencap', '-p'), { flag: 'wx' });
  }
  return withWebView(target, async browser => {
    switch (action) {
      case 'source': {
        const url = validateSource(required(values, 'url'));
        await until(() => browser.run(async () => {
          const state = await MobileServices.api('/catalog');
          return !state.checking && !state.visual_assets.checking;
        }), 'waiting for the initial catalog check to settle');
        await browser.run(address => {
          document.querySelector('[data-tab="profile"]').click();
          const field = document.querySelector('#catalog-source');
          field.closest('details').open = true;
          field.value = address;
          document.querySelector('#catalog-source-save').click();
        }, url);
        await until(() => browser.run(async address => (await MobileServices.api('/catalog')).source === address, url),
          'waiting for the configured HTTPS source');
        const result = values.stage ? await waitStage(browser, fixture(values), { url }) : { source: url };
        return console.log(JSON.stringify(result));
      }
      case 'refresh': {
        const url = values.url && validateSource(values.url);
        const before = await browser.run(async () => (await MobileServices.api('/catalog')).checked_at);
        await browser.run(() => document.querySelector('#catalog-check').click());
        return console.log(JSON.stringify(await waitStage(browser, fixture(values), { url, refreshedAfter: before })));
      }
      case 'wait':
        return console.log(JSON.stringify(await waitStage(browser, fixture(values), {
          url: values.url && validateSource(values.url), offline: values.offline,
        })));
      case 'import':
        return console.log(JSON.stringify({ importedPulls: await importDemo(browser, required(values, 'demo')) }));
      case 'export':
        return writeFileSync(required(values, 'output'), await exportReport(browser), { flag: 'wx' });
      case 'long-image':
        return writeFileSync(required(values, 'output'), await longImage(browser), { flag: 'wx' });
      default:
        throw new Error(help);
    }
  });
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main(process.argv.slice(2)).catch(error => {
    console.error(error.stack ?? error);
    process.exitCode = 1;
  });
}
