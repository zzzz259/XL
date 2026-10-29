'use strict';

/* Derived entertainment labels only. Never changes records, pity or archive identity. */
((root) => {
  const DAY = 86400;
  const ratedCategories = new Set(['normal', 'limited', 'collection']);
  const goldNames = {2: '双金', 3: '三金', 4: '四金', 5: '五金', 6: '六金', 7: '七金', 8: '八金', 9: '九金', 10: '十金'};
  function batchLabel(count, exact = true) { return `十连${exact ? '' : '至少'}${goldNames[count] || `${count}金`}`; }
  function scope() { return {pulls: 0, five_count: 0, unknown_count: 0, excluded_pulls: 0, normalized_cost: 0}; }
  function finish(value) {
    const average = value.five_count && !value.unknown_count ? value.pulls / value.five_count : null;
    const result = {...value, average, low_confidence: value.pulls < 100 || value.five_count < 3};
    if (!value.pulls) return {...result, status: value.excluded_pulls ? 'excluded' : 'empty', grade: value.excluded_pulls ? '不参与评价' : '暂无记录', tone: 'quiet'};
    if (value.unknown_count) return {...result, status: 'unknown', grade: '星级待确认', tone: 'quiet'};
    if (value.pulls < 30) return {...result, status: 'small', grade: '样本较少', tone: 'quiet'};
    if (!value.five_count) return {...result, status: 'zero', grade: '暂未见金', tone: 'quiet'};
    const ratio = value.normalized_cost / value.five_count;
    const tier = ratio <= .3 + 1e-12 ? ['欧皇', 'lucky'] : ratio <= .5 + 1e-12 ? ['偏欧', 'good'] : ratio <= .7 + 1e-12 ? ['平稳', 'steady'] : ratio <= .9 + 1e-12 ? ['偏非', 'rough'] : ['待转运', 'rough'];
    return {...result, status: 'rated', grade: tier[0], tone: tier[1]};
  }
  function analyze(report, {now = Date.now() / 1000, category = 'all'} = {}) {
    const cutoff = now - 30 * DAY, recent = scope(), total = scope();
    const pools = new Map((report?.analysis?.pools || []).map(pool => [String(pool.pool_id), pool]));
    const unique = new Map();
    for (const record of report?.records || []) {
      let key = JSON.stringify([record.pool_id, record.timestamp, record.index || 0, record.record_id || 0]);
      if (!record.index && !record.record_id) key += JSON.stringify(record.drops);
      if (!unique.has(key)) unique.set(key, record);
    }
    const records = [...unique.values()].sort((a, b) => a.timestamp - b.timestamp || a.index - b.index || a.record_id - b.record_id || a.pool_id - b.pool_id);
    const offsets = new Map(), events = [], counts = {}, byPool = {};
    for (const record of records) {
      const id = String(record.pool_id), pool = pools.get(id) || report.pool_metadata?.[id] || {};
      const start = offsets.get(id) || 0;
      const quantity = record.drops.reduce((sum, drop) => sum + drop.count, 0);
      offsets.set(id, start + quantity);
      if (!Number.isFinite(record.timestamp) || record.timestamp > now || (category !== 'all' && pool.category !== category)) continue;
      const fiveCount = record.drops.reduce((sum, drop) => sum + (drop.rarity === 5 ? drop.count : 0), 0);
      const unknownCount = record.drops.reduce((sum, drop) => sum + (drop.rarity === null || drop.rarity === undefined ? drop.count : 0), 0);
      if (quantity === 10 && fiveCount >= 2) {
        let cursor = start;
        const positions = [], characters = [];
        for (const drop of record.drops) {
          if (drop.rarity === 5) {
            for (let i = 1; i <= drop.count; i++) positions.push(cursor + i);
            characters.push({name: drop.name, count: drop.count});
          }
          cursor += drop.count;
        }
        const exact = unknownCount === 0;
        const event = {pool_id: record.pool_id, record_id: record.record_id, timestamp: record.timestamp, time_beijing: record.time_beijing, five_count: fiveCount, exact, positions, characters};
        events.push(event); (byPool[id] ||= []).push(event);
        const key = `${fiveCount}${exact ? '' : '+'}`; counts[key] = (counts[key] || 0) + 1;
      }
      const eligible = ratedCategories.has(pool.category) && Number.isFinite(pool.hard_pity) && pool.hard_pity > 0;
      for (const target of record.timestamp >= cutoff ? [recent, total] : [total]) {
        if (!eligible) { target.excluded_pulls += quantity; continue; }
        target.pulls += quantity; target.five_count += fiveCount; target.unknown_count += unknownCount;
        target.normalized_cost += quantity / pool.hard_pity;
      }
    }
    return {recent: {...finish(recent), days: 30, from: cutoff, to: now}, total: finish(total), multi: {count: events.length, counts, events, by_pool: byPool}};
  }
  const api = {analyze, batchLabel};
  if (typeof module !== 'undefined') module.exports = api;
  root.MobileInsights = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
