const {test} = require('node:test');
const assert = require('node:assert/strict');
const {planPages, makeZip} = require('../static/image-export.js');

test('pagination keeps newest first, each event exactly once and respects canvas limits', () => {
  const events = Array.from({length: 320}, (_, i) => ({name: `角色${i}`}));
  const report = {analysis: {pools: [{category: 'limited', pool_name: '池', events},
    {category: 'normal', pool_name: '排除', events: [{name: '不应出现'}]}]}};
  const pages = planPages(report, 'limited', 2400);
  assert.ok(pages.length > 1);
  assert.ok(pages.every(p => p.height <= 2400));
  const names = pages.flatMap(p => p.chunks.flatMap(c => c.events.map(e => e.name)));
  assert.equal(names.length, 320);
  assert.equal(new Set(names).size, 320);
  assert.equal(names[0], '角色319');
  assert.equal(names.at(-1), '角色0');
});

test('zip stores independent PNG bytes with readable UTF-8 filenames', async () => {
  const zip = await makeZip([{name: '抽卡-01.png', blob: new Blob([Uint8Array.from([1,2,3,4])])}]);
  const buffer = new Uint8Array(await zip.arrayBuffer());
  const view = new DataView(buffer.buffer);
  assert.equal(view.getUint32(0, true), 0x04034b50);
  const length = view.getUint16(26, true);
  assert.equal(new TextDecoder().decode(buffer.slice(30, 30 + length)), '抽卡-01.png');
  assert.deepEqual([...buffer.slice(30 + length, 34 + length)], [1,2,3,4]);
  assert.equal(view.getUint32(buffer.length - 22, true), 0x06054b50);
});
