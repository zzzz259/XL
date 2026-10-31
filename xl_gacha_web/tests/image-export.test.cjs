const {test} = require('node:test');
const assert = require('node:assert/strict');
const {planPages, makeZip, deliver} = require('../static/image-export.js');

function freshLongImage() {
  delete require.cache[require.resolve('../static/image-export.js')];
  return require('../static/image-export.js');
}

function mockImage(w = 280, h = 140) {
  return {naturalWidth: w, naturalHeight: h, width: w, height: h};
}

function mockCanvas() {
  const ops = [];
  return {width: 0, height: 0, ops,
    getContext: () => ({fillStyle: '', font: '',
      fillRect: (...a) => ops.push(['fillRect', ...a]),
      fillText: (...a) => ops.push(['fillText', ...a]),
      measureText: s => ({width: String(s).length * 10}),
      drawImage: (...a) => ops.push(['drawImage', ...a])})};
}

function assetBridge({throwIds = new Set(), nullIds = new Set(), failSrc = new Set(), failBrand = false} = {}) {
  const calls = {init: 0, character: [], load: []};
  globalThis.MobileAssets = {
    init: async () => {calls.init++;},
    character: async id => {calls.character.push(id);
      if (throwIds.has(id)) throw new Error('bad character');
      return nullIds.has(id) ? null : {src: `assets/char/${id}.png`, name: `角色${id}`, rarity: 5, crop: 'left'};},
    loadImage: async src => {calls.load.push(src);
      if (failSrc.has(src) || (failBrand && src.includes('brand'))) throw new Error('decode failed');
      return mockImage();},
    brand: async () => ({logo: 'assets/brand/starfall-logo.png', name: '星落'}),
  };
  return calls;
}

function imageReport() {
  const events = [
    {name: '角色甲', item_id: 11, pulls_since_five: 20, interval_exact: true, outcome: 'up'},
    {name: '角色乙', item_id: 22, pulls_since_five: 40, interval_exact: true, outcome: 'off', guaranteed: true, carried_pulls: 3},
    {name: '角色甲', item_id: 11, pulls_since_five: 10, interval_exact: true, outcome: 'up'},
  ];
  return {account: {label: '主号'}, preview_source: false,
    summary: {pull_count: 200, rarity_counts: {'5': 3}, pool_pull_counts: {池: 200}},
    analysis: {categories: {normal: {name: '普通池', pull_count: 200, five_count: 3}},
      pools: [{pool_name: '池', category: 'normal', category_name: '普通池', first_date: '2026-01-01',
        last_date: '2026-01-03', pull_count: 200, hard_pity: 70, tail_since_five: 5, tail_exact: true,
        next_guaranteed: false, events}]}};
}

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

test('sharing selected pages preserves page order and never saves an album copy', async () => {
  const files=[{name:'01.png'},{name:'02.png'},{name:'03.png'}],calls=[];
  const bridge={saveImage:async()=>assert.fail('share must not save'),shareImages:async images=>{calls.push(images.map(v=>v.name));}};
  const result=await deliver(files,new Set([2,0]),'share',bridge);
  assert.deepEqual(calls,[['01.png','03.png']]);
  assert.deepEqual(result,{opened:true,count:2});
});

test('album batch stops on failure and reports only completed pages', async () => {
  const calls=[],progress=[];
  const bridge={saveImage:async file=>{calls.push(file.name);if(file.name==='02.png')throw new Error('STORAGE_PERMISSION_DENIED');}};
  await assert.rejects(deliver([{name:'01.png'},{name:'02.png'},{name:'03.png'}],new Set([0,1,2]),'album',bridge,n=>progress.push(n)),e=>e.message==='STORAGE_PERMISSION_DENIED'&&e.completed===1);
  assert.deepEqual(calls,['01.png','02.png']);assert.deepEqual(progress,[1]);
});

test('empty selection does not open a share sheet or save a file', async () => {
  await assert.rejects(deliver([{name:'01.png'}],new Set(),'share',{shareImages:async()=>assert.fail()}),/请先选择/);
});

test('prepare degrades to null when MobileAssets is unavailable', async () => {
  delete globalThis.MobileAssets;
  const result = await freshLongImage().prepare(imageReport(), []);
  assert.equal(result, null);
});

