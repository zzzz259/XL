const {test} = require('node:test');
const assert = require('node:assert/strict');
const {planPages, makeZip, deliver} = require('../static/image-export.js');

function freshLongImage() {
  delete require.cache[require.resolve('../static/image-export.js')];
  return require('../static/image-export.js');
}

function mockImage(w = 256, h = 256) {
  return {naturalWidth: w, naturalHeight: h, width: w, height: h};
}

function mockCanvas() {
  const ops = [];
  const ctx = {fillStyle: '', font: '', _align: 'left',
    fillRect(...a) {ops.push(['fillRect', ...a, this.fillStyle]);},
    createLinearGradient(...bounds) {return {kind: 'linear-gradient', bounds, stops: [], addColorStop(offset, color) {this.stops.push({offset, color});}};},
    fillText(text, x, y, max) {ops.push(['fillText', text, x, y, max, this.font, this.textAlign, this.fillStyle]);},
    measureText: s => ({width: String(s).length * 10}),
    save: () => ops.push(['save']), restore: () => ops.push(['restore']),
    beginPath: () => ops.push(['beginPath']), arc: (...a) => ops.push(['arc', ...a]), clip: () => ops.push(['clip']),
    drawImage: (...a) => ops.push(['drawImage', ...a])};
  Object.defineProperty(ctx, 'textAlign', {
    get() {return ctx._align;},
    set(value) {ctx._align = value; ops.push(['textAlign', value]);},
  });
  return {width: 0, height: 0, ops, getContext: () => ctx};
}

function assetBridge({throwIds = new Set(), nullIds = new Set(), failSrc = new Set(), failBrand = false, crop = 'center'} = {}) {
  const calls = {init: 0, character: [], load: []};
  globalThis.MobileAssets = {
    init: async () => {calls.init++;},
    character: async id => {calls.character.push(id);
      if (throwIds.has(id)) throw new Error('bad character');
      return nullIds.has(id) ? null : {src: `assets/char/${id}.png`, name: `角色${id}`, rarity: 5, crop};},
    loadImage: async src => {calls.load.push(src);
      if (failSrc.has(src) || (failBrand && src.includes('brand'))) throw new Error('decode failed');
      return src.includes('brand') ? mockImage(280, 140) : mockImage();},
    brand: async () => ({logo: 'assets/brand/starfall-logo.png', name: '星落'}),
  };
  return calls;
}

function imageReport() {
  const events = [
    {name: '角色甲', item_id: 11, pulls_since_five: 20, interval_exact: true, outcome: 'up', in_pool_pull: 20, time_beijing: '2026-01-02 03:04:05'},
    {name: '角色乙', item_id: 22, pulls_since_five: 40, interval_exact: true, outcome: 'off', guaranteed: true, carried_pulls: 3, in_pool_pull: 60, time_beijing: '2026-01-03 03:04:05'},
    {name: '角色甲', item_id: 11, pulls_since_five: 10, interval_exact: true, outcome: 'up', in_pool_pull: 70, time_beijing: '2026-01-04 03:04:05'},
  ];
  return {account: {label: '主号'}, preview_source: false,
    summary: {pull_count: 200, rarity_counts: {'5': 3}, pool_pull_counts: {池: 200}},
    analysis: {categories: {normal: {name: '普通池', pull_count: 200, five_count: 3, up_count: 0, off_count: 0, average_five_interval: 66.67, since_five: 5, since_five_exact: true, hard_pity: 70}},
      pools: [{pool_id: 1, pool_name: '池', category: 'normal', category_name: '普通池', first_date: '2026-01-01',
        last_date: '2026-01-03', pull_count: 200, five_count: 3, hard_pity: 70, tail_since_five: 5, tail_exact: true,
        next_guaranteed: false, events}]}};
}

function multiPoolReport() {
  const mk = (pool_id, category, tag, n) => ({pool_id, pool_name: `池${tag}`, category, category_name: category, first_date: '2026-01-01',
    last_date: '2026-01-02', pull_count: n, five_count: n, hard_pity: 70, tail_since_five: 1, tail_exact: true, next_guaranteed: false,
    events: Array.from({length: n}, (_, i) => ({name: `${tag}${i}`, item_id: i + 1, pulls_since_five: 10, interval_exact: true, outcome: 'up'}))});
  return {analysis: {pools: [mk(1, 'limited', '甲', 3), mk(2, 'limited', '乙', 2), mk(3, 'normal', '丙', 1)]}};
}

test('pagination keeps newest first, each event exactly once and respects canvas limits', () => {
  const events = Array.from({length: 320}, (_, i) => ({name: `角色${i}`}));
  const report = {analysis: {pools: [{pool_id: 7, category: 'limited', pool_name: '池', events},
    {pool_id: 8, category: 'normal', pool_name: '排除', events: [{name: '不应出现'}]}]}};
  const pages = planPages(report, 'limited', 2400);
  assert.ok(pages.length > 1);
  assert.ok(pages.every(p => p.height <= 2400));
  const names = pages.flatMap(p => p.chunks.flatMap(c => c.events.map(e => e.name)));
  assert.equal(names.length, 320);
  assert.equal(new Set(names).size, 320);
  assert.equal(names[0], '角色319');
  assert.equal(names.at(-1), '角色0');
});

test('planPages with null or omitted pool ids keeps the legacy contract', () => {
  const report = imageReport();
  const baseline = planPages(report, 'all');
  assert.deepEqual(planPages(report, 'all', 10000, null), baseline);
  assert.deepEqual(planPages(report, 'all', 10000, undefined), baseline);
  assert.equal(baseline[0].chunks.length, 1);
  assert.equal(baseline[0].chunks[0].events.length, 3);
});

