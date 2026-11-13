'use strict';

// Drawing from the report avoids remote scripts, screenshots of credentials, and DOM canvas limits.
const LongImage = (() => {
  const W = 1080, HEADER = 380, FOOTER = 100, POOL_BASE = 180, ROW = 96;
  const BASE_HEADER = 904, TAG_TOP = 816, TAG_PITCH = 44, MOBILE_POOL_BASE = 224, MOBILE_ROW = 144;
  const FONT = '"Microsoft YaHei", "PingFang SC", sans-serif';
  function careerTagText(tag) {
    if (typeof MobileInsights !== 'undefined' && typeof MobileInsights.tagText === 'function') return MobileInsights.tagText(tag);
    const text = tag.key === 'up_140' ? '吃满140' : tag.text;
    return tag.count > 1 ? `${text}${tag.key === 'up_140' ? '*' : ' ×'}${tag.count}` : text;
  }
  function tagRows(tags, measure) {
    const rows = []; let row = [], width = 0;
    for (const tag of tags || []) {
      const text = careerTagText(tag);
      const w = Math.min(984, Math.ceil(measure(text)) + 32);
      if (row.length && width + 12 + w > 984) {rows.push({tags: row, width}); row = []; width = 0;}
      width += (row.length ? 12 : 0) + w; row.push({tag, text, width: w});
    }
    if (row.length) rows.push({tags: row, width});
    return rows;
  }
  function plannedHeaderHeight(report, poolIds) {
    let rows = 1;
    try {
      if (typeof MobileInsights !== 'undefined' && MobileInsights && typeof MobileInsights.analyze === 'function') {
        let context = null;
        try {context = typeof document !== 'undefined' ? document.createElement('canvas').getContext('2d') : null;} catch {}
        if (context) context.font = `650 22px ${FONT}`;
        const measure = text => context ? context.measureText(text).width : [...text].reduce((sum, ch) => sum + (ch.charCodeAt(0) > 127 ? 22 : 13), 0);
        rows = Math.max(1, tagRows(MobileInsights.analyze(report, insightOptions(report, poolIds))?.tags, measure).length);
      }
    } catch { rows = 1; }
    return BASE_HEADER + (rows - 1) * TAG_PITCH;
  }
  function planPages(report, category = 'all', maximum = 10000, poolIds = null) {
    const mobile = Boolean(assetsBridge()), headerHeight = mobile ? plannedHeaderHeight(report, poolIds) : HEADER;
    const rowHeight = mobile ? MOBILE_ROW : ROW, poolBase = mobile ? MOBILE_POOL_BASE : POOL_BASE;
    const newPage = () => ({chunks: [], height: headerHeight + FOOTER, headerHeight, rowHeight, poolBase});
    const pages = []; let page = newPage();
    const wanted = poolIds == null ? null : new Set([...poolIds].map(String));
    const pools = (report.analysis?.pools || []).filter(p =>
      (category === 'all' || p.category === category) && (!wanted || wanted.has(String(p.pool_id))));
    if (!Number.isFinite(maximum) || maximum < headerHeight + FOOTER + (pools.length ? poolBase + rowHeight : 0))
      throw new Error('图片高度不足以容纳记录，请增加高度或减少卡池。');
    for (const pool of pools) {
      const events = [...pool.events].reverse(); let offset = 0;
      do {
        let capacity = Math.floor((maximum - page.height - poolBase) / rowHeight);
        if (capacity < 1) {pages.push(page); page = newPage(); continue;}
        const chunk = events.slice(offset, offset + capacity);
        page.chunks.push({pool, events: chunk, continuation: offset > 0});
        page.height += poolBase + Math.max(chunk.length, 1) * rowHeight;
        offset += chunk.length;
        if (pages.length > 100) throw new Error('记录太长，请先选择一个卡池分类后导出。');
      } while (offset < events.length);
    }
    if (page.chunks.length || !pages.length) pages.push(page);
    if (pages.length > 100) throw new Error('记录太长，请先选择一个卡池分类后导出。');
    return pages;
  }

  // Local avatar/logo assets come from the optional global MobileAssets bridge (Android).
  // Everything degrades to the plain text/color layout when it is missing or a single image fails.
  const characterCache = new Map();
  let brandCache = null, brandResolved = false, activeBridge = null, activeManifest = null;
  function assetsBridge() {
    return typeof MobileAssets !== 'undefined' && MobileAssets && typeof MobileAssets.init === 'function' ? MobileAssets : null;
  }
  async function ensureAssetsInit(bridge) {
    const manifest = await bridge.init();
    // MobileAssets owns its init promise. Read it each time so a refreshed manifest invalidates our decoded entries too.
    if (bridge !== activeBridge || manifest !== activeManifest) {
      characterCache.clear(); brandCache = null; brandResolved = false;
      activeBridge = bridge; activeManifest = manifest;
    }
    return manifest;
  }
  async function resolveCharacter(bridge, itemId) {
    if (characterCache.has(itemId)) return characterCache.get(itemId);
    let entry = null;
    try {
      const info = await bridge.character(itemId);
      if (info && info.src && typeof bridge.loadImage === 'function') {
        const image = await bridge.loadImage(info.src);
        if (image) entry = {info, image};
      }
    } catch { entry = null; }
    characterCache.set(itemId, entry);
    return entry;
  }
  async function resolveBrand(bridge) {
    if (brandResolved) return brandCache;
    brandResolved = true;
    try {
      const info = typeof bridge.brand === 'function' ? await bridge.brand() : null;
      if (info && info.logo && typeof bridge.loadImage === 'function') {
        const image = await bridge.loadImage(info.logo);
        if (image) brandCache = {info, image};
      }
    } catch { brandCache = null; }
    return brandCache;
  }
  function detectCategory(report, poolIds) {
    if (!poolIds) return 'all';
    const wanted = new Set([...poolIds].map(String)), cats = new Set();
    for (const p of report?.analysis?.pools || []) if (wanted.has(String(p.pool_id))) cats.add(p.category);
    return cats.size === 1 ? [...cats][0] : 'all';
  }
  function insightOptions(report, poolIds) {
    return {category: detectCategory(report, poolIds), ...(poolIds == null ? {} : {poolIds: [...poolIds]})};
  }
  async function resolvePlayer(bridge, account) {
    if (typeof bridge.player !== 'function' || typeof bridge.loadImage !== 'function') return null;
    try {
      const info = await bridge.player(account), result = {head: null, frame: null, rank: info?.rank ?? null};
      await Promise.all(['head', 'frame'].map(async key => {
        try {if (info?.[key]?.src) result[key] = {info: info[key], image: await bridge.loadImage(info[key].src)};} catch {}
      }));
      return result;
    } catch {return null;}
  }
  function featuredIds(pool, manifest) {
    const direct = pool.featured_ids || manifest?.pool_meta?.[String(pool.pool_id)]?.featured_ids;
    if (direct?.length) return [...new Set(direct)];
    const names = new Set(pool.featured_names || []), ids = [];
    for (const [id, entry] of Object.entries(manifest?.characters || {}))
      if (names.has(entry.name)) ids.push(Number.isFinite(Number(id)) ? Number(id) : id);
    for (const event of pool.events || [])
      if (names.has(event.name) && event.item_id != null && !ids.some(id => String(id) === String(event.item_id))) ids.push(event.item_id);
    return ids;
  }
  async function prepare(report, pages, poolIds = null) {
    const bridge = assetsBridge();
    if (!bridge) return null;
    try {
      const manifest = await ensureAssetsInit(bridge).catch(() => null);
      const itemIds = new Set(), poolFeatured = new Map();
      for (const page of pages || []) for (const chunk of page.chunks || []) {
        const ids = featuredIds(chunk.pool, manifest); poolFeatured.set(String(chunk.pool.pool_id), ids);
        for (const id of ids) itemIds.add(id);
        for (const event of chunk.events || [])
          if (event.item_id !== undefined && event.item_id !== null) itemIds.add(event.item_id);
      }
      const characters = new Map();
      await Promise.all([...itemIds].map(async itemId => {
        const entry = await resolveCharacter(bridge, itemId);
        if (entry) characters.set(itemId, entry);
      }));
      let insights = null;
      try {
        if (typeof MobileInsights !== 'undefined' && MobileInsights && typeof MobileInsights.analyze === 'function')
          insights = MobileInsights.analyze(report, insightOptions(report, poolIds));
      } catch { insights = null; }
      const featured = new Map([...poolFeatured].map(([id, ids]) => [id, ids.map(itemId => ({...characters.get(itemId), itemId})).filter(entry => entry.image)]));
      const eventGrades = new Map();
      if (typeof MobileProbability !== 'undefined' && typeof MobileProbability.upCycles === 'function') {
        const cycles = MobileProbability.upCycles(report, {poolIds});
        for (const page of pages || []) for (const chunk of page.chunks || []) for (const event of chunk.events || [])
          eventGrades.set(event, MobileProbability.eventGrade({...event, ...cycles.get(event)}, chunk.pool.hard_pity, chunk.pool.category));
      }
      const [brand, player] = await Promise.all([resolveBrand(bridge), resolvePlayer(bridge, report.account)]);
      return {brand, player, characters, featured, insights, eventGrades};
    } catch { return null; }
  }

  const encoder = new TextEncoder();
  const crcTable = Array.from({length: 256}, (_, n) => {
    for (let k = 0; k < 8; k++) n = (n & 1) ? 0xedb88320 ^ (n >>> 1) : n >>> 1;
    return n >>> 0;
  });
  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) crc = crcTable[(crc ^ byte) & 255] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }
  async function makeZip(files) {
    const local = [], central = []; let offset = 0, size = 0;
    for (const file of files) {
      const bytes = new Uint8Array(await file.blob.arrayBuffer()), name = encoder.encode(file.name);
      const crc = crc32(bytes);
      const header = new Uint8Array(30 + name.length), h = new DataView(header.buffer);
      h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x800, true);
      h.setUint32(14, crc, true); h.setUint32(18, bytes.length, true); h.setUint32(22, bytes.length, true);
      h.setUint16(26, name.length, true); header.set(name, 30);
      const entry = new Uint8Array(46 + name.length), e = new DataView(entry.buffer);
      e.setUint32(0, 0x02014b50, true); e.setUint16(4, 20, true); e.setUint16(6, 20, true);
      e.setUint16(8, 0x800, true); e.setUint32(16, crc, true); e.setUint32(20, bytes.length, true);
      e.setUint32(24, bytes.length, true); e.setUint16(28, name.length, true); e.setUint32(42, offset, true); entry.set(name, 46);
      local.push(header, bytes); central.push(entry);
      offset += header.length + bytes.length; size += entry.length;
    }
    const end = new Uint8Array(22), e = new DataView(end.buffer);
    e.setUint32(0, 0x06054b50, true); e.setUint16(8, files.length, true); e.setUint16(10, files.length, true);
    e.setUint32(12, size, true); e.setUint32(16, offset, true);
    return new Blob([...local, ...central, end], {type: 'application/zip'});
  }

  function headRect(image, crop, cropRect) {
    const w = image.naturalWidth || image.width || 140, h = image.naturalHeight || image.height || 140;
    if (Array.isArray(cropRect) && cropRect.length === 4 && cropRect.every(Number.isFinite)) {
      const [x, y, rw, rh] = cropRect;
      if (x >= 0 && y >= 0 && rw > 0 && rh > 0 && x + rw <= w && y + rh <= h) return cropRect;
    }
    if (crop === 'left') return [0, 0, 140, 140];
    if (crop === 'center') return [0, 0, w, h];
    return [Math.max(0, (w - 140) / 2), Math.max(0, (h - 140) / 2), 140, 140];
  }

  function draw(report, page, index, total, category, assets) {
    const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = page.height;
    const c = canvas.getContext('2d');
    if (!c) throw new Error('当前浏览器无法生成图片，请更换浏览器。');
    const brand = assets && assets.brand && assets.brand.image ? assets.brand : null;
    const characters = assets && assets.characters ? assets.characters : null;
    const rect = (x,y,w,h,color) => {c.fillStyle = color; c.fillRect(x,y,w,h);};
    function label(value,x,y,size=26,color='#eef1f5',weight=400,max=1000,align='left') {
      c.font = `${weight} ${size}px "Microsoft YaHei", "PingFang SC", sans-serif`; c.fillStyle = color; c.textAlign = align;
      let s = String(value);
      if (c.measureText(s).width > max) {
        while (s.length && c.measureText(s + '…').width > max) s = s.slice(0,-1);
        s += '…';
      }
      c.fillText(s,x,y);
    }
    if (!assets) {
    rect(0,0,W,canvas.height,'#111820'); rect(40,40,6,56,'#d8bb86');
    if (brand) c.drawImage(brand.image, W-40-56, 51, 56, 34);
    label(report.account?.label || (report.preview_source ? '本地记录预览' : '招募档案'),64,80,38,'#f3e4c7',650,brand?W-40-56-64-24:950);
    label(`星落 · 抽卡手记    ${category === 'all' ? '全部卡池' : ({normal:'普通池',limited:'限定池',collection:'典藏池',beginner:'启程池',unknown:'未分类'}[category] || category)}    ${index+1}/${total}`,42,124,24,'#a9b7c8');
    label(`${report.summary.pull_count.toLocaleString()} 抽  ·  ${report.summary.rarity_counts['5'] || 0} 个五星  ·  ${Object.keys(report.summary.pool_pull_counts).length} 个卡池`,42,178,32,'#f3f5f8',600);
    for (const [i, key] of ['normal','limited','collection'].entries()) {
      const s = report.analysis?.categories[key]; if (!s) continue;
      const x = 40+i*338; rect(x,206,324,130,'#202b38');
      label(s.name,x+16,238,24,'#bdc9d6'); label(`${s.pull_count} 抽`,x+16,279,34,'#f4e4c4',650);
      label(`${s.five_count} 个五星${key !== 'normal' ? ` · ${s.up_count} UP / ${s.off_count} 歪` : ''}`,x+16,316,22,'#b4c0ce');
    }
    label('五星间隔：绿 1–30 · 金 31–60 · 红 61+ · 灰 前序未知',42,367,22,'#9eaebf');
    let y = HEADER;
    for (const part of page.chunks) {
      const p = part.pool, height = POOL_BASE - 16 + Math.max(part.events.length,1)*ROW;
      rect(40,y,1000,height,'#1c2632'); rect(40,y,4,42,'#d8bb86');
      label(p.pool_name + (part.continuation ? '（续）' : ''),60,y+39,30,'#f0f3f6',650,748);
      label(`${p.pull_count} 抽`,835,y+39,30,'#eed4a4',650,185);
      label(`${p.category_name}  ·  ${p.first_date} — ${p.last_date}`,60,y+73,22,'#a5b3c3');
      let ey = y+94;
      for (const e of part.events) {
        const entry = characters ? characters.get(e.item_id) : null;
        const avatar = entry && entry.image ? entry : null;
        const nameX = avatar ? 132 : 60, nameMax = avatar ? 290-nameX-12 : 218;
        if (avatar) c.drawImage(avatar.image, ...headRect(avatar.image, avatar.info.crop), 60, ey+3, 60, 60);
        label(e.name,nameX,ey+33,27,'#edf1f5',600,nameMax);
        const count = e.pulls_since_five, color = !e.interval_exact ? '#67778b' : count<=30 ? '#41c3aa' : count<=60 ? '#dfbd66' : '#e78372';
        rect(290,ey+6,470,41,'#131b25'); rect(290,ey+6,Math.max(105, Math.min(470,count/(p.hard_pity || 70)*470)),41,color);
        label(`${e.interval_exact ? '' : '≥'}${count} 抽`,302,ey+36,25,'#101921',650);
        let tags = e.outcome === 'up' ? 'UP' : e.outcome === 'off' ? '歪' : '';
        if(e.guaranteed === true) tags += ' · 大保底¹';
        if(!e.interval_exact) tags = '前序不完整';
        label(tags,780,ey+34,22,'#e2c38f',500,242);
        if(e.carried_pulls) label(`跨池 ${e.carried_pulls} 抽`,nameX,ey+57,18,'#93a4b8',400,nameMax);
        ey += ROW;
      }
      if(!part.events.length) {label('尚无五星记录',60,ey+36,25,'#9aabbe'); ey += ROW;}
      label(`截至本池记录末：距五星 ${p.tail_exact?'':'≥'}${p.tail_since_five} 抽${p.next_guaranteed === true ? ' · 下个五星为大保底¹' : ''}`,60,ey+31,22,'#aebdcd',400,950);
      y += height+16;
    }
    label('¹ 保底与间隔依据可见历史推断，不代表官方实时状态。',42,canvas.height-60,21,'#96a5b7');
    label('本机存档未必涵盖全部生涯 · 玩家工具，非官方服务 · 最新记录在前',42,canvas.height-25,21,'#96a5b7');
    return canvas;
    }
    // Modern interface (Android assets present): dark charcoal base, fluorescent yellow-green
    // interactive accents, a little gold, and green/yellow/red/gray semantic interval colors.
    const P = {bg:'#161c20', panel:'#20272b', track:'#10171b', text:'#f2f3f4', muted:'#a7afb5', faint:'#7e898f',
      accent:'#d8f553', gold:'#e4c789', teal:'#14c7a7', green:'#54e0b9', yellow:'#f0c84d', red:'#ff5d50',
      rough:'#ff9c91', blue:'#168ddd', gray:'#7c878e', dark:'#111a18', onAccent:'#162014'};
    const catNames = {normal:'普通池',limited:'限定池',collection:'典藏池',beginner:'启程池',unknown:'未分类'};
    const insights = assets.insights || null;
    const measure = (s,size,weight=400) => {c.font = `${weight} ${size}px "Microsoft YaHei", "PingFang SC", sans-serif`; return c.measureText(String(s)).width;};
    const headerH = page.headerHeight ?? BASE_HEADER, rowH = page.rowHeight ?? MOBILE_ROW, poolBase = page.poolBase ?? MOBILE_POOL_BASE;
    const plannedRows = Math.max(1, Math.round((headerH - BASE_HEADER) / TAG_PITCH) + 1);
    const containImage = (image, x, y, width, height) => {
      const iw = image.naturalWidth || image.width || width, ih = image.naturalHeight || image.height || height;
      const scale = Math.min(width / iw, height / ih), dw = iw * scale, dh = ih * scale;
      c.drawImage(image, x + (width-dw)/2, y + (height-dh)/2, dw, dh);
    };
    const portrait = (entry, x, y, size) => {
      const source = headRect(entry.image, entry.info?.crop, entry.info?.cropRect);
      const scale = Math.min(size/source[2], size/source[3]), dw = source[2]*scale, dh = source[3]*scale;
      c.drawImage(entry.image, ...source, x+(size-dw)/2, y+(size-dh)/2, dw, dh);
    };
    rect(0,0,W,canvas.height,P.bg);
    rect(48,24,984,3,P.accent);
    if (brand) {
      containImage(brand.image, 260,40,560,160);
    } else label('星 落',540,156,72,P.accent,750,560,'center');
    label('抽卡手记',540,258,46,P.text,700,900,'center');
    label(`${category === 'all' ? '全部卡池' : (catNames[category] || category)}  ·  第 ${index+1} / ${total} 张`,540,296,24,P.muted,400,900,'center');
    const account = report.account || {};
    const fallbackName = report.preview_source ? '本地记录预览' : '招募档案';
    const nickname = typeof MobileServices !== 'undefined' && typeof MobileServices.accountName === 'function' ? MobileServices.accountName(account,fallbackName) : account.nickname || fallbackName;
    const uid = account.game_uid || account.uid || '', level = account.level;
    const idParts = [];
    if (uid) idParts.push(`UID ${uid}`);
    if (level !== undefined && level !== null && level !== '') idParts.push(`Lv.${level}`);
    rect(48,326,984,116,P.panel);
    const player = assets.player, head = player?.head?.image ? player.head : null;
    if (head) {
      const textWidth = Math.min(696, Math.max(measure(nickname,34,650), measure(idParts.join(' · '),24)));
      const groupX = (W-104-24-textWidth)/2, unit = 104/130;
      // The game Head component is 130²: its 88² viewport starts at (21,21), and its 120² frame at (5,5).
      c.save(); c.beginPath(); c.arc(groupX+65*unit,332+65*unit,44*unit,0,Math.PI*2); c.clip();
      rect(groupX+21*unit,332+21*unit,88*unit,88*unit,P.track);
      portrait(head,groupX+21*unit,332+21*unit,88*unit);
      c.restore();
      if (player.frame?.image) containImage(player.frame.image,groupX+5*unit,332+5*unit,120*unit,120*unit);
      label(nickname,groupX+128,378,34,P.gold,650,696);
      if (idParts.length) label(idParts.join(' · '),groupX+128,414,24,P.muted,400,696);
    } else {
      label(nickname,540,idParts.length ? 376 : 396,34,P.gold,650,888,'center');
      if (idParts.length) label(idParts.join(' · '),540,414,24,P.muted,400,888,'center');
    }
    const summary = report.summary || {};
    label(`${Number(summary.pull_count || 0).toLocaleString()} 抽  ·  ${summary.rarity_counts?.['5'] || 0} 个五星  ·  ${Object.keys(summary.pool_pull_counts || {}).length} 个卡池`,540,490,32,P.text,650,936,'center');
    const evalText = v => !v ? '暂无记录' : v.status==='rated' ? v.label + (v.lowConfidence ? ' · 暂估' : '') :
      v.status==='small' ? '样本较少' : v.status==='zero' ? '暂未见金' : v.status==='unknown' ? '星级待确认' : '暂无记录';
    const toneColor = t => ({lucky:P.green,good:P.green,steady:P.gold,rough:P.rough,quiet:P.faint}[t] || P.faint);
    if (insights) {
      const evaluations = [['生涯',insights.career],['近45天',insights.recent]].map(([prefix,value]) => {
        let text = `${prefix} · ${evalText(value)}`;
        if (measure(text,32,600) > 450) {
          const chars = [...text];
          while (chars.length && measure(chars.join('')+'…',32,600) > 450) chars.pop();
          text = chars.join('')+'…';
        }
        return {text,width:measure(text,32,600),color:toneColor(value?.tone)};
      });
      let x = (W-evaluations[0].width-36-evaluations[1].width)/2;
      for (const evaluation of evaluations) {
        label(evaluation.text,x,534,32,evaluation.color,600,450);
        x += evaluation.width+36;
      }
    }
    for (const [i, key] of ['normal','limited','collection'].entries()) {
      const s = report.analysis?.categories?.[key] || {name:catNames[key],pull_count:0,five_count:0,up_count:0,off_count:0};
      const x = 48+i*336, cx = x+156;
      rect(x,558,312,232,P.panel);
      label(s.name,cx,596,26,P.text,600,264,'center');
      label(`${s.pull_count} 抽`,cx,644,38,P.accent,700,264,'center');
      if (key === 'normal') label(s.five_count,x+84,684,28,P.text,650,120,'center');
      else {
        label(s.up_count,x+54,684,28,P.teal,700,54,'center');
        label('/',x+84,684,24,P.muted,500,22,'center');
        label(s.off_count,x+114,684,28,P.red,700,54,'center');
      }
      label(key === 'normal' ? '出卡数' : 'UP/歪',x+84,714,21,P.muted,400,120,'center');
      const mean = key === 'normal' ? s.average_five_interval : s.up_count > 0 ? s.pull_count/s.up_count : null;
      const average = mean == null || !Number.isFinite(Number(mean)) ? '—' : Number(mean).toFixed(1);
      label(average,x+228,684,28,P.text,650,120,'center');
      label(key === 'normal' ? '五星平均' : '平均UP',x+228,714,21,P.muted,400,120,'center');
      rect(x+156,662,1,58,'#354044');
      rect(x+20,742,272,30,P.track);
      const hard = s.hard_pity || 70, since = s.since_five || 0;
      if (s.pull_count) rect(x+20,742,Math.round(272*Math.min(1,since/hard)),30,s.since_five_exact ? (key === 'collection' ? P.blue : P.teal) : P.gray);
      const pityText = `已垫 ${s.pull_count ? `${s.since_five_exact ? '' : '≥'}${since}` : '—'} / ${s.hard_pity || '—'}`;
      const guaranteed = key !== 'normal' && s.next_guaranteed === true;
      const textWidth = measure(pityText,21,650), startX = cx-(textWidth+(guaranteed ? 34 : 0))/2;
      label(pityText,startX,764,21,P.text,650,guaranteed ? 230 : 264);
      if (guaranteed) {
        const bx = startX+Math.min(textWidth,230)+8;
        rect(bx,747,26,22,'#343025');
        label('保',bx+13,764,18,P.gold,650,24,'center');
      }
    }
    const rows = tagRows(insights?.tags, text => measure(text,22,650));
    for (const [rowIndex, row] of rows.entries()) {
      let tx = (W-row.width)/2, ty = TAG_TOP+rowIndex*TAG_PITCH;
      for (const {tag,text,width} of row.tags) {
        const risky = ['guarantee_streak', 'up_130', 'up_140', 'off_streak'].includes(tag.key);
        rect(tx,ty,width,34,risky ? '#33201d' : '#173129');
        label(text,tx+16,ty+25,22,risky ? P.rough : P.green,650,width-32);
        tx += width+12;
      }
    }
    label('五星间隔：绿 1–30 · 黄 31–60 · 红 61+ · 灰 前序未知',540,TAG_TOP+Math.max(plannedRows,rows.length)*TAG_PITCH+16,22,P.faint,400,936,'center');
    let y = headerH;
    const numerals = ['','单','双','三','四','五','六','七','八','九','十'];
    for (const part of page.chunks) {
      const p = part.pool, height = poolBase-24+Math.max(part.events.length,1)*rowH;
      rect(48,y,984,height,P.panel); rect(48,y,5,80,P.accent);
      const featured = assets.featured?.get(String(p.pool_id)) || [];
      const shownFeatured = featured.slice(0,3);
      for (const [i,entry] of shownFeatured.entries()) portrait(entry,72+i*72,y+18,64);
      const titleX = shownFeatured.length ? 80+shownFeatured.length*72 : 72;
      label(p.pool_name + (part.continuation ? '（续）' : ''),titleX,y+59,32,P.text,650,820-titleX);
      rect(844,y+24,164,52,P.accent);
      label(`${p.pull_count} 抽`,926,y+60,28,P.onAccent,800,144,'center');
      label(`${p.category_name}  ·  ${p.first_date} — ${p.last_date}`,72,y+110,23,P.muted,400,936);
      const ups = (p.events || []).filter(e => e.outcome === 'up').length, offs = (p.events || []).filter(e => e.outcome === 'off').length;
      let sub = `${p.five_count ?? p.events?.length ?? 0} 个五星`;
      if (p.category === 'limited' || p.category === 'collection') sub += `  ·  UP ${ups} / 歪 ${offs}`;
      label(sub,72,y+146,23,P.muted,400,936);
      rect(72,y+162,936,1,'#354044');
      const burstByPos = new Map();
      for (const burst of insights?.multi?.byPool?.[String(p.pool_id)] || [])
        for (const pos of burst.positions || []) burstByPos.set(pos,burst);
      let ey = y+180;
      for (const [eventIndex,e] of part.events.entries()) {
        const burst = burstByPos.get(e.in_pool_pull), groupStart = burst && burstByPos.get(part.events[eventIndex-1]?.in_pool_pull) !== burst;
        if (groupStart) {
          let end = eventIndex+1;
          while (end < part.events.length && burstByPos.get(part.events[end].in_pool_pull) === burst) end++;
          const groupHeight = (end-eventIndex)*rowH-12, shade = c.createLinearGradient(0,ey,0,ey+groupHeight);
          shade.addColorStop(0,'rgba(20,199,167,0.20)');
          shade.addColorStop(.45,'rgba(20,199,167,0.11)');
          shade.addColorStop(1,'rgba(20,199,167,0.035)');
          rect(60,ey,960,groupHeight,shade);
          const continued = (burst.positions || []).some(pos => Number(pos) > Number(e.in_pool_pull));
          label(`十连${burst.exact ? '' : '至少'}${numerals[burst.five_count] || burst.five_count}金${continued ? '（续）' : ''}`,84,ey+22,22,P.green,700,700);
        }
        // A group's first row uses the existing 144px pitch for its header; it does not add a synthetic event.
        const contentY = ey+(groupStart ? 28 : 0);
        const entry = characters ? characters.get(e.item_id) : null;
        const avatar = entry && entry.image ? entry : null;
        const nameX = 184, nameMax = 266;
        rect(72,contentY+4,92,92,P.track);
        if (avatar) portrait(avatar,72,contentY+4,92);
        else label(String(e.name || '?').slice(0,1),118,contentY+65,38,P.faint,600,72,'center');
        label(e.name,nameX,contentY+35,30,P.text,600,nameMax);
        const nameW = Math.min(measure(e.name,30,600), nameMax);
        const chips = [];
        if (e.outcome === 'up') chips.push(['UP',P.teal]); else if (e.outcome === 'off') chips.push(['歪',P.red]);
        if (e.guaranteed === true) chips.push(['大保底',P.gold]);
        if (e.guarantee_conflict) chips.push(['待核验',P.red]);
        if (!e.interval_exact) chips.push(['前序不完整',P.gray]);
        const grade = assets.eventGrades?.get(e);
        if (grade === 'lucky') chips.push(['超欧',P.green]);
        else if (grade === 'rough') chips.push(['超非',P.red]);
        let chipX = nameX + nameW + 12;
        for (const [text,bg] of chips) {
          const w = Math.ceil(measure(text,21,700)) + 24;
          if (chipX+w > 1008) break;
          rect(chipX,contentY+9,w,32,bg);
          label(text,chipX+12,contentY+33,21,P.dark,700,w-24);
          chipX += w+8;
        }
        const count = e.pulls_since_five, exact = e.interval_exact, hard = p.hard_pity || 70;
        const countText = `${exact ? '' : '≥'}${count} 抽`, fillW = Math.min(824, Math.max(measure(countText,26,800)+32, count/hard*824));
        rect(184,contentY+58,824,42,P.track);
        rect(184,contentY+58,fillW,42,!exact ? P.gray : count<=30 ? P.teal : count<=60 ? P.yellow : P.red);
        label(countText,200,contentY+88,26,P.dark,800,fillW-32);
        if (e.carried_pulls) label(`跨池 ${e.carried_pulls} 抽`,1008,ey+(groupStart ? 22 : 122),20,P.muted,400,220,'right');
        ey += rowH;
      }
      if(!part.events.length) {label('可见记录中尚未出现五星角色。',72,ey+52,26,P.faint); ey += rowH;}
      label(`截至本池记录末 · 已垫 ${p.tail_exact?'':'≥'}${p.tail_since_five} 抽${p.next_guaranteed === true ? ' · 下个五星为大保底¹' : ''}`,72,ey+8,22,P.muted,400,936);
      y += height+24;
    }
    label('¹ 保底与间隔依据可见历史推断，不代表官方实时状态。',540,canvas.height-60,20,P.faint,400,984,'center');
    label('本机存档未必涵盖全部生涯 · 玩家工具，非官方服务 · 最新记录在前',540,canvas.height-25,20,P.faint,400,984,'center');
    return canvas;
  }

  async function deliver(files, selected, action, bridge, progress=()=>{}) {
    const chosen=files.filter((file,i)=>selected.has(i));
    if(!chosen.length)throw new Error('请先选择要操作的图片。');
    if(action==='share'){await bridge.shareImages(chosen);return {opened:true,count:chosen.length};}
    if(action!=='album')throw new Error('不支持的图片操作。');
    let completed=0;
    try {for(const file of chosen){await bridge.saveImage(file);progress(++completed);}}
    catch(error){error.completed=completed;throw error;}
    return {saved:true,count:completed};
  }
  return {planPages, makeZip, draw, deliver, prepare, headRect};
})();

