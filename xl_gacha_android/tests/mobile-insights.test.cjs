const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = path.join(__dirname, '../web/mobile-insights.js');
const DAY = 86400, NOW = Date.UTC(2026, 9, 25) / 1000;
const low = count => ({item_id: 1, name: '低星', rarity: 3, count});
const gold = count => ({item_id: 2, name: '五星', rarity: 5, count});
const row = (id, days, drops, pool_id = 1) => ({pool_id, record_id: id, index: id, timestamp: NOW - days * DAY, time_beijing: '2026-10-20 12:00:00', drops});
const report = records => ({records, analysis: {pools: [
  {pool_id: 1, category: 'limited', hard_pity: 70},
  {pool_id: 2, category: 'normal', hard_pity: 70},
  {pool_id: 3, category: 'beginner', hard_pity: 10},
]}});
function insights() {
  assert.ok(fs.existsSync(source), 'Mobile luck and ten-pull insights have not been implemented');
  return require(source);
}

test('recent means a rolling 30-day window while lifetime keeps older archived pulls', () => {
  const ui = insights();
  const input = report([row(1, 31, [low(9), gold(1)]), row(2, 30, [gold(2), low(8)]), row(3, 1, [low(10)]), row(4, -1, [gold(10)])]);
  const original = structuredClone(input), value = ui.analyze(input, {now: NOW});
  assert.equal(value.recent.pulls, 20); assert.equal(value.recent.five_count, 2);
  assert.equal(value.total.pulls, 30); assert.equal(value.total.five_count, 3);
  assert.equal(value.recent.days, 30); assert.equal(value.total.average, 10);
  assert.deepEqual(input, original);
});

test('ratings respect categories, hard-pity normalization, sparse samples and unknown rarity', () => {
  const ui = insights();
  const input = report([row(1, 1, [low(38), gold(2)]), row(2, 1, [low(28), gold(2)], 2), row(3, 1, [gold(10)], 3)]);
  assert.equal(ui.analyze(input, {now: NOW, category: 'limited'}).total.grade, '欧皇');
  assert.equal(ui.analyze(input, {now: NOW}).total.pulls, 70);
  assert.equal(ui.analyze(input, {now: NOW, category: 'beginner'}).total.status, 'excluded');
  assert.equal(ui.analyze(report([row(1, 1, [gold(1)])]), {now: NOW}).total.status, 'small');
  assert.equal(ui.analyze(report([row(1, 40, [low(40)])]), {now: NOW}).recent.status, 'empty');
  assert.equal(ui.analyze(report([row(1, 1, [low(40)])]), {now: NOW}).total.grade, '暂未见金');
  const unknown = report([row(1, 1, [{item_id: 99, count: 1, rarity: null}, low(37), gold(2)])]);
  assert.equal(ui.analyze(unknown, {now: NOW}).total.status, 'unknown');
  const weaker = report([row(1, 1, [low(38), gold(2)])]); weaker.analysis.pools[0].hard_pity = 40;
  assert.equal(ui.analyze(weaker, {now: NOW}).total.grade, '偏欧');
});

test('double/triple gold is detected only inside one original ten-pull batch and never double-counted', () => {
  const ui = insights();
  const double = row(1, 1, [low(3), gold(2), low(5)]);
  const triple = row(2, 1, [gold(3), low(7)]);
  const input = report([double, structuredClone(double), triple, row(3, 1, [gold(1), low(4)]), row(4, 1, [gold(1), low(4)]), row(5, 1, [gold(2), low(18)])]);
  const value = ui.analyze(input, {now: NOW});
  assert.equal(value.multi.count, 2);
  assert.equal(value.multi.counts['2'], 1); assert.equal(value.multi.counts['3'], 1);
  assert.deepEqual(value.multi.by_pool['1'][0].positions, [4, 5]);
  assert.equal(value.total.pulls, 50);
  assert.equal(ui.batchLabel(2, true), '十连双金');
  assert.equal(ui.batchLabel(3, true), '十连三金');
});

test('a ten-pull with unknown rarity reports at least two gold rather than inventing an exact count', () => {
  const ui = insights();
  const input = report([row(1, 1, [gold(2), low(7), {item_id: 99, count: 1, rarity: null}])]);
  const event = ui.analyze(input, {now: NOW}).multi.events[0];
  assert.equal(event.exact, false);
  assert.equal(ui.batchLabel(event.five_count, event.exact), '十连至少双金');
});

test('entertainment grade thresholds include their documented boundaries', () => {
  const ui = insights();
  for (const [pulls, grade] of [[42, '欧皇'], [70, '偏欧'], [98, '平稳'], [126, '偏非'], [128, '待转运']]) {
    assert.equal(ui.analyze(report([row(1, 1, [low(pulls - 2), gold(2)])]), {now: NOW}).total.grade, grade);
  }
});