test('planPages filters by specific pool ids given as a Set or an array', () => {
  const report = multiPoolReport();
  const bySet = planPages(report, 'all', 10000, new Set([1, 3]));
  const byArray = planPages(report, 'all', 10000, [1, 3]);
  const names = pages => pages.flatMap(p => p.chunks.flatMap(c => c.events.map(e => e.name)));
  assert.deepEqual(names(bySet), ['甲2', '甲1', '甲0', '丙0']);
  assert.deepEqual(byArray.map(p => p.height), bySet.map(p => p.height));
  const limitedOnly = planPages(report, 'limited', 10000, new Set([1, 3]));
  assert.deepEqual(names(limitedOnly), ['甲2', '甲1', '甲0']);
  const stringIds = planPages(report, 'all', 10000, new Set(['2']));
  assert.deepEqual(names(stringIds), ['乙1', '乙0']);
  const unknown = planPages(report, 'all', 10000, new Set([999]));
  assert.equal(unknown.length, 1);
  assert.equal(unknown[0].chunks.length, 0);
});

test('planPages keeps pagination identical when every pool is selected', () => {
  const events = Array.from({length: 320}, (_, i) => ({name: `角色${i}`}));
  const report = {analysis: {pools: [{pool_id: 7, category: 'limited', pool_name: '池', events},
    {pool_id: 8, category: 'normal', pool_name: '别的池', events: Array.from({length: 5}, (_, i) => ({name: `其他${i}`}))}]}};
  const shape = pages => pages.map(p => ({height: p.height, counts: p.chunks.map(c => c.events.length)}));
  const all = planPages(report, 'all', 2400);
  const selected = planPages(report, 'all', 2400, new Set([7, 8]));
  assert.deepEqual(shape(selected), shape(all));
  assert.deepEqual(shape(planPages(report, 'limited', 2400, new Set([7]))), shape(planPages(report, 'limited', 2400)));
});

test('planPages with an empty selection yields a single blank page that still draws', () => {
  const LongImage = freshLongImage();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const report = imageReport();
  const pages = LongImage.planPages(report, 'all', 10000, []);
  assert.equal(pages.length, 1);
  assert.equal(pages[0].chunks.length, 0);
  assert.equal(pages[0].height, pages[0].headerHeight + 100);
  const modern = LongImage.draw(report, pages[0], 0, 1, 'all', {brand: null, characters: new Map(), insights: null});
  assert.equal(modern.ops.filter(op => op[0] === 'drawImage').length, 0);
  const legacy = LongImage.draw(report, pages[0], 0, 1, 'all');
  assert.equal(legacy.ops.filter(op => op[0] === 'drawImage').length, 0);
  delete globalThis.document;
});

