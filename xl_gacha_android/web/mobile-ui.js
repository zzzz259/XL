'use strict';

/* Presentation only: all values and operations come from the existing archive engine. */
((root) => {
  const pages = new Set(['records', 'query', 'settings']);
  function initialPage({report, accounts = []}) { return report || accounts.length ? 'records' : 'query'; }
  function createNavigation(initial) {
    if (!pages.has(initial)) throw new Error('Unknown page');
    const history = [initial];
    return {
      get page() { return history[history.length - 1]; },
      go(page, replace = false) {
        if (!pages.has(page)) throw new Error('Unknown page');
        if (page !== history[history.length - 1]) {
          if (replace) {
            history.pop();
            if (history[history.length - 1] !== page) history.push(page);
          } else history.push(page);
        }
        return page;
      },
      back() { if (history.length < 2) return null; history.pop(); return history[history.length - 1]; },
    };
  }
  const intervalLabel = event => `${event.interval_exact ? '' : '≥'}${event.pulls_since_five} 抽`;
  const channelLabel = channel => channel === 'bilibili' ? 'B 服' : ['sms', 'taptap', 'haoplay_cn', 'official', 'qq', 'tap'].includes(channel) ? '官服' : channel === 'legacy' || !channel ? '导入存档' : `官服 · ${channel}`;
  const api = {initialPage, createNavigation, intervalLabel, channelLabel};
  if (typeof module !== 'undefined') module.exports = api;
  root.MobileUI = api;
  if (typeof document === 'undefined') return;

  const byId = id => document.getElementById(id);
  function el(tag, text, className) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
  }
  function badge(parent, text, kind = '') { parent.append(el('span', text, `event-badge ${kind}`)); }

  const insightCache = new WeakMap();
  function insightsFor(report, category) {
    if (!report) return root.MobileInsights.analyze(null, {category});
    const minute = Math.floor(Date.now() / 60000);
    let entry = insightCache.get(report);
    if (!entry || entry.minute !== minute) { entry = {minute, categories: new Map()}; insightCache.set(report, entry); }
    if (!entry.categories.has(category)) entry.categories.set(category, root.MobileInsights.analyze(report, {category}));
    return entry.categories.get(category);
  }
  function renderInsights(value) {
    for (const key of ['recent', 'total']) {
      const target = byId(`mobile-luck-${key}`), data = value[key];
      if (!target) continue;
      target.dataset.tone = data.tone;
      target.querySelector('strong').textContent = data.grade;
      target.querySelector('.luck-sample').textContent = data.status === 'excluded' ? '启程、未分类或规则未确认' : data.pulls ? `${data.pulls.toLocaleString()} 抽参与评价 · ${data.unknown_count ? '已知 ' : ''}${data.five_count} 个五星` : key === 'recent' ? '近 30 天暂无可评价记录' : '暂无可评价的已存档记录';
      target.querySelector('.luck-average').textContent = data.average === null ? '平均出金：—' : `平均出金 ${Number(data.average.toFixed(1))} 抽`;
      target.querySelector('.luck-confidence').textContent = data.status === 'unknown' ? '有未知星级，暂不评级' : data.status === 'small' ? '少于 30 抽，先积累记录' : data.status === 'rated' && data.low_confidence ? '小样本 · 评价暂估' : data.status === 'zero' ? '可见样本中尚未出现五星' : '';
    }
    const highlights = byId('mobile-multi-highlights'); if (!highlights) return;
    highlights.replaceChildren();
    if (!value.multi.count) highlights.append(el('span', '尚未发现十连多金记录', 'insight-empty'));
    else for (const [key, count] of Object.entries(value.multi.counts)) highlights.append(el('span', `${root.MobileInsights.batchLabel(Number.parseInt(key, 10), !key.endsWith('+'))} ×${count}`, 'multi-badge'));
  }

  function renderPools(report, category, target) {
    target.replaceChildren();
    const pools = (report?.analysis?.pools || []).filter(pool => category === 'all' || pool.category === category);
    byId('mobile-pool-count').textContent = `${pools.length} 个卡池`;
    const insights = insightsFor(report, category); renderInsights(insights);
    if (!pools.length) { target.append(el('p', '这个分类暂时没有可见记录。', 'pool-empty')); return; }
    // Pools remain expanded. Long histories append in batches without changing their order.
    let poolCursor = 0;
    const morePools = el('button', '', 'load-more'); morePools.type = 'button';
    function appendPools() {
      morePools.remove();
      const end = Math.min(poolCursor + 20, pools.length);
      for (; poolCursor < end; poolCursor++) target.append(poolCard(pools[poolCursor], insights.multi.by_pool[String(pools[poolCursor].pool_id)] || []));
      if (poolCursor < pools.length) { morePools.textContent = `继续显示卡池 · 还有 ${pools.length - poolCursor} 个`; target.append(morePools); }
    }
    morePools.addEventListener('click', appendPools); appendPools();
  }

  function poolCard(pool, bursts) {
    const card = el('article', undefined, `mobile-pool ${pool.category}`);
    const header = el('div', undefined, 'pool-top'), title = el('div');
    title.append(el('span', pool.category_name, 'pool-category'), el('h4', pool.pool_name), el('small', `${pool.first_date} — ${pool.last_date}`));
    const total = el('div', undefined, 'pool-total'); total.append(el('strong', Number(pool.pull_count).toLocaleString()), el('span', `抽 · ${pool.five_count} 个五星`));
    header.append(title, total); card.append(header);
    card.append(el('p', pool.featured_names?.length ? `当期五星 UP · ${pool.featured_names.join(' / ')}` : pool.category === 'normal' ? '常驻招募 · 不区分 UP / 歪' : pool.category === 'beginner' ? '启程招募 · 独立计数' : 'UP 配置未确认', 'pool-up'));
    const burstPositions = new Map(), burstCounts = new Map();
    for (const burst of bursts) {
      const label = root.MobileInsights.batchLabel(burst.five_count, burst.exact);
      burstCounts.set(label, (burstCounts.get(label) || 0) + 1);
      for (const position of burst.positions) burstPositions.set(position, burst);
    }
    if (bursts.length) {
      const chips = el('div', undefined, 'pool-highlights');
      for (const [label, count] of burstCounts) chips.append(el('span', `${label} ×${count}`, 'multi-badge'));
      card.append(chips);
    }
    const events = el('div', undefined, 'pool-events'); card.append(events);
    const visible = [...(pool.events || [])].reverse();
    if (!visible.length) events.append(el('p', '可见记录中尚未出现五星角色。', 'no-five'));
    let cursor = 0;
    const more = el('button', '', 'load-more'); more.type = 'button';
    function appendEvents() {
      more.remove();
      const end = Math.min(cursor + 20, visible.length);
      for (; cursor < end; cursor++) {
        const event = visible[cursor], row = el('div', undefined, 'mobile-event'), main = el('div', undefined, 'event-main');
        const emblem = el('span', '✦', 'character-emblem'); emblem.setAttribute('aria-hidden', 'true');
        const name = el('div', undefined, 'event-name'); name.append(el('strong', event.name), el('small', `本池第 ${event.in_pool_pull} 抽`));
        const track = el('div', undefined, 'mobile-event-track');
        const count = event.pulls_since_five;
        const tone = !event.interval_exact ? 'uncertain' : count <= 30 ? '' : count <= 60 ? 'gold' : 'red';
        const fill = el('div', intervalLabel(event), `mobile-event-fill ${tone}`);
        fill.style.width = `${Math.max(16, Math.min(100, count / (pool.hard_pity || 70) * 100))}%`;
        track.append(fill); main.append(emblem, name, track); row.append(main);
        const notes = el('div', undefined, 'mobile-event-notes');
        const burst = burstPositions.get(event.in_pool_pull);
        if (burst) {
          badge(notes, root.MobileInsights.batchLabel(burst.five_count, burst.exact), 'multi');
          notes.lastElementChild.title = `${burst.time_beijing || ''} · 同一条十抽记录 · ${burst.characters.map(character => `${character.name}×${character.count}`).join(' / ')}`;
        }
        if (event.outcome === 'up') badge(notes, 'UP', 'up');
        else if (event.outcome === 'off') badge(notes, '歪', 'off');
        else if (!['normal', 'beginner'].includes(pool.category)) badge(notes, 'UP 未知', 'uncertain');
        if (!event.interval_exact) badge(notes, '前序不完整', 'uncertain');
        if (event.guaranteed === true) badge(notes, '大保底¹', 'guarantee');
        if (event.guarantee_conflict || (event.interval_exact && pool.hard_pity && count > pool.hard_pity)) badge(notes, '记录待核验', 'off');
        if (event.carried_pulls) notes.append(el('span', `跨池累计 ${event.carried_pulls} 抽`));
        if (notes.childNodes.length) row.append(notes);
        events.append(row);
      }
      if (cursor < visible.length) { more.textContent = `继续显示五星记录 · 还有 ${visible.length - cursor} 条`; events.append(more); }
    }
    more.addEventListener('click', appendEvents); appendEvents();
    const tail = el('div', undefined, 'pool-tail');
    tail.append(el('span', `截至本池记录末 · 已垫 ${pool.tail_exact ? '' : '≥'}${pool.tail_since_five} 抽`));
    if (pool.next_guaranteed === true) tail.append(el('b', '下个五星为大保底¹'));
    card.append(tail);
    const roster = el('details', undefined, 'characters-detail');
    roster.append(el('summary', `全部获得角色 · ${pool.characters.length} 种`));
    const list = el('div', undefined, 'character-roster');
    let rosterCursor = 0;
    const moreCharacters = el('button', '继续显示角色', 'load-more'); moreCharacters.type = 'button';
    function appendCharacters() {
      moreCharacters.remove();
      const end = Math.min(rosterCursor + 40, pool.characters.length);
      for (; rosterCursor < end; rosterCursor++) {
        const character = pool.characters[rosterCursor];
        list.append(el('span', `${character.name} · ${character.rarity || '?'}★ ×${character.count}`, `roster-chip s${character.rarity || 0}`));
      }
      if (rosterCursor < pool.characters.length) list.append(moreCharacters);
    }
    moreCharacters.addEventListener('click', appendCharacters);
    roster.addEventListener('toggle', () => { if (roster.open && !rosterCursor) appendCharacters(); });
    roster.append(list); card.append(roster); return card;
  }
  root.GachaPresentation = {renderPools};

  function boot() {
    let library = [], navigation = createNavigation('query'), hasChosenPage = false;
    const scroll = {records: 0, query: 0, settings: 0};
    const headings = {records: '招募档案', query: '查询我的招募记录', settings: '我的手记'};
    function applyPage(page) {
      document.body.dataset.page = page;
      for (const view of document.querySelectorAll('[data-view]')) view.hidden = view.dataset.view !== page;
      for (const button of document.querySelectorAll('.bottom-nav [data-nav]')) {
        if (button.dataset.nav === page) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
      }
      document.title = `${headings[page]} · 星落手记`;
      requestAnimationFrame(() => { if (navigation.page === page) window.scrollTo(0, scroll[page]); });
    }
    function navigate(page, replace = false, resetScroll = false) {
      scroll[navigation.page] = window.scrollY;
      if (resetScroll) scroll[page] = 0;
      navigation.go(page, replace); hasChosenPage = true; applyPage(page);
    }
    api.navigate = navigate;
    api.back = () => {
      scroll[navigation.page] = window.scrollY;
      const previous = navigation.back();
      if (!previous) return false;
      applyPage(previous); return true;
    };
    function openDialog(id) {
      const dialog = byId(id);
      if (!dialog || dialog.open) return;
      for (const other of document.querySelectorAll('dialog[open]')) { if (other.dataset.busy === 'true') return; other.close(); }
      dialog.showModal();
    }
    document.addEventListener('click', event => {
      const nav = event.target.closest('[data-nav]'); if (nav) navigate(nav.dataset.nav);
      const opener = event.target.closest('[data-dialog]'); if (opener) openDialog(opener.dataset.dialog);
      const closer = event.target.closest('[data-close]'); if (closer) byId(closer.dataset.close).close();
    });
    // Close the account surface before the existing import handler opens its modal.
    byId('import-open').addEventListener('click', () => { if (!state.busy) byId('accounts-dialog').close(); }, true);
    byId('mobile-add-account').addEventListener('click', () => { byId('accounts-dialog').close(); navigate('query'); });
    byId('toggle-query').addEventListener('click', () => navigate('query'));

    function accountMeta(report, hideUid = true) {
      const account = report?.account;
      if (!account) return report ? '虚构演示 · 未连接游戏账号' : '尚未选择存档';
      const uid = String(account.uid || '');
      const shownUid = hideUid && uid.length > 5 ? `${uid.slice(0, 2)}•••${uid.slice(-3)}` : uid;
      return `${channelLabel(account.channel)}${shownUid ? ` · UID ${shownUid}` : ''}`;
    }
    function reflectAccount() {
      const report = state.report;
      const label = report?.account?.label || (state.demo ? '演示手记' : '本地存档');
      byId('mobile-account-name').textContent = label;
      byId('mobile-settings-account').textContent = report?.account?.label || '本地账号与备份';
      byId('mobile-account-meta').textContent = accountMeta(report);
      if (state.demo) byId('result-time').textContent = '虚构示例 · 不保存为个人账号存档';
      byId('mobile-selected-meta').textContent = report ? `${accountMeta(report, false)} · ${report.summary.pull_count.toLocaleString()} 抽 · ${report.storage === 'local' ? '已存到本机' : '预览数据'}` : '选择存档后可查看、重命名、导出或删除。删除前会再次确认。';
      if (report?.captured_at && !state.demo) {
        const at = new Date(report.captured_at);
        if (!Number.isNaN(at.getTime())) byId('mobile-selected-meta').textContent += `\n最近更新 ${at.toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false})}`;
      }
      byId('mobile-library-count').textContent = `${library.length} 个本地账号 · 切换 / 导入 / 备份`;
      for (const id of ['export-json', 'export-csv', 'clear-results']) byId(id).disabled = !report;
      byId('mobile-empty-title').textContent = library.length ? '选择一份招募手记' : '还没有记录的篇章';
      byId('mobile-empty-description').textContent = library.length ? `本机已有 ${library.length} 个账号存档。点击右上角，选择要查看的账号。` : '完成一次查询，或导入已有存档，把每一次相遇留在这里。';
    }
    function reflectCategory() {
      document.body.dataset.category = state.category;
      renderInsights(insightsFor(state.report, state.category));
      const keys = ['normal', 'limited', 'collection'];
      for (const card of byId('career-categories').children) card.hidden = state.category !== 'all' && !card.classList.contains(state.category);
      for (const [index, key] of keys.entries()) {
        const value = state.report?.analysis?.categories?.[key];
        const metric = byId('career-categories').children[index]?.querySelector('.category-metrics>div');
        if (value && metric) {
          metric.querySelector('strong').textContent = state.category === 'all' && key !== 'normal' ? `${value.five_count} / ${value.off_count}` : String(value.five_count);
          metric.querySelector('span').textContent = state.category === 'all' && key !== 'normal' ? '五星 / 歪' : '五星';
          const average = metric.nextElementSibling?.querySelector('strong');
          if (average) average.textContent = value.average_five_interval === null ? '—' : `${Number(value.average_five_interval.toFixed(1))}${state.category === 'all' ? '' : ' 抽'}`;
        }
      }
      let pity = byId('mobile-current-pity');
      if (!pity) { pity = el('div', undefined, 'current-pity'); pity.id = 'mobile-current-pity'; document.querySelector('.pool-history').insertBefore(pity, byId('pool-timeline')); }
      pity.replaceChildren();
      for (const key of keys) {
        const value = state.report?.analysis?.categories?.[key]; if (!value) continue;
        const item = el('div'); item.append(el('small', `${value.name}已垫`), el('strong', value.pull_count ? `${value.since_five_exact ? '' : '≥'}${value.since_five} 抽` : '暂无记录'));
        if (value.pull_count && value.hard_pity) {
          const track = el('div', undefined, 'pity-track'), fill = el('i'); fill.style.width = `${Math.min(100, value.since_five / value.hard_pity * 100)}%`; track.append(fill); item.append(track);
        }
        if (value.pull_count && value.next_guaranteed === true) item.append(el('small', '下个五星大保底¹'));
        else if (value.pull_count && key !== 'normal' && value.next_guaranteed === null) item.append(el('small', '大小保底未知'));
        pity.append(item);
      }
      pity.hidden = !state.report?.analysis;
    }
    byId('category-tabs').addEventListener('click', () => reflectCategory());
    document.addEventListener('library-changed', event => {
      library = event.detail.accounts || []; reflectAccount();
      for (const account of library) {
        const label = `${account.label} · ${channelLabel(account.channel)} · ${account.pull_count.toLocaleString()} 抽`;
        const option = [...byId('account-select').options].find(item => item.value === account.id);
        if (option) option.textContent = label;
      }
      if (!hasChosenPage && !state.report) { navigation = createNavigation(initialPage({accounts: library})); applyPage(navigation.page); }
    });
    document.addEventListener('report-shown', event => {
      reflectAccount(); reflectCategory(); renderPools(state.report, state.category, byId('pool-timeline'));
      if (!event.preserveView) {
        if (byId('accounts-dialog').open) byId('accounts-dialog').close();
        navigate('records', true, true);
      }
    });
    document.addEventListener('report-cleared', () => { reflectAccount(); navigate(initialPage({accounts: library}), true); });
    byId('export-image').addEventListener('click', () => {
      const labels = {all: '全部卡池', normal: '普通', limited: '限定', collection: '典藏', beginner: '启程', unknown: '未分类'};
      byId('mobile-image-scope').textContent = `${state.report?.account?.label || '招募记录'} · ${labels[state.category]} · 长记录自动分张`;
    });
    new MutationObserver(() => {
      const first = byId('image-previews').querySelector('.image-preview details');
      if (first && !first.dataset.previewReady) { first.dataset.previewReady = 'true'; first.open = true; }
    }).observe(byId('image-previews'), {childList: true});
    const syncBusy = () => {
      byId('other-login').disabled = byId('portal-start').disabled;
      const active = !byId('portal-cancel').hidden;
      for (const button of document.querySelectorAll('.bottom-nav button')) button.dataset.taskActive = active ? 'true' : 'false';
      document.querySelector('.bottom-nav [data-nav="query"]>span').textContent = active ? '查询中' : '查询';
    };
    new MutationObserver(syncBusy).observe(byId('portal-start'), {attributes: true, attributeFilter: ['disabled']});
    syncBusy(); reflectAccount();
    if (state.report) { reflectCategory(); renderPools(state.report, state.category, byId('pool-timeline')); navigate('records', true); }
    fetch('./build-info.json', {cache: 'no-store'}).then(response => response.ok ? response.json() : null).then(info => { if (info?.version_name) byId('mobile-version').textContent = info.version_name; }).catch(() => {});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true}); else boot();
})(typeof globalThis !== 'undefined' ? globalThis : this);
