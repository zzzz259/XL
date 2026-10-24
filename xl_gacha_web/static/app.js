'use strict';

const $ = (id) => document.getElementById(id);
const state = {report: null, rows: [], page: 1, pageSize: 20, busy: false, demo: false, category: 'all'};
const errors = {
  INVALID_INPUT: '请粘贴完整的 JWT 字符串，或官网 HP_2070021 / HP_2070023 的 JSON 值。',
  INVALID_CREDENTIAL_INPUT: '没有识别到有效的凭据格式。请复制 Value（值）的完整内容，不要复制键名或整张表格。',
  CREDENTIAL_EXPIRED: '这份凭据已过期。请在官网重新登录，再复制新的值。',
  WRONG_APP: '当前不支持这份凭据的应用 ID。已支持星落 2070021 和 TapTap 2070023，请使用教程中的官网入口。',
  APP_ID_MISMATCH: 'HP 包装键与 JWT 中的应用 ID 不一致。请直接复制官网 jwt 的完整值。',
  AMBIGUOUS_CREDENTIAL_INPUT: '提交内容包含多个登录会话。请只复制一个账号的 jwt 或一个 HP 条目的值。',
  DEVICE_MISMATCH: '提交内容中的设备字段不一致。请直接复制官网 jwt 的值。',
  PROVIDER_MISMATCH: '提交内容中的登录渠道不一致。请直接复制官网 jwt 的值。',
  ACTOR_NOT_FOUND: '认证已通过，但没有找到对应的游戏角色。请确认登录的是正确的星落账号。',
  GATEWAY_NETWORK_ERROR: '无法直连官方游戏网关，请检查网络连接或稍后重试。已保存的记录不受影响。',
  AUTH_NETWORK_ERROR: '暂时无法连接游戏认证服务，请稍后再试。',
  SERVER_MAINTENANCE: '服务端明确提示正在维护，请稍后再查询。',
  BUSY: '当前查询较多，请稍后再试。', RATE_LIMITED: '操作有些频繁，请一分钟后再试。',
  INPUT_TOO_LARGE: '提交内容过长。只需复制 jwt 或对应 HP 条目的值，无需其他条目。',
  ALL_HISTORY_QUERIES_REJECTED: '服务端没有接受抽卡历史查询，请稍后重试。',
  ORIGIN_NOT_ALLOWED: '当前页面地址与服务配置不一致，请使用站点提供的正式入口。',
};

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function status(text, kind = '') {
  const target = $('global-status') || $('status');
  target.hidden = !text;
  target.className = `status ${kind}`;
  target.textContent = text;
}

function showReport(report, isDemo = false, options = {}) {
  const previousCategory = state.category;
  document.body.classList.add('has-report');
  state.report = report;
  state.demo = isDemo;
  state.page = 1;
  state.category = 'all';
  $('empty-state').hidden = true;
  $('results').hidden = false;
  $('demo-label').hidden = !isDemo;
  $('partial-warning').hidden = !report.partial;
  const s = report.summary;
  const five = s.rarity_counts['5'] || 0;
  $('stat-pulls').textContent = s.pull_count.toLocaleString();
  $('stat-records').textContent = `${s.record_count} 条服务端记录`;
  $('stat-five').replaceChildren(document.createTextNode(String(five)), node('span', '✦'));
  $('stat-rate').textContent = `返回样本占比 ${s.pull_count ? (five / s.pull_count * 100).toFixed(2) : '0.00'}%`;
  $('stat-pools').textContent = String(Object.keys(s.pool_pull_counts).length);
  const dates = report.records.map(r => r.time_beijing.slice(0, 10)).sort();
  $('stat-dates').textContent = dates.length ? `${dates[0]} — ${dates[dates.length - 1]}` : '尚无返回记录';
  const captured = new Date(report.captured_at);
  const shownTime = Number.isNaN(captured.getTime()) ? '' : captured.toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false});
  $('account-title').textContent = report.account?.label || '招募记录';
  $('result-time').textContent = report.preview_source ? '本地 CSV 预览 · 未连接游戏账号' : isDemo ? '虚构示例，仅展示页面功能；没有连接任何游戏账号。' : `更新于 ${shownTime} · ${report.storage === 'local' ? '已保存到本机' : '尚未保存到本机'}`;
  $('rarity-bars').replaceChildren();
  for (let star = 5; star >= 1; star--) {
    const count = s.rarity_counts[String(star)] || 0;
    addBar($('rarity-bars'), `${star} ★`, count, s.pull_count, `rarity-${star}`, true);
  }
  if (s.rarity_counts.unknown) addBar($('rarity-bars'), '未映射', s.rarity_counts.unknown, s.pull_count, '', true);
  $('pool-bars').replaceChildren();
  for (const [pool, count] of Object.entries(s.pool_pull_counts).sort((a, b) => b[1] - a[1])) {
    addBar($('pool-bars'), pool, count, s.pull_count, '', false);
  }
  if (!Object.keys(s.pool_pull_counts).length) $('pool-bars').append(node('p', '本次查询没有返回抽卡记录。', 'muted'));
  $('pool-filter').replaceChildren(new Option('全部卡池', ''));
  for (const name of Object.keys(s.pool_pull_counts)) $('pool-filter').append(new Option(name, name));
  $('rarity-filter').value = '';
  $('search').value = '';
  state.rows = [];
  for (const record of [...report.records].reverse()) {
    for (const drop of [...record.drops].reverse()) state.rows.push({record, drop});
  }
  renderTable();
  renderCareer();
  if (options.preserveView && previousCategory !== 'all') {
    [...$('category-tabs').children].find(button => button.dataset.category === previousCategory)?.click();
  }
  const shown = new CustomEvent('report-shown', {detail: report});
  shown.preserveView = Boolean(options.preserveView);
  document.dispatchEvent(shown);
}

