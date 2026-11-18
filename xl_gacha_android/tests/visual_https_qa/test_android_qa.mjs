import assert from 'node:assert/strict';
import test from 'node:test';
import { catalogRequest, compareArchive, validateSource } from './android_qa.mjs';

const record = {
  pool_id: 2, timestamp: 1774972800, index: 5, record_id: 42, pool_name: 'QA',
  drops: [{ item_id: 601, count: 1, name: 'Before', rarity: 5 }],
};
const before = { account: { id: 'local-qa', label: 'Visual QA' }, records: [record] };

test('archive compare checks local identity and raw rows, allowing catalog labels to update', () => {
  const after = structuredClone(before);
  after.records[0].drops[0].name = 'After';
  assert.equal(compareArchive(before, after, { records: [record] }).pulls, 1);
  after.records[0].drops[0].count = 2;
  assert.throws(() => compareArchive(before, after), /raw records/);
  const other = { ...before, account: { ...before.account, id: 'wrong' } };
  assert.throws(() => compareArchive(before, other), /identity/);
  assert.throws(() => compareArchive(before, before, { records: [] }), /demo records/);
});

test('source must use an isolated HTTPS path with no query and fragment', () => {
  assert.equal(validateSource('https://example.test/visual-v2/current.json'), 'https://example.test/visual-v2/current.json');
  for (const url of ['http://example.test/v1/current.json', 'https://a.test/current.json',
    'https://a.test/x/current.json?qa', 'https://a.test/foo/../x/current.json',
    'https://8.134.186.234/gacha-config/current.json']) {
    assert.throws(() => validateSource(url));
  }
});

test('catalog actions await the native bridge and expose its failure state', async () => {
  const source = 'https://localhost:43210/old-041/current.json';
  const paths = [];
  const previous = globalThis.MobileServices;
  const browser = { run: (evaluate, ...args) => evaluate(...args) };
  const ready = { source, phase: 'checking', visual_assets: { phase: 'unconfigured' } };
  const failed = { source, phase: 'error', error: 'CATALOG_UPDATE_FAILED' };
  globalThis.MobileServices = { api: async (path, body) => {
    paths.push([path, body]);
    if (path === '/catalog/source') return ready;
    if (path === '/catalog/check') throw Object.assign(new Error('localized'), { code: 'CATALOG_SOURCE_INVALID' });
    return failed;
  } };
  try {
    assert.deepEqual(await catalogRequest(browser, '/catalog/source', { url: source }), ready);
    await assert.rejects(catalogRequest(browser, '/catalog/check', { force: true }),
      /\/catalog\/check failed: CATALOG_SOURCE_INVALID.*"phase":"error".*CATALOG_UPDATE_FAILED/);
    assert.deepEqual(paths, [['/catalog/source', { url: source }], ['/catalog/check', { force: true }], ['/catalog', undefined]]);
  } finally {
    globalThis.MobileServices = previous;
  }
});