test('prepare preloads unique characters once, caches across calls and includes the brand logo', async () => {
  const calls = assetBridge();
  const LongImage = freshLongImage(), report = imageReport(), pages = LongImage.planPages(report, 'all');
  const first = await LongImage.prepare(report, pages);
  assert.equal(calls.init, 1);
  assert.deepEqual([...calls.character].sort((a, b) => a - b), [11, 22]);
  assert.equal(calls.load.filter(src => src.includes('char')).length, 2);
  assert.deepEqual(calls.load.filter(src => src.includes('brand')), ['assets/brand/starfall-logo.png']);
  assert.equal(first.characters.get(11).info.name, '角色11');
  assert.ok(first.brand.image);
  const second = await LongImage.prepare(report, pages);
  assert.equal(calls.character.length, 2);
  assert.equal(second.characters.get(11), first.characters.get(11));
  delete globalThis.MobileAssets;
});

test('prepare only preloads characters referenced by the given pages', async () => {
  const calls = assetBridge();
  const LongImage = freshLongImage(), report = imageReport();
  report.analysis.pools.push({pool_name: '别的池', category: 'limited', category_name: '限定池',
    first_date: '2026-01-01', last_date: '2026-01-02', pull_count: 1, hard_pity: 70,
    tail_since_five: 1, tail_exact: true, next_guaranteed: false,
    events: [{name: '角色丙', item_id: 99, pulls_since_five: 1, interval_exact: true, outcome: 'up'}]});
  const pages = LongImage.planPages(report, 'normal');
  const assets = await LongImage.prepare(report, pages);
  assert.equal(calls.character.includes(99), false);
  assert.equal(assets.characters.has(99), false);
  delete globalThis.MobileAssets;
});

test('prepare drops failed character images and brand failures without rejecting', async () => {
  assetBridge({throwIds: new Set([55]), nullIds: new Set([44]), failSrc: new Set(['assets/char/22.png']), failBrand: true});
  const LongImage = freshLongImage(), report = imageReport();
  report.analysis.pools[0].events = [
    {name: '角色A', item_id: 11, pulls_since_five: 5, interval_exact: true, outcome: 'up'},
    {name: '角色B', item_id: 22, pulls_since_five: 15, interval_exact: true, outcome: 'up'},
    {name: '角色C', item_id: 33, pulls_since_five: 25, interval_exact: true, outcome: 'up'},
    {name: '角色D', item_id: 44, pulls_since_five: 35, interval_exact: true, outcome: 'up'},
    {name: '角色E', item_id: 55, pulls_since_five: 45, interval_exact: true, outcome: 'up'},
  ];
  const assets = await LongImage.prepare(report, LongImage.planPages(report, 'all'));
  assert.ok(assets);
  assert.deepEqual([...assets.characters.keys()].sort((a, b) => a - b), [11, 33]);
  assert.equal(assets.brand, null);
  delete globalThis.MobileAssets;
});

test('draw keeps the legacy text layout when no assets are passed', () => {
  const LongImage = freshLongImage();
  globalThis.document = {createElement: () => mockCanvas()};
  const report = imageReport(), page = LongImage.planPages(report, 'all')[0];
  const canvas = LongImage.draw(report, page, 0, 1, 'all');
  assert.equal(canvas.ops.filter(op => op[0] === 'drawImage').length, 0);
  const nameOp = canvas.ops.find(op => op[0] === 'fillText' && op[1] === '角色甲');
  assert.equal(nameOp[2], 60);
  const accountOp = canvas.ops.find(op => op[0] === 'fillText' && op[1] === '主号');
  assert.equal(accountOp[4], 950);
  delete globalThis.document;
});

test('draw renders the brand logo and square-cropped avatars while keeping rows inside the row height', async () => {
  const LongImage = freshLongImage();
  globalThis.document = {createElement: () => mockCanvas()};
  assetBridge();
  const report = imageReport(), pages = LongImage.planPages(report, 'all');
  const assets = await LongImage.prepare(report, pages);
  const canvas = LongImage.draw(report, pages[0], 0, pages.length, 'all', assets);
  const draws = canvas.ops.filter(op => op[0] === 'drawImage');
  assert.equal(draws.length, 4);
  assert.equal(draws[0][1], assets.brand.image);
  assert.deepEqual(draws[0].slice(2), [1080 - 40 - 56, 51, 56, 34]);
  for (const avatar of draws.slice(1)) {
    assert.deepEqual(avatar.slice(2, 6), [0, 0, 140, 140]);
    assert.deepEqual(avatar.slice(6), [60, avatar[7], 60, 60]);
  }
  const nameOp = canvas.ops.find(op => op[0] === 'fillText' && op[1] === '角色甲');
  assert.equal(nameOp[2], 132);
  const accountOp = canvas.ops.find(op => op[0] === 'fillText' && op[1] === '主号');
  assert.equal(accountOp[4], 1080 - 40 - 56 - 64 - 24);
  delete globalThis.document;
  delete globalThis.MobileAssets;
});