function renderCareer() {
  const analysis = state.report?.analysis;
  $('career-categories').replaceChildren(); $('extra-categories').replaceChildren();
  $('category-tabs').replaceChildren(); $('pool-timeline').replaceChildren();
  if (!analysis) {
    $('pool-timeline').append(node('p', '请重新查询，以生成生涯和卡池出货统计。', 'muted'));
    return;
  }
  const categories = analysis.categories;
  for (const key of ['normal', 'limited', 'collection']) {
    const value = categories[key];
    const card = node('article', undefined, `career-category ${key}`);
    card.append(node('span', value.name, 'category-name'));
    const count = node('strong', value.pull_count.toLocaleString()); count.append(node('small', '抽')); card.append(count);
    const stats = node('div', undefined, 'category-metrics');
    stats.append(metric('五星', String(value.five_count)), metric('平均抽数', value.average_five_interval === null ? '—' : `${Number(value.average_five_interval.toFixed(1))} 抽`));
    if (key !== 'normal') stats.append(metric('UP/歪', `${value.up_count} / ${value.off_count}`), metric('大保底出货¹', String(value.guaranteed_count)));
    else stats.append(metric('五星率', value.pull_count ? `${(value.five_count / value.pull_count * 100).toFixed(2)}%` : '—'));
    card.append(stats);
    const pending = node('div', undefined, 'pity-status');
    pending.append(node('span', `距五星 ${value.since_five_exact ? '' : '≥'}${value.since_five} 抽`));
    if (!value.pull_count) pending.replaceChildren(node('span', '暂无返回记录'));
    else if (value.next_guaranteed === true) pending.append(node('b', '下个五星为大保底¹'));
    else if (key !== 'normal' && value.next_guaranteed === null) pending.append(node('span', '大小保底未知', 'uncertain'));
    card.append(pending); $('career-categories').append(card);
  }
  for (const key of ['beginner', 'unknown']) {
    if (categories[key]?.pull_count) $('extra-categories').append(node('span', `${categories[key].name}另计 ${categories[key].pull_count} 抽 · ${categories[key].five_count} 个五星`));
  }
  $('extra-categories').append(node('span', '¹ 历史记录推断，非实时保底。', 'inference-note'));
  state.category = 'all';
  const entries = [['all', '全部卡池'], ['normal', '普通'], ['limited', '限定'], ['collection', '典藏'], ['beginner', '启程'], ['unknown', '未分类']];
  for (const [key, label] of entries) {
    if (key !== 'all' && !categories[key]?.pull_count && !['normal', 'limited', 'collection'].includes(key)) continue;
    const button = node('button', label, key === 'all' ? 'active' : '');
    button.type = 'button'; button.dataset.category = key; button.setAttribute('aria-pressed', String(key === 'all'));
    button.addEventListener('click', () => {state.category = key; document.querySelectorAll('#category-tabs button').forEach(b => {b.classList.toggle('active', b === button); b.setAttribute('aria-pressed', String(b === button));}); renderPools();});
    $('category-tabs').append(button);
  }
  renderPools();
}

function metric(label, value) {
  const result = node('div'); if (label === '平均抽数') result.title = '可见总抽数 ÷ 五星数量，包含首个五星及当前垫抽；无五星时显示 —'; result.append(node('strong', value), node('span', label)); return result;
}