if (typeof module !== 'undefined') module.exports = LongImage;
if (typeof document !== 'undefined' && document.getElementById('image-dialog')) {
  let urls = [], generating = false, delivering=false;
  const mobile=Boolean(window.AndroidBridge?.saveImage);
  if(mobile){$('image-zip').className='outline-button';$('image-zip').textContent='导出全部图片 ZIP';$('image-dialog').classList.add('mobile-images');}
  $('image-dialog').addEventListener('cancel',e=>{if(delivering)e.preventDefault();});
  $('image-close').addEventListener('click', () => $('image-dialog').close());
  $('image-dialog').addEventListener('close', () => {for(const url of urls) URL.revokeObjectURL(url); urls=[]; $('image-previews').replaceChildren();});
  $('export-image').addEventListener('click', async () => {
    if (!state.report || generating) return;
    generating = true; $('image-previews').replaceChildren(); $('image-zip').hidden = true;
    const report = state.report, category = state.category;
    $('image-status').textContent = '正在本机生成图片…'; $('image-dialog').showModal();
    try {
      await document.fonts.ready;
      const pages = LongImage.planPages(report, category), files=[],selected=new Set(),controls=[];
      const selectionText=node('span',''),toolbar=node('div',undefined,'image-actions');
      const updateSelection=()=>{selectionText.textContent=`已选 ${selected.size}/${files.length} 张`;};
      async function perform(action,indices=selected){
        if(delivering||generating)return;delivering=true;$('image-close').disabled=true;$('image-dialog').dataset.busy='true';
        for(const c of controls)c.disabled=true;
        $('image-status').textContent=action==='share'?'正在准备分享图片…':'正在保存到相册…';
        try {
          const result=await LongImage.deliver(files,new Set(indices),action,window.AndroidBridge,n=>{$('image-status').textContent=`已保存 ${n} 张到相册…`;});
          $('image-status').textContent=result.opened?'已打开系统分享，请选择应用并确认发送。':`已保存 ${result.count} 张到相册「星落抽卡手记」。`;
        }catch(error){
          const messages={STORAGE_PERMISSION_DENIED:'未获得保存图片权限。允许后可重试，或直接分享。',IMAGE_SHARE_TOO_LARGE:'图片较多，请减少选择后分批分享。',IMAGE_SHARE_FAILED:'无法打开分享面板，请重试。',IMAGE_INVALID:'图片过大或格式异常，请选择较少的记录重新生成。',FILE_SAVE_FAILED:'保存到相册失败，请检查剩余空间后重试。'};
          $('image-status').textContent=(error.completed?`已保存 ${error.completed} 张，后续保存中止。`:'')+(messages[error.message]||error.message||'图片操作失败，请重试。');
        }finally{delivering=false;$('image-close').disabled=false;delete $('image-dialog').dataset.busy;for(const c of controls)c.disabled=false;}
      }
      function button(text,handler,primary=false){const b=node('button',text,primary?'primary-button':'outline-button');b.type='button';b.disabled=true;b.addEventListener('click',handler);controls.push(b);return b;}
      if(mobile){
        toolbar.append(button('全选',()=>{for(let i=0;i<files.length;i++)selected.add(i);for(const c of $('image-previews').querySelectorAll('input[type=checkbox]'))c.checked=true;updateSelection();}),button('取消选择',()=>{selected.clear();for(const c of $('image-previews').querySelectorAll('input[type=checkbox]'))c.checked=false;updateSelection();}),selectionText,button('保存所选到相册',()=>perform('album'),true),button('分享所选',()=>perform('share'),true));
        $('image-previews').append(toolbar,node('p','分享只使用临时图片，不会额外保存到相册。多图数量和画质由接收应用决定。','image-help'));
      }
      const base = `星落-${(report.account?.label || '招募记录').replace(/[<>:"/\\|?*]/g,'_')}`;
      for (let i=0;i<pages.length;i++) {
        if (!$('image-dialog').open) break;
        const canvas = LongImage.draw(report,pages[i],i,pages.length,category);
        const blob = await new Promise(resolve => canvas.toBlob(resolve,'image/png'));
        canvas.width=canvas.height=1;
        if (!blob) throw new Error('图片生成失败，请缩小卡池分类后重试。');
        if (!$('image-dialog').open) break;
        const url=URL.createObjectURL(blob); urls.push(url);
        const name=`${base}-${String(i+1).padStart(2,'0')}.png`; files.push({name,blob});
        const item=node('div',undefined,'image-preview'), link=node('a',`下载第 ${i+1} 张 PNG（1080 × ${pages[i].height}）`);
        link.href=url; link.download=name; const details=node('details'), img=document.createElement('img');
        img.src=url; img.alt=`抽卡记录长图 ${i+1}`; details.append(node('summary','展开图片预览'),img);
        if(mobile){
          selected.add(i);const choose=document.createElement('input');choose.type='checkbox';choose.checked=true;choose.setAttribute('aria-label',`选择第 ${i+1} 张图片`);choose.addEventListener('change',()=>{choose.checked?selected.add(i):selected.delete(i);updateSelection();});controls.push(choose);
          const label=node('label',undefined,'image-choice');label.append(choose,document.createTextNode(`第 ${i+1} 张 · 1080 × ${pages[i].height}`));
          const actions=node('div',undefined,'image-actions');actions.append(button('保存到相册',()=>perform('album',[i])),button('分享',()=>perform('share',[i])));item.append(label,actions,details);updateSelection();
        }else item.append(link,details);
        $('image-previews').append(item);
        $('image-status').textContent=`已生成 ${i+1}/${pages.length} 张图片。`;
        await new Promise(resolve => requestAnimationFrame(resolve));
      }
      if(files.length && $('image-dialog').open) {
        $('image-zip').hidden=false;
        $('image-zip').onclick=async () => {
          $('image-zip').disabled=true;
          try {LocalArchive.save(await LongImage.makeZip(files),`${base}-长图.zip`);}
          catch {$('image-status').textContent='压缩包生成失败，仍可逐张下载 PNG。';}
          finally {$('image-zip').disabled=false;}
        };
      }
    } catch(error) {$('image-status').textContent=error.message || '图片生成失败。';}
    finally {generating=false;for(const b of $('image-previews').querySelectorAll('button'))b.disabled=false;}
  });
}