test('headRect uses the full square for center crops and keeps the left crop for wide banners', () => {
  const LongImage = freshLongImage();
  assert.deepEqual(LongImage.headRect(mockImage(256, 256), 'center'), [0, 0, 256, 256]);
  assert.deepEqual(LongImage.headRect(mockImage(280, 140), 'left'), [0, 0, 140, 140]);
  assert.deepEqual(LongImage.headRect(mockImage(280, 140), 'center'), [0, 0, 280, 140]);
  assert.deepEqual(LongImage.headRect(mockImage(280, 140)), [70, 0, 140, 140]);
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
  report.analysis.pools.push({pool_id: 2, pool_name: '别的池', category: 'limited', category_name: '限定池',
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

test('prepare scopes insights to the selected pools category and degrades without MobileInsights', async () => {
  const calls = assetBridge();
  const LongImage = freshLongImage(), report = imageReport();
  const seen = [];
  globalThis.MobileInsights = {analyze: (value, options) => {seen.push(options); return {career: null, recent: null, tags: [], multi: {byPool: {}}}}};
  const assets = await LongImage.prepare(report, LongImage.planPages(report, 'all'), [1]);
  // planPages consults MobileInsights for tag-row planning ('all' scope), then prepare scopes to the selection
  assert.deepEqual(seen, [{category: 'all'}, {category: 'normal', poolIds: [1]}]);
  assert.deepEqual(assets.insights, {career: null, recent: null, tags: [], multi: {byPool: {}}});
  await LongImage.prepare(report, LongImage.planPages(report, 'all'));
  assert.deepEqual(seen.slice(2), [{category: 'all'}, {category: 'all'}]);
  delete globalThis.MobileInsights;
  const plain = await LongImage.prepare(report, LongImage.planPages(report, 'all'), [1]);
  assert.equal(plain.insights, null);
  delete globalThis.MobileAssets;
});

test('draw keeps the legacy text layout when no assets are passed', () => {
  const LongImage = freshLongImage();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const report = imageReport(), page = LongImage.planPages(report, 'all')[0];
  const canvas = LongImage.draw(report, page, 0, 1, 'all');
  assert.equal(canvas.ops.filter(op => op[0] === 'drawImage').length, 0);
  const nameOp = canvas.ops.find(op => op[0] === 'fillText' && op[1] === '角色甲');
  assert.equal(nameOp[2], 60);
  const accountOp = canvas.ops.find(op => op[0] === 'fillText' && op[1] === '主号');
  assert.equal(accountOp[4], undefined);
  assert.equal(accountOp[2], 64);
  delete globalThis.document;
});

test('draw renders a centered intact logo and inset portrait rows in the mobile layout', async () => {
  const LongImage = freshLongImage();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  assetBridge();
  const report = imageReport(), pages = LongImage.planPages(report, 'all');
  const assets = await LongImage.prepare(report, pages);
  const canvas = LongImage.draw(report, pages[0], 0, pages.length, 'all', assets);
  const draws = canvas.ops.filter(op => op[0] === 'drawImage');
  assert.equal(draws.length, 4);
  assert.equal(draws[0][1], assets.brand.image);
  // The complete 2:1 logo is enlarged proportionally, never square-cropped.
  const [logoX, logoY, logoW, logoH] = draws[0].slice(2);
  assert.equal(logoX + logoW / 2, 540);
  assert.equal(logoW / logoH, 2);
  assert.ok(logoH >= 120 && logoY >= 32);
  for (const avatar of draws.slice(1)) {
    assert.deepEqual(avatar.slice(2, 6), [0, 0, 256, 256]);
    assert.ok(avatar[6] >= 72 && avatar[8] >= 88 && avatar[9] === avatar[8]);
  }
  const nameOp = canvas.ops.find(op => op[0] === 'fillText' && op[1] === '角色甲');
  assert.ok(nameOp[2] >= 184);
  const accountOp = canvas.ops.find(op => op[0] === 'fillText' && op[1] === '招募档案');
  assert.equal(accountOp[2], 540);
  assert.equal(accountOp[6], 'center');
  assert.ok(accountOp[3] > logoY + logoH + 60);
  // career cards use the overlaid (叠字) pity progress text
  const pityText = canvas.ops.find(op => op[0] === 'fillText' && String(op[1]).startsWith('已垫 '));
  assert.ok(pityText);
  // nickname fallback appears exactly once and no UID is written by default
  assert.equal(canvas.ops.filter(op => op[0] === 'fillText' && op[1] === '招募档案').length, 1);
  assert.equal(canvas.ops.some(op => op[0] === 'fillText' && /UID/.test(String(op[1]))), false);
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('draw shows career and recent evaluations plus rule tags when insights exist and omits them when missing', async () => {
  const LongImage = freshLongImage();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  assetBridge();
  const report = imageReport(), pages = LongImage.planPages(report, 'all');
  globalThis.MobileInsights = {analyze: () => ({
    career: {status: 'rated', label: '绝世欧皇', lowConfidence: true, tone: 'lucky'},
    recent: {status: 'small', label: null, tone: 'quiet'},
    tags: [{key: 'ten_2', text: '十连双金', count: 2}, {key: 'pity_70', text: '抽满70井', count: 1}],
    multi: {byPool: {}},
  })};
  const assets = await LongImage.prepare(report, pages);
  const withInsights = LongImage.draw(report, pages[0], 0, pages.length, 'all', assets);
  assert.ok(withInsights.ops.some(op => op[0] === 'fillText' && op[1] === '生涯 · 绝世欧皇 · 暂估'));
  assert.ok(withInsights.ops.some(op => op[0] === 'fillText' && op[1] === '近45天 · 样本较少'));
  assert.ok(withInsights.ops.some(op => op[0] === 'fillText' && op[1] === '十连双金 ×2'));
  assert.ok(withInsights.ops.some(op => op[0] === 'fillText' && op[1] === '抽满70井'));
  delete globalThis.MobileInsights;
  const plain = await LongImage.prepare(report, pages);
  const without = LongImage.draw(report, pages[0], 0, pages.length, 'all', plain);
  assert.equal(without.ops.some(op => op[0] === 'fillText' && String(op[1]).startsWith('生涯 ·')), false);
  assert.equal(without.ops.some(op => op[0] === 'fillText' && String(op[1]).startsWith('十连')), false);
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('draw keeps the new interface non-overlapping with overlaid bar text', async () => {
  const LongImage = freshLongImage();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  assetBridge();
  const report = imageReport(), pages = LongImage.planPages(report, 'all');
  const assets = await LongImage.prepare(report, pages);
  const canvas = LongImage.draw(report, pages[0], 0, pages.length, 'all', assets);
  const ops = canvas.ops;
  const texts = ops.filter(op => op[0] === 'fillText').map(op => {
    const size = Number(/(\d+(?:\.\d+)?)px/.exec(op[5])[1]);
    const w = String(op[1]).length * 10;
    const align = op[6] || 'left';
    const left = align === 'center' ? op[2] - w / 2 : align === 'right' ? op[2] - w : op[2];
    return {s: String(op[1]), left, right: left + w, y: op[3], size};
  });
  const overlap = (a, b) => a.left < b.right && b.left < a.right && a.y - a.size < b.y && b.y - b.size < a.y;
  for (const [i, a] of texts.entries())
    for (const b of texts.slice(i + 1))
      assert.ok(!overlap(a, b), `texts overlap: ${a.s} vs ${b.s}`);
  const rects = ops.filter(op => op[0] === 'fillRect');
  // interval bar moved to its own second row: track then fill, with the dark count text overlaid inside the fill
  const tracks = rects.filter(r => r[1] === 184 && r[3] === 824 && r[4] === 42);
  assert.equal(tracks.length, 3);
  for (const track of tracks) {
    const at = ops.indexOf(track);
    const fill = ops.slice(at + 1).find(op => op[0] === 'fillRect' && op[1] === 184 && op[2] === track[2]);
    assert.ok(fill);
    assert.ok(fill[3] >= 72 && fill[3] <= 824);
    assert.ok(track[1] + track[3] <= 1008, 'event bars must keep an inner margin');
    const count = texts.find(t => t.left === 200 && t.y === track[2] + 30);
    assert.ok(count, 'missing overlaid interval text');
    assert.ok(count.right <= 184 + fill[3]);
  }
  // event chips follow the name on the first row and stay inside the card
  for (const chip of rects.filter(r => r[4] === 32 && r[3] >= 26 && r[3] <= 300))
    assert.ok(chip[1] >= 184 && chip[1] + chip[3] <= 1008);
  // avatars stay left of the names
  for (const avatar of ops.filter(op => op[0] === 'drawImage').slice(1)) {
    assert.equal(avatar[6], 72);
    assert.ok(avatar[6] + avatar[8] <= 184);
  }
  // career pity track hosts its overlaid centered text
  const pityTracks = rects.filter(r => r[3] === 272 && r[4] === 30);
  assert.equal(pityTracks.length, 3);
  const pity = texts.find(t => String(t.s).startsWith('已垫 '));
  assert.ok(pity.left >= pityTracks[0][1] && pity.right <= pityTracks[0][1] + 272);
  assert.ok(pity.y <= pityTracks[0][2] + 30 && pity.y > pityTracks[0][2]);
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('modern export uses real player identity, a larger logo and inline complete event tags', () => {
  const LongImage = freshLongImage();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  assetBridge();
  const report = imageReport();
  report.account = {label: 'TapTap · UID 123456', nickname: '真实昵称', game_uid: '123456', level: 80};
  const event = report.analysis.pools[0].events[1];
  event.outcome = 'up'; event.guaranteed = true;
  const assets = {brand: {image: mockImage(280, 140)}, characters: new Map(), insights: {
    career: null, recent: null, tags: [], multi: {byPool: {'1': [{positions: [60], five_count: 4, exact: true}]}},
  }};
  const canvas = LongImage.draw(report, LongImage.planPages(report, 'all')[0], 0, 1, 'all', assets);
  const texts = canvas.ops.filter(op => op[0] === 'fillText');
  assert.ok(texts.some(op => op[1] === '真实昵称'));
  assert.ok(texts.some(op => op[1] === 'UID 123456 · Lv.80'));
  assert.equal(texts.some(op => String(op[1]).includes('TapTap')), false);
  const logo = canvas.ops.find(op => op[0] === 'drawImage');
  assert.equal(logo.length, 6);
  assert.ok(logo.at(-1) >= 120);
  assert.equal(logo[2] + logo[4] / 2, 540);
  assert.equal(texts.find(op => op[1] === '真实昵称')[6], 'center');
  assert.ok(texts.find(op => String(op[1]).includes('200 抽'))[3] > texts.find(op => op[1] === 'UID 123456 · Lv.80')[3]);
  assert.ok(texts.every(op => op[4] === undefined), 'text must be ellipsized explicitly without Canvas maxWidth squeezing');
  for (const text of ['UP', '大保底']) {
    const tag = texts.find(op => op[1] === text);
    assert.ok(tag, `missing ${text}`);
    assert.ok(tag[2] > 132 && tag[2] < 772, `${text} should follow the name`);
  }
  assert.ok(texts.some(op => op[1] === '十连四金'), 'multi-gold label now appears once in its group header');
  assert.equal(texts.some(op => String(op[1]).includes('本池第')), false);
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('modern export wraps every career tag instead of dropping overflowing tags', () => {
  const LongImage = freshLongImage();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  assetBridge();
  const tags = [
    {key: 'up_streak', text: '4连UP', count: 1},
    {key: 'guarantee_streak', text: '3连大保底', count: 1},
    {key: 'ten_4', text: '十连四金', count: 2},
    {key: 'up_130', text: '130抽以上才出UP', count: 3},
    {key: 'awaken_1', text: '满觉角色甲 满觉', count: 1},
    {key: 'awaken_2', text: '满觉角色乙 满觉', count: 1},
  ];
  globalThis.MobileInsights = {analyze: () => ({career: null, recent: null, tags, multi: {byPool: {}}})};
  const report = imageReport(), page = LongImage.planPages(report, 'all')[0];
  assert.ok(page.headerHeight > 380);
  const canvas = LongImage.draw(report, page, 0, 1, 'all', {brand: null, characters: new Map(), insights: globalThis.MobileInsights.analyze()});
  const texts = canvas.ops.filter(op => op[0] === 'fillText').map(op => op[1]);
  for (const tag of tags) {
    const text = tag.count > 1 ? `${tag.text} ×${tag.count}` : tag.text;
    assert.ok(texts.includes(text), `missing ${text}`);
  }
  assert.equal(canvas.height, page.height);
  delete globalThis.MobileInsights;
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('mobile preparation resolves account art and undrawn featured characters, then follows refreshed mappings', async () => {
  const calls = assetBridge();
  let manifest = {characters: {'33': {name: '未抽到的UP', path: 'assets/char/33.png'}}};
  globalThis.MobileAssets.init = async () => manifest;
  globalThis.MobileAssets.player = account => ({head: {src: `assets/player/${account.face_id}.png`, crop: 'left'}, frame: {src: 'assets/player/frame.png'}, rank: 3});
  const LongImage = freshLongImage(), report = imageReport();
  report.account.face_id = 7;
  report.analysis.pools[0].featured_names = ['未抽到的UP'];
  let assets = await LongImage.prepare(report, LongImage.planPages(report));
  assert.ok(assets.player.head.image);
  assert.ok(assets.player.frame.image);
  assert.equal(assets.player.rank, 3);
  assert.deepEqual(assets.featured.get('1').map(entry => entry.itemId), [33]);
  assert.ok(calls.character.includes(33), 'UP portrait must load even without an event');
  const originalImage = assets.characters.get(11).image;
  manifest = {characters: {'33': {name: '未抽到的UP', path: 'assets/char/new-33.png'}}};
  assets = await LongImage.prepare(report, LongImage.planPages(report));
  assert.notEqual(assets.characters.get(11).image, originalImage, 'refreshed manifest must invalidate export image entries');
  delete globalThis.MobileAssets;
});

test('mobile export uses metadata portrait bounds for event and featured art and never bleeds into the footer', async () => {
  assetBridge();
  const LongImage = freshLongImage(), report = imageReport();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const portrait = {info: {crop: 'center', cropRect: [18, 24, 208, 208]}, image: mockImage()};
  const pages = LongImage.planPages(report, 'all', 1600);
  assert.ok(pages.length > 1);
  for (const [index, page] of pages.entries()) {
    const canvas = LongImage.draw(report, page, index, pages.length, 'all', {brand: null, characters: new Map([[11, portrait], [22, portrait]]), featured: new Map([['1', [{...portrait, itemId: 11}]]])});
    const images = canvas.ops.filter(op => op[0] === 'drawImage');
    assert.ok(images.length > page.chunks.reduce((sum, part) => sum + part.events.length, 0));
    for (const op of images) {
      assert.deepEqual(op.slice(2, 6), [18, 24, 208, 208]);
      assert.ok(op[6] >= 72 && op[6] + op[8] <= 1008);
      assert.ok(op[7] + op[9] < canvas.height - 100);
    }
    assert.ok(canvas.height <= 1600);
  }
  assert.equal(pages[1].chunks[0].continuation, true);
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('mobile pagination rejects a canvas limit smaller than its header and one record', () => {
  assetBridge();
  const LongImage = freshLongImage();
  // This limit fits the former compressed header but cannot fit the readable mobile layout.
  assert.throws(() => LongImage.planPages(imageReport(), 'all', 1000), /图片高度|记录|卡池/);
  delete globalThis.MobileAssets;
});

test('mobile text keeps readable glyph widths and all event chips when names are long', () => {
  assetBridge();
  const LongImage = freshLongImage(), report = imageReport();
  report.account = {nickname: '很长的玩家昵称'.repeat(20), game_uid: '123456789', level: 99};
  const event = report.analysis.pools[0].events[0];
  event.name = '很长的角色名称'.repeat(12);
  Object.assign(event, {outcome: 'up', guaranteed: true, interval_exact: false, guarantee_conflict: true});
  const measure = (text, font) => {
    const size = Number(/(\d+)px/.exec(font)?.[1] || 26);
    return [...String(text)].reduce((sum, ch) => sum + size * (ch.charCodeAt(0) > 127 ? 1 : .55), 0);
  };
  globalThis.document = {createElement: () => {
    const canvas = mockCanvas(), context = canvas.getContext();
    context.measureText = text => ({width: measure(text, context.font)});
    return canvas;
  }, getElementById: () => null};
  const insights = {tags: [], multi: {byPool: {'1': [{positions: [20], exact: false, five_count: 4}]}}};
  const canvas = LongImage.draw(report, LongImage.planPages(report)[0], 0, 1, 'all', {brand: null, characters: new Map(), insights});
  const texts = canvas.ops.filter(op => op[0] === 'fillText');
  const nickname = texts.find(op => String(op[1]).startsWith('很长的玩家'));
  const name = texts.find(op => String(op[1]).startsWith('很长的角色'));
  assert.ok(nickname[1].endsWith('…'));
  assert.ok(name[1].endsWith('…'));
  assert.ok(measure(nickname[1], nickname[5]) <= 888);
  assert.ok(measure(name[1], name[5]) <= 266);
  assert.ok(texts.every(op => op[4] === undefined));
  assert.ok(texts.some(op => op[1] === '十连至少四金'));
  const expected = ['UP', '大保底', '待核验', '前序不完整'];
  const chips = expected.map(label => texts.find(op => op[1] === label));
  assert.ok(chips.every(Boolean), 'every meaningful event badge must survive long names');
  for (const [index, chip] of chips.entries()) {
    assert.ok(chip[2] + measure(chip[1], chip[5]) <= 1008);
    if (index) assert.ok(chips[index - 1][2] + measure(chips[index - 1][1], chips[index - 1][5]) < chip[2]);
  }
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('mobile manifest failure preserves readable text layout and isolates player image failures', async () => {
  assetBridge({throwIds: new Set([11, 22]), failBrand: true});
  globalThis.MobileAssets.init = async () => {throw new Error('manifest unavailable');};
  globalThis.MobileAssets.player = () => ({head: {src: 'assets/player/missing.png'}, frame: {src: 'assets/player/frame.png'}});
  const loadImage = globalThis.MobileAssets.loadImage;
  globalThis.MobileAssets.loadImage = src => src.includes('missing') ? Promise.reject(new Error('missing head')) : loadImage(src);
  const LongImage = freshLongImage(), report = imageReport();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const pages = LongImage.planPages(report), assets = await LongImage.prepare(report, pages);
  assert.ok(assets, 'a broken mobile image manifest must retain the mobile text layout');
  assert.equal(assets.player.head, null);
  assert.ok(assets.player.frame.image, 'one failed player image must not suppress another valid image');
  const canvas = LongImage.draw(report, pages[0], 0, 1, 'all', assets);
  assert.ok(canvas.ops.some(op => op[0] === 'fillText' && op[1] === '抽卡手记' && op[2] === 540));
  assert.ok(canvas.ops.some(op => op[0] === 'fillText' && op[1] === '角色甲' && op[2] === 184));
  assert.equal(canvas.height, pages[0].height);
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('mobile account art follows the game head viewport and surrounding frame geometry', () => {
  assetBridge();
  const LongImage = freshLongImage(), report = imageReport();
  report.account.nickname = '玩家';
  report.account.game_uid = '123456';
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const head = {info: {crop: 'game-head', cropRect: [91, 23, 88, 88]}, image: mockImage(320, 140)};
  const frame = {info: {}, image: mockImage(130, 130)};
  const canvas = LongImage.draw(report, LongImage.planPages(report)[0], 0, 1, 'all', {brand: null, characters: new Map(), player: {head, frame}});
  const images = canvas.ops.filter(op => op[0] === 'drawImage');
  assert.equal(images.length, 2);
  const headOp = images.find(op => op[1] === head.image), frameOp = images.find(op => op[1] === frame.image);
  assert.deepEqual(headOp.slice(2, 6), [91, 23, 88, 88]);
  assert.equal(headOp[8], 70.4);
  assert.equal(headOp[9], 70.4);
  assert.equal(frameOp[4], 96);
  assert.equal(frameOp[5], 96);
  assert.ok(Math.abs(headOp[6] + 35.2 - frameOp[2] - 48) < 0.001);
  assert.ok(Math.abs(headOp[7] + 35.2 - frameOp[3] - 48) < 0.001);
  const circle = canvas.ops.find(op => op[0] === 'arc');
  assert.ok(circle, 'account heads must use the same circular viewport as the page');
  assert.deepEqual(circle.slice(1), [headOp[6] + 35.2, headOp[7] + 35.2, 35.2, 0, Math.PI * 2]);
  const clipAt = canvas.ops.findIndex(op => op[0] === 'clip'), restoreAt = canvas.ops.findIndex(op => op[0] === 'restore');
  assert.ok(clipAt < canvas.ops.indexOf(headOp));
  assert.ok(canvas.ops.indexOf(headOp) < restoreAt);
  assert.ok(restoreAt < canvas.ops.indexOf(frameOp), 'frame art must remain outside the head clip');
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('mobile career cards retain average metrics beside counts with room before pity and tags', () => {
  assetBridge();
  const LongImage = freshLongImage(), report = imageReport();
  report.analysis.categories.limited = {name: '限定池', pull_count: 200, five_count: 5, up_count: 4, off_count: 1, average_five_interval: 49.25, since_five: 1, hard_pity: 70};
  report.analysis.categories.collection = {name: '典藏池', pull_count: 0, five_count: 0, up_count: 0, off_count: 0, average_five_interval: null};
  globalThis.MobileInsights = {analyze: () => ({tags: [{key: 'up_streak', text: '4连UP', count: 1}]})};
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const page = LongImage.planPages(report)[0];
  const canvas = LongImage.draw(report, page, 0, 1, 'all', {brand: null, characters: new Map(), insights: globalThis.MobileInsights.analyze()});
  const texts = canvas.ops.filter(op => op[0] === 'fillText');
  const averages = ['66.7', '50.0', '—'].map(value => texts.find(op => op[1] === value));
  assert.ok(averages.every(Boolean), 'average values must use the same one-decimal calculation and empty state as the app');
  const labels = texts.filter(op => op[1] === '五星平均' || op[1] === '平均UP');
  assert.equal(labels.length, 3);
  assert.equal(labels[0][1], '五星平均');
  const pity = texts.filter(op => String(op[1]).startsWith('已垫 '));
  const tag = texts.find(op => op[1] === '4连UP');
  for (const [index, average] of averages.entries()) {
    assert.equal(average[2], labels[index][2]);
    assert.equal(average[6], 'center');
    assert.ok(average[3] < labels[index][3] - 21);
    assert.ok(labels[index][3] < pity[index][3] - 30);
    assert.ok(pity[index][3] < tag[3] - 40);
  }
  assert.ok(page.headerHeight > tag[3] + 40, 'the larger cards and tags must remain inside the planned header');
  delete globalThis.document;
  delete globalThis.MobileInsights;
  delete globalThis.MobileAssets;
});

test('mobile career warning tags use the same red categories as the app', () => {
  assetBridge();
  const LongImage = freshLongImage(), report = imageReport();
  const tags = [
    {key: 'guarantee_streak', text: '3连大保底'},
    {key: 'up_130', text: '130抽以上才出UP'},
    {key: 'off_streak', text: '3连歪'},
    {key: 'up_streak', text: '4连UP'},
    {key: 'pity_70', text: '抽满70井'},
  ];
  globalThis.MobileInsights = {analyze: () => ({tags})};
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const canvas = LongImage.draw(report, LongImage.planPages(report)[0], 0, 1, 'all', {characters: new Map(), insights: {tags}});
  const texts = canvas.ops.filter(op => op[0] === 'fillText');
  for (const tag of tags) assert.equal(texts.find(op => op[1] === tag.text)[7], ['guarantee_streak', 'up_130', 'off_streak'].includes(tag.key) ? '#ff9c91' : '#54e0b9');
  delete globalThis.document;
  delete globalThis.MobileInsights;
  delete globalThis.MobileAssets;
});

test('mobile export shows luck badges from complete selected UP cycles without changing interval bars', async () => {
  assetBridge();
  globalThis.MobileProbability = require('../../xl_gacha_android/web/mobile-probability.js');
  const LongImage = freshLongImage(), report = imageReport(), pool = report.analysis.pools[0];
  pool.category = 'limited';
  pool.events = [
    {name: '角色甲', item_id: 11, timestamp: 1, pulls_since_five: 3, interval_exact: true, outcome: 'up', guaranteed: false},
    {name: '角色乙', item_id: 22, timestamp: 2, pulls_since_five: 65, interval_exact: true, outcome: 'off', guaranteed: false},
    {name: '角色甲', item_id: 11, timestamp: 3, pulls_since_five: 65, interval_exact: true, outcome: 'up', guaranteed: true},
  ];
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const pages = LongImage.planPages(report), assets = await LongImage.prepare(report, pages);
  const canvas = LongImage.draw(report, pages[0], 0, 1, 'all', assets);
  const texts = canvas.ops.filter(op => op[0] === 'fillText').map(op => op[1]);
  assert.equal(texts.filter(value => value === '超欧').length, 1);
  assert.equal(texts.filter(value => value === '超非').length, 1);
  assert.equal(texts.filter(value => value === '65 抽').length, 2, 'bar labels still show exact five-star intervals');
  delete globalThis.MobileProbability;
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('mobile canvas uses the shared account display name without changing desktop aliases', () => {
  assetBridge();
  const LongImage = freshLongImage(), report = imageReport();
  globalThis.MobileServices = {accountName: account => account.label === '主号' ? '自定义主号' : '招募档案'};
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const page = LongImage.planPages(report)[0];
  const mobile = LongImage.draw(report, page, 0, 1, 'all', {characters: new Map()});
  assert.ok(mobile.ops.some(op => op[0] === 'fillText' && op[1] === '自定义主号'));
  const desktop = LongImage.draw(report, page, 0, 1, 'all');
  assert.ok(desktop.ops.some(op => op[0] === 'fillText' && op[1] === '主号'));
  delete globalThis.MobileServices;
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

function multiExportReport() {
  const engine = require('../../xl_gacha_android/web/mobile-engine.js');
  const catalog = {version: 'test', cards: {1: ['低星', 3]}, pools: {1: '普通池'}, pool_meta: {1: {category: 'normal', pity_group: 'normal', hard_pity: 70}}};
  for (const id of [101, 102, 103, 104, 105, 106, 107]) catalog.cards[id] = [`角色${id}`, 5];
  const drops = [
    [{item_id: 101, count: 1}],
    [{item_id: 1, count: 7}, {item_id: 102, count: 1}, {item_id: 103, count: 1}, {item_id: 104, count: 1}],
    [{item_id: 1, count: 8}, {item_id: 105, count: 1}, {item_id: 106, count: 1}],
    [{item_id: 107, count: 1}],
  ];
  return engine.buildReport(drops.map((value, index) => ({pool_id: 1, timestamp: index + 1, index: index + 1, record_id: index + 1, drops: value})), catalog);
}

test('export groups only real ten-pull members and separates adjacent multi-gold ten-pulls', () => {
  assetBridge();
  globalThis.MobileInsights = require('../../xl_gacha_android/web/mobile-insights.js');
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const LongImage = freshLongImage(), report = multiExportReport(), snapshot = JSON.stringify(report);
  const insights = MobileInsights.analyze(report), page = LongImage.planPages(report)[0];
  const canvas = LongImage.draw(report, page, 0, 1, 'all', {characters: new Map(), insights});
  const groups = canvas.ops.filter(op => op[0] === 'fillRect' && op[5]?.kind === 'linear-gradient');
  const texts = canvas.ops.filter(op => op[0] === 'fillText');
  assert.equal(groups.length, 2);
  assert.deepEqual(groups.map(op => op.slice(1, 5)), [[60, page.headerHeight + 324, 960, 276], [60, page.headerHeight + 612, 960, 420]]);
  assert.notEqual(groups[0][5], groups[1][5], 'adjacent tens need independent gradients');
  for (const group of groups) {
    const gradient = group[5];
    assert.deepEqual(gradient.bounds, [0, group[2], 0, group[2] + group[4]], 'fade restarts at each group top');
    assert.equal(gradient.stops[0].offset, 0); assert.equal(gradient.stops.at(-1).offset, 1);
    const opacity = stop => Number(/([\d.]+)\)$/.exec(stop.color)[1]);
    assert.ok(opacity(gradient.stops[0]) > opacity(gradient.stops.at(-1)) * 3, 'next group starts visibly darker than previous group ends');
  }
  const headers = texts.filter(op => /^十连(?:双|三)金$/.test(op[1]) && op[3] > page.headerHeight);
  assert.deepEqual(headers.map(op => op[1]), ['十连双金', '十连三金']);
  for (const [index, header] of headers.entries()) assert.ok(header[3] > groups[index][2] && header[3] < groups[index][2] + 30);
  for (const text of texts.filter(op => /^角色\d+$/.test(op[1]))) {
    const matching = groups.filter(group => text[3] >= group[2] && text[3] <= group[2] + group[4]);
    assert.equal(matching.length, ['角色101', '角色107'].includes(text[1]) ? 0 : 1, `incorrect group for ${text[1]}`);
  }
  assert.equal(JSON.stringify(report), snapshot);
  delete globalThis.MobileInsights;
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('export shades only visible burst members on continued pages without inserting event rows', () => {
  assetBridge();
  globalThis.MobileInsights = require('../../xl_gacha_android/web/mobile-insights.js');
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const LongImage = freshLongImage(), report = multiExportReport(), insights = MobileInsights.analyze(report);
  const pages = LongImage.planPages(report, 'all', 1516), renderedNames = [], labels = [];
  assert.equal(pages.length, 4);
  for (const [index, page] of pages.entries()) {
    const canvas = LongImage.draw(report, page, index, pages.length, 'all', {characters: new Map(), insights});
    const groups = canvas.ops.filter(op => op[0] === 'fillRect' && op[5]?.kind === 'linear-gradient');
    const texts = canvas.ops.filter(op => op[0] === 'fillText');
    renderedNames.push(...texts.filter(op => /^角色\d+$/.test(op[1])).map(op => op[1]));
    labels.push(texts.filter(op => /^十连/.test(op[1]) && op[3] > page.headerHeight).map(op => op[1]));
    assert.equal(groups.length, texts.filter(op => /^十连/.test(op[1]) && op[3] > page.headerHeight).length);
    for (const group of groups) assert.ok(group[2] + group[4] < canvas.height - 100);
    for (const text of texts.filter(op => /^角色\d+$/.test(op[1]))) assert.ok(text[3] < canvas.height - 100);
    assert.ok(page.height <= 1516);
  }
  assert.deepEqual(renderedNames, ['角色107', '角色106', '角色105', '角色104', '角色103', '角色102', '角色101']);
  assert.deepEqual(labels, [['十连双金'], ['十连双金（续）', '十连三金'], ['十连三金（续）'], []]);
  delete globalThis.MobileInsights;
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('export formats full-140 career tags consistently and restores original UP/off count size without squeezing', () => {
  assetBridge();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
  const LongImage = freshLongImage(), report = imageReport();
  report.analysis.categories.limited = {name: '限定池', pull_count: 400, five_count: 8, up_count: 7, off_count: 1, average_five_interval: 50};
  const tags = [{key: 'up_140', text: '旧标签', count: 2}];
  const draw = () => LongImage.draw(report, LongImage.planPages(report)[0], 0, 1, 'all', {characters: new Map(), insights: {tags}});
  let canvas = draw(), texts = canvas.ops.filter(op => op[0] === 'fillText');
  let label = texts.find(op => op[1] === '吃满140*2');
  assert.ok(label, 'fallback must format the new tag without stale text or multiplication glyph');
  assert.equal(label[7], '#ff9c91');
  const up = texts.find(op => op[1] === '7' && op[7] === '#14c7a7');
  const off = texts.find(op => op[1] === '1' && op[7] === '#ff5d50');
  assert.ok(/ 28px /.test(up[5]) && / 28px /.test(off[5]));
  assert.equal(up[4], undefined);assert.equal(off[4], undefined);
  globalThis.MobileInsights = {tagText: tag => tag.key === 'up_140' ? '共享格式文本' : tag.text};
  canvas = draw();
  assert.ok(canvas.ops.some(op => op[0] === 'fillText' && op[1] === '共享格式文本'));
  delete globalThis.MobileInsights;
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('export evaluations match the total-pull font and form one centered pair without long-text collisions', () => {
  assetBridge();
  const measure = (text, size) => [...String(text)].reduce((sum, ch) => sum + size * (ch.charCodeAt(0) > 127 ? 1 : .55), 0);
  globalThis.document = {createElement: () => {
    const canvas = mockCanvas(), context = canvas.getContext();
    context.measureText = text => ({width: measure(text, Number(/(\d+)px/.exec(context.font)?.[1] || 26))});
    return canvas;
  }, getElementById: () => null};
  const LongImage = freshLongImage(), report = imageReport(), page = LongImage.planPages(report)[0];
  const cases = [
    {career: {status: 'rated', label: '绝世欧皇', lowConfidence: true, tone: 'lucky'}, recent: {status: 'small', tone: 'quiet'}},
    {career: {status: 'rated', label: '很长的生涯评价'.repeat(30), lowConfidence: true, tone: 'rough'}, recent: {status: 'zero', tone: 'quiet'}},
  ];
  for (const [index, insights] of cases.entries()) {
    const canvas = LongImage.draw(report, page, 0, 1, 'all', {characters: new Map(), insights});
    const texts = canvas.ops.filter(op => op[0] === 'fillText');
    const summary = texts.find(op => String(op[1]).includes('200 抽') && String(op[1]).includes('个五星'));
    const evaluations = ['生涯 · ', '近45天 · '].map(prefix => texts.find(op => String(op[1]).startsWith(prefix)));
    const bounds = evaluations.map(op => {
      const size = Number(/(\d+)px/.exec(op[5])[1]), width = measure(op[1], size);
      const left = op[6] === 'center' ? op[2] - width / 2 : op[6] === 'right' ? op[2] - width : op[2];
      assert.equal(size, Number(/(\d+)px/.exec(summary[5])[1]), 'evaluation text must match the total-pull font');
      return {left, right: left + width, baseline: op[3], top: op[3] - size};
    });
    assert.ok(Math.abs((bounds[0].left + bounds[1].right) / 2 - 540) < .01, 'the actual text pair must center on the image axis');
    assert.ok(bounds[1].left - bounds[0].right >= 32 && bounds[1].left - bounds[0].right <= 40);
    assert.ok(bounds[0].left >= 72 && bounds[1].right <= 1008);
    assert.equal(bounds[0].baseline, bounds[1].baseline);
    assert.ok(bounds[0].top > summary[3] && bounds[0].baseline < 558);
    if (index === 0) assert.equal(evaluations[0][1], '生涯 · 绝世欧皇 · 暂估');
    else assert.ok(evaluations[0][1].endsWith('…'), 'long evaluations must ellipsize while keeping the same font size');
  }
  delete globalThis.document;
  delete globalThis.MobileAssets;
});