function renderPools() {
  if (window.GachaPresentation?.renderPools) { window.GachaPresentation.renderPools(state.report, state.category, $('pool-timeline')); return; }
  $('pool-timeline').replaceChildren();
  const pools = (state.report?.analysis?.pools || []).filter(pool => state.category === 'all' || pool.category === state.category);
  if (!pools.length) {$('pool-timeline').append(node('div', '这个分类暂时没有返回记录。', 'pool-empty')); return;}
  for (const pool of pools) {
    const card = node('article', undefined, `banner-card ${pool.category}`);
    const header = node('div', undefined, 'banner-heading'); const title = node('div');
    const kicker = node('div', undefined, 'banner-kicker'); kicker.append(node('span', pool.category_name), node('small', `${pool.first_date} — ${pool.last_date} · 记录日期`));
    title.append(kicker, node('h4', pool.pool_name));
    title.append(node('p', pool.featured_names.length ? `当期五星 UP · ${pool.featured_names.join(' / ')}` : pool.category === 'normal' ? '常驻角色招募 · 不区分 UP / 歪' : pool.category === 'beginner' ? '启程招募 · 独立计数' : 'UP 配置未确认', 'banner-up'));
    header.append(title); const total = node('div', undefined, 'banner-total'); total.append(node('strong', pool.pull_count.toLocaleString()), node('span', `抽 / ${pool.five_count} 个五星`)); header.append(total); card.append(header);
    const events = node('div', undefined, 'banner-events');
    if (!pool.events.length) events.append(node('p', '本池返回记录中尚未出现五星角色。', 'no-five'));
    // Reverse presentation only: intervals and guarantees are computed oldest-first.
    for (const event of [...pool.events].reverse()) {
      const line = node('div', undefined, 'event-row'); const label = node('div', undefined, 'event-character');
      label.append(node('span', '✦', 'character-star'), node('strong', event.name));
      label.append(node('small', `本池第 ${event.in_pool_pull} 抽`)); line.append(label);
      const track = node('div', undefined, 'event-track');
      const count = event.pulls_since_five;
      const fill = node('div', undefined, `event-fill ${!event.interval_exact ? 'uncertain' : count <= 30 ? 'green' : count <= 60 ? 'gold' : 'red'}`);
      fill.style.width = `${Math.max(12, Math.min(100, count / (pool.hard_pity || 70) * 100))}%`;
      fill.append(node('b', `${event.interval_exact ? '' : '≥'}${count} 抽`));
      track.append(fill); line.append(track);
      const badges = node('div', undefined, 'event-badges');
      if (event.outcome === 'up') badges.append(node('span', 'UP', 'event-badge up'));
      if (event.outcome === 'off') badges.append(node('span', '歪', 'event-badge off'));
      if (event.guaranteed === true) badges.append(node('span', '大保底¹', 'event-badge guarantee'));
      if (event.guarantee_conflict || (event.interval_exact && pool.hard_pity && count > pool.hard_pity)) badges.append(node('span', '记录待核验', 'event-badge off'));
      if (!event.interval_exact) {const badge = node('span', '前序不完整', 'event-badge uncertain'); badge.title = '首个可见五星或记录断档，此间隔只能给出下界。'; badges.append(badge);}
      line.append(badges); events.append(line);
      if (event.carried_pulls) events.append(node('p', `跨池累计 ${event.carried_pulls} 抽`, 'carry-note'));
    }
    card.append(events);
    const tail = node('div', undefined, 'banner-tail');
    tail.append(node('span', `截至本池记录末：距上个五星 ${pool.tail_exact ? '' : '≥'}${pool.tail_since_five} 抽`));
    if (pool.next_guaranteed === true) tail.append(node('b', '下个五星为大保底¹'));
    card.append(tail);
    const characters = node('details', undefined, 'characters-detail');
    characters.append(node('summary', `本池获得的全部角色 · ${pool.characters.length} 种`));
    const roster = node('div', undefined, 'character-roster');
    for (const character of pool.characters) roster.append(node('span', `${character.name} · ${character.rarity || '?'}★ ×${character.count}`, `roster-chip s${character.rarity || 0}`));
    characters.append(roster); card.append(characters); $('pool-timeline').append(card);
  }
}

