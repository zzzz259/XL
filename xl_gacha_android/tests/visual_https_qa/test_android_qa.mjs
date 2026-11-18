import assert from 'node:assert/strict';
import test from 'node:test';
import { compareArchive, validateSource } from './android_qa.mjs';

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
