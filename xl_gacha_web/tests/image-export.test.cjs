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
    fillRect: (...a) => ops.push(['fillRect', ...a]),
    fillText(...a) {ops.push(['fillText', ...a, this.font, this.textAlign]);},
    measureText: s => ({width: String(s).length * 10}),
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
  assert.equal(accountOp[4], 950);
  delete globalThis.document;
});

test('draw renders the new interface with a proportional logo, avatars and a two-row event layout', async () => {
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
  assert.deepEqual(draws[0].slice(2), [1080 - 40 - 128, 40, 128, 64]);
  for (const avatar of draws.slice(1)) {
    assert.deepEqual(avatar.slice(2, 6), [0, 0, 256, 256]);
    assert.deepEqual(avatar.slice(6), [60, avatar[7], 60, 60]);
  }
  const nameOp = canvas.ops.find(op => op[0] === 'fillText' && op[1] === '角色甲');
  assert.equal(nameOp[2], 132);
  const accountOp = canvas.ops.find(op => op[0] === 'fillText' && op[1] === '招募档案');
  assert.equal(accountOp[4], 1080 - 40 - 128 - 64 - 24);
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
    const w = Math.min(op[4], String(op[1]).length * 10);
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
  const tracks = rects.filter(r => r[1] === 132 && r[3] === 908 && r[4] === 36);
  assert.equal(tracks.length, 3);
  for (const track of tracks) {
    const at = ops.indexOf(track);
    const fill = ops.slice(at + 1).find(op => op[0] === 'fillRect' && op[1] === 132 && op[2] === track[2]);
    assert.ok(fill);
    assert.ok(fill[3] >= 105 && fill[3] <= 908);
    const count = texts.find(t => t.left === 148 && t.y === track[2] + 25);
    assert.ok(count, 'missing overlaid interval text');
    assert.ok(count.right <= 132 + fill[3]);
  }
  // event chips follow the name on the first row and stay inside the card
  for (const chip of rects.filter(r => r[4] === 30 && r[3] >= 26 && r[3] <= 300))
    assert.ok(chip[1] >= 132 && chip[1] + chip[3] <= 1040);
  // avatars stay left of the names
  for (const avatar of ops.filter(op => op[0] === 'drawImage').slice(1)) {
    assert.equal(avatar[6], 60);
    assert.ok(avatar[6] + avatar[8] <= 132);
  }
  // career pity track hosts its overlaid centered text
  const pityTracks = rects.filter(r => r[3] === 292 && r[4] === 22);
  assert.equal(pityTracks.length, 1);
  const pity = texts.find(t => String(t.s).startsWith('已垫 '));
  assert.ok(pity.left >= pityTracks[0][1] && pity.right <= pityTracks[0][1] + 292);
  assert.ok(pity.y <= pityTracks[0][2] + 22 && pity.y > pityTracks[0][2]);
  delete globalThis.document;
  delete globalThis.MobileAssets;
});

test('modern export uses real player identity, a larger logo and inline complete event tags', () => {
  const LongImage = freshLongImage();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
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
  assert.deepEqual(logo.slice(-2), [128, 64]);
  for (const text of ['UP', '大保底', '十连四金']) {
    const tag = texts.find(op => op[1] === text);
    assert.ok(tag, `missing ${text}`);
    assert.ok(tag[2] > 132 && tag[2] < 772, `${text} should follow the name`);
  }
  assert.equal(texts.some(op => String(op[1]).includes('本池第')), false);
  delete globalThis.document;
});

test('modern export wraps every career tag instead of dropping overflowing tags', () => {
  const LongImage = freshLongImage();
  globalThis.document = {createElement: () => mockCanvas(), getElementById: () => null};
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
});