function addBar(container, label, count, total, className, percentage) {
  const row = node('div', undefined, 'bar-row');
  row.append(node('span', label, 'bar-label'));
  const track = node('div', undefined, 'bar-track');
  const fill = node('div', undefined, `bar-fill ${className}`);
  fill.style.width = `${total ? Math.max(count > 0 ? 1 : 0, count / total * 100) : 0}%`;
  track.append(fill); row.append(track);
  row.append(node('span', percentage ? `${count} / ${total ? (count / total * 100).toFixed(1) : '0'}%` : `${count} 抽`, 'bar-count'));
  container.append(row);
}

function renderTable() {
  const search = $('search').value.trim().toLocaleLowerCase();
  const pool = $('pool-filter').value;
  const rarity = $('rarity-filter').value;
  const rows = state.rows.filter(({record, drop}) => (!pool || record.pool_name === pool)
    && (!rarity || String(drop.rarity) === rarity) && (!search || drop.name.toLocaleLowerCase().includes(search)));
  const pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
  state.page = Math.min(state.page, pages);
  const start = (state.page - 1) * state.pageSize;
  $('history-body').replaceChildren();
  for (const {record, drop} of rows.slice(start, start + state.pageSize)) {
    const tr = node('tr');
    tr.append(node('td', record.time_beijing), node('td', record.pool_name), node('td', drop.name));
    const td = node('td'); td.append(node('span', drop.rarity ? `${drop.rarity} ★` : '未映射', `star-badge s${drop.rarity || 0}`));
    tr.append(td, node('td', `× ${drop.count}`)); $('history-body').append(tr);
  }
  if (!rows.length) {
    const tr = node('tr'); const td = node('td', state.rows.length ? '没有符合筛选条件的记录。' : '本次服务端未返回抽卡记录。');
    td.colSpan = 5; td.style.textAlign = 'center'; td.style.padding = '35px'; tr.append(td); $('history-body').append(tr);
  }
  $('filtered-count').textContent = `${rows.length} 项掉落`;
  $('page-description').textContent = rows.length ? `显示 ${start + 1}–${Math.min(start + state.pageSize, rows.length)} 项，共 ${rows.length} 项` : '共 0 项';
  $('page-number').textContent = `${state.page} / ${pages}`;
  $('prev-page').disabled = state.page === 1;
  $('next-page').disabled = state.page === pages;
}

function clearResults() {
  document.body.classList.remove('has-report', 'show-query');
  state.report = null; state.rows = []; state.demo = false;
  $('results').hidden = true; $('empty-state').hidden = false;
  $('history-body').replaceChildren(); $('rarity-bars').replaceChildren(); $('pool-bars').replaceChildren();
  $('pool-filter').replaceChildren(new Option('全部卡池', '')); $('search').value = '';
  $('pool-timeline').replaceChildren(); $('career-categories').replaceChildren(); $('extra-categories').replaceChildren();
  document.dispatchEvent(new CustomEvent('report-cleared'));
}

function download(kind) {
  if (!state.report) return;
  let content, mime;
  if (kind === 'json') {content = JSON.stringify(state.report, null, 2); mime = 'application/json';}
  else {
    const rows = [['时间（北京时间）', '卡池', '记录序号', '记录ID', '抽次（该记录内）', '物品ID', '角色/物品', '初始星级', '数量', '卡池ID', '存档ID', '账号名称', '账号UID', '登录渠道', '卡池分类']];
    const a = state.report.account || {};
    const categories = Object.fromEntries((state.report.analysis?.pools || []).map(p => [p.pool_id, p.category]));
    for (const r of state.report.records) r.drops.forEach((d, i) => rows.push([r.time_beijing, r.pool_name, r.index, r.record_id, i + 1, d.item_id, d.name, d.rarity ?? '', d.count, r.pool_id, a.id || '', a.label || '', a.uid || '', a.channel || '', categories[r.pool_id] || 'unknown']));
    const escape = value => {let s = String(value); if (typeof value === 'string' && /^[=+@\-\t\r]/.test(s)) s = "'" + s; return `"${s.replaceAll('"', '""')}"`;};
    content = '\ufeff' + rows.map(row => row.map(escape).join(',')).join('\r\n'); mime = 'text/csv;charset=utf-8';
  }
  const url = URL.createObjectURL(new Blob([content], {type: mime}));
  const a = document.createElement('a'); a.href = url;
  a.download = `${state.demo ? 'demo-' : ''}starfall-gacha-${(state.report.account?.label || '记录').replace(/[<>:"/\\|?*]/g, '_')}-${new Date().toISOString().slice(0, 10)}.${kind}`;
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$('query-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (state.busy) return;
  if (!$('credential').value.trim()) {status('请先粘贴本人账号的登录凭据，或使用官网登录查询。', 'error'); $('credential').focus(); return;}
  if (!$('consent').checked) {status('请先确认这是你本人的账号，并同意查询抽卡历史。', 'error'); $('consent').focus(); return;}
  state.busy = true;
  $('submit').disabled = true; $('demo').disabled = true;
  $('submit-label').textContent = '正在查询，请稍候'; $('submit-arrow').textContent = '…';
  let credential = $('credential').value.trim(); $('credential').value = '';
  $('credential').classList.add('masked'); $('visibility').textContent = '显示内容'; $('visibility').setAttribute('aria-pressed', 'false');
  const started = Date.now();
  status('正在验证凭据并查询抽卡历史，请勿重复提交。', 'loading');
  const timer = setInterval(() => status(`正在查询抽卡历史，已等待 ${Math.floor((Date.now() - started) / 1000)} 秒…`, 'loading'), 1000);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120000);
  try {
    const request = fetch('/api/query', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({credential}), cache: 'no-store', credentials: 'omit', signal: controller.signal});
    credential = '';
    const response = await request;
    const body = await response.json();
    if (!response.ok) {
      const code = typeof body.error === 'string' ? body.error : 'QUERY_FAILED';
      const message = errors[code] || (code.startsWith('AUTH_REJECTED_') ? '官网凭据未被认证服务接受，请重新登录官网并复制新的凭据。' : '查询未能完成，请稍后重试。若持续失败，请联系站点维护者。');
      throw new Error(message);
    }
    if (!body.summary || !Array.isArray(body.records)) throw new Error('服务端返回的数据格式不正确，请稍后再试。');
    showReport(body);
    clearInterval(timer);
    status(body.partial ? '已保存成功返回的记录；部分分组未成功，建议稍后重试。' : `已合并并保存到本机：${body.summary.record_count} 条记录，共 ${body.summary.pull_count} 抽。`);
    $('results').scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch (error) {
    clearInterval(timer);
    status(error.name === 'AbortError' ? '查询等待超时，请稍后再试。' : error instanceof TypeError ? '无法连接查询服务，请检查网页服务是否正在运行。' : error.message, 'error');
  } finally {
    credential = ''; clearInterval(timer); clearTimeout(timeout);
    state.busy = false; $('submit').disabled = false; $('demo').disabled = false;
    $('submit-label').textContent = '查询我的抽卡记录'; $('submit-arrow').textContent = '↗';
  }
});

$('visibility').addEventListener('click', () => {
  const hidden = $('credential').classList.toggle('masked');
  $('visibility').textContent = hidden ? '显示内容' : '隐藏内容';
  $('visibility').setAttribute('aria-pressed', String(!hidden));
});
document.querySelectorAll('.guide-open').forEach(button => button.addEventListener('click', () => $('guide-dialog').showModal()));
['close-guide', 'guide-done'].forEach(id => $(id).addEventListener('click', () => $('guide-dialog').close()));
$('guide-dialog').addEventListener('click', event => {if (event.target === $('guide-dialog')) {const r = event.target.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.target.close();}});
['search', 'pool-filter', 'rarity-filter'].forEach(id => $(id).addEventListener(id === 'search' ? 'input' : 'change', () => {state.page = 1; renderTable();}));
$('prev-page').addEventListener('click', () => {state.page--; renderTable();});
$('next-page').addEventListener('click', () => {state.page++; renderTable();});
$('export-csv').addEventListener('click', () => download('csv'));
$('export-json').addEventListener('click', () => download('json'));
$('clear-results').addEventListener('click', () => {clearResults(); status('');});

$('toggle-query').addEventListener('click', () => {
  const expanded = document.body.classList.toggle('show-query');
  $('toggle-query').textContent = expanded ? '收起查询' : '重新查询';
  $('toggle-query').setAttribute('aria-expanded', String(expanded));
});

if (new URLSearchParams(location.search).get('preview') === '1') {
  fetch('/preview.json', {cache: 'no-store'}).then(r => {
    if (!r.ok) throw new Error(); return r.json();
  }).then(report => showReport(report)).catch(() => status('本地预览数据未加载，请检查服务的预览配置。', 'error'));
}

$('demo').addEventListener('click', async () => {
  $('credential').value = ''; status('');
  try {
    const response = await fetch('/demo.json', {cache: 'no-store'});
    if (!response.ok) throw new Error();
    showReport(await response.json(), true);
    $('results').scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch {status('演示暂时无法加载，请稍后重试。', 'error');}
});
