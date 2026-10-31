'use strict';

// Drawing from the report avoids remote scripts, screenshots of credentials, and DOM canvas limits.
const LongImage = (() => {
  const W = 1080, HEADER = 380, FOOTER = 100, POOL_BASE = 180, ROW = 66;
  function planPages(report, category = 'all', maximum = 10000, poolIds = null) {
    const pages = []; let page = {chunks: [], height: HEADER + FOOTER};
    const wanted = poolIds == null ? null : new Set([...poolIds].map(String));
    const pools = (report.analysis?.pools || []).filter(p =>
      (category === 'all' || p.category === category) && (!wanted || wanted.has(String(p.pool_id))));
    for (const pool of pools) {
      const events = [...pool.events].reverse(); let offset = 0;
      do {
        let capacity = Math.floor((maximum - page.height - POOL_BASE) / ROW);
        if (capacity < 1) {pages.push(page); page = {chunks: [], height: HEADER + FOOTER}; continue;}
        const chunk = events.slice(offset, offset + capacity);
        page.chunks.push({pool, events: chunk, continuation: offset > 0});
        page.height += POOL_BASE + Math.max(chunk.length, 1) * ROW;
        offset += chunk.length;
      } while (offset < events.length);
    }
    if (page.chunks.length || !pages.length) pages.push(page);
    if (pages.length > 100) throw new Error('记录太长，请先选择一个卡池分类后导出。');
    return pages;
  }

  // Local avatar/logo assets come from the optional global MobileAssets bridge (Android).
  // Everything degrades to the plain text/color layout when it is missing or a single image fails.
  const characterCache = new Map();
  let brandCache = null, brandResolved = false, assetsInitPromise = null;
  function assetsBridge() {
    return typeof MobileAssets !== 'undefined' && MobileAssets && typeof MobileAssets.init === 'function' ? MobileAssets : null;
  }
  function ensureAssetsInit(bridge) {
    if (!assetsInitPromise) assetsInitPromise = Promise.resolve().then(() => bridge.init());
    return assetsInitPromise.catch(() => {});
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
  async function prepare(report, pages, poolIds = null) {
    const bridge = assetsBridge();
    if (!bridge) return null;
    try {
      await ensureAssetsInit(bridge);
      const itemIds = new Set();
      for (const page of pages || []) for (const chunk of page.chunks || [])
        for (const event of chunk.events || [])
          if (event.item_id !== undefined && event.item_id !== null) itemIds.add(event.item_id);
      const characters = new Map();
      await Promise.all([...itemIds].map(async itemId => {
        const entry = await resolveCharacter(bridge, itemId);
        if (entry) characters.set(itemId, entry);
      }));
      let insights = null;
      try {
        if (typeof MobileInsights !== 'undefined' && MobileInsights && typeof MobileInsights.analyze === 'function')
          insights = MobileInsights.analyze(report, {category: detectCategory(report, poolIds)});
      } catch { insights = null; }
      return {brand: await resolveBrand(bridge), characters, insights};
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

  function headRect(image, crop) {
    if (crop === 'left') return [0, 0, 140, 140];
    const w = image.naturalWidth || image.width || 140, h = image.naturalHeight || image.height || 140;
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
    function label(value,x,y,size=26,color='#eef1f5',weight=400,max=1000) {
      c.font = `${weight} ${size}px "Microsoft YaHei", "PingFang SC", sans-serif`; c.fillStyle = color;
      let s = String(value);
      if (c.measureText(s).width > max) {
        while (s.length && c.measureText(s + '…').width > max) s = s.slice(0,-1);
        s += '…';
      }
      c.fillText(s,x,y,max);
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
    rect(0,0,W,canvas.height,P.bg); rect(40,40,6,56,P.accent);
    let logoW = 0;
    if (brand) {
      const img = brand.image, iw = img.naturalWidth || img.width || 56, ih = img.naturalHeight || img.height || 34;
      let lh = 36, lw = Math.round(lh*iw/ih);
      if (lw > 240) {lw = 240; lh = Math.round(lw*ih/iw);}
      logoW = lw;
      c.drawImage(img, W-40-lw, 68-lh/2, lw, lh);
    }
    label(report.account?.label || (report.preview_source ? '本地记录预览' : '招募档案'),64,80,38,P.gold,650,logoW?W-40-logoW-64-24:950);
    label(`星落 · 抽卡手记    ${category === 'all' ? '全部卡池' : (catNames[category] || category)}    ${index+1}/${total}`,42,124,24,P.muted);
    const evalText = v => !v ? '暂无记录' : v.status==='rated' ? v.label + (v.lowConfidence ? ' · 暂估' : '') :
      v.status==='small' ? '样本较少' : v.status==='zero' ? '暂未见金' : v.status==='unknown' ? '星级待确认' : '暂无记录';
    const toneColor = t => ({lucky:P.green,good:P.green,steady:P.gold,rough:P.rough,quiet:P.faint}[t] || P.faint);
    if (insights) {
      label(`生涯 · ${evalText(insights.career)}`,42,162,22,toneColor(insights.career?.tone),600,410);
      label(`近45天 · ${evalText(insights.recent)}`,470,162,22,toneColor(insights.recent?.tone),600,420);
    }
    for (const [i, key] of ['normal','limited','collection'].entries()) {
      const s = report.analysis?.categories[key]; if (!s) continue;
      const x = 40+i*338; rect(x,186,324,130,P.panel);
      label(s.name,x+16,218,22,P.text,600,140);
      label(`${s.pull_count} 抽`,x+16,258,32,P.accent,650,150);
      label(key === 'normal' ? `${s.five_count} 出卡` : `UP ${s.up_count} / 歪 ${s.off_count}`,x+170,242,20,P.text,500,138);
      const avg = s.average_five_interval === null || s.average_five_interval === undefined ? '—' : Number(s.average_five_interval).toFixed(1);
      label(`平均 ${avg}`,x+170,268,18,P.muted,400,138);
      rect(x+16,284,292,22,P.track);
      const hard = s.hard_pity || 70, since = s.since_five || 0;
      if (s.pull_count) rect(x+16,284,Math.round(292*Math.min(1,since/hard)),22,s.since_five_exact ? (key === 'collection' ? P.blue : P.teal) : P.gray);
      label(`已垫 ${s.pull_count ? `${s.since_five_exact ? '' : '≥'}${since}` : '—'} / ${s.hard_pity || '—'}`,x+162,300,17,P.text,650,146);
    }
    let tx = 42;
    for (const tag of insights?.tags || []) {
      const text = tag.count > 1 ? `${tag.text} ×${tag.count}` : tag.text;
      const w = Math.round(measure(text,19,650)) + 26;
      if (tx + w > 1038) break;
      const risky = tag.key === 'pity_70' || tag.key === 'off_streak';
      rect(tx,324,w,26,risky ? '#33201d' : '#173129');
      label(text,tx+13,342,19,risky ? P.rough : P.green,650,w-26);
      tx += w + 8;
    }
    label('五星间隔：绿 1–30 · 黄 31–60 · 红 61+ · 灰 前序未知',42,372,20,P.faint);
    let y = HEADER;
    const numerals = ['','单','双','三','四','五','六','七','八','九','十'];
    for (const part of page.chunks) {
      const p = part.pool, height = POOL_BASE - 16 + Math.max(part.events.length,1)*ROW;
      rect(40,y,1000,height,P.panel); rect(40,y,4,42,P.accent);
      label(p.pool_name + (part.continuation ? '（续）' : ''),60,y+39,30,P.text,650,748);
      rect(822,y+8,198,40,P.accent);
      label(`${p.pull_count} 抽`,842,y+36,26,P.onAccent,800,158);
      const ups = part.events.filter(e => e.outcome === 'up').length, offs = part.events.filter(e => e.outcome === 'off').length;
      let sub = `${p.category_name}  ·  ${p.first_date} — ${p.last_date}  ·  ${p.five_count ?? part.events.length} 个五星`;
      if (p.category === 'limited' || p.category === 'collection') sub += `  ·  UP ${ups} / 歪 ${offs}`;
      label(sub,60,y+73,21,P.muted,400,950);
      const burstByPos = new Map();
      for (const burst of insights?.multi?.byPool?.[String(p.pool_id)] || [])
        for (const pos of burst.positions || []) burstByPos.set(pos,burst);
      let ey = y+94;
      for (const e of part.events) {
        const entry = characters ? characters.get(e.item_id) : null;
        const avatar = entry && entry.image ? entry : null;
        const nameX = avatar ? 132 : 60, nameMax = avatar ? 290-nameX-12 : 218;
        if (avatar) c.drawImage(avatar.image, ...headRect(avatar.image, avatar.info.crop), 60, ey+2, 60, 60);
        label(e.name,nameX,ey+34,27,P.text,600,nameMax);
        const meta = `${e.time_beijing ? e.time_beijing.slice(0,10)+' · ' : ''}本池第 ${e.in_pool_pull ?? '—'} 抽${e.carried_pulls ? ` · 跨池 ${e.carried_pulls} 抽` : ''}`;
        label(meta,nameX,ey+60,17,P.faint,400,avatar ? 560 : 950);
        const count = e.pulls_since_five, exact = e.interval_exact, hard = p.hard_pity || 70;
        const fillW = Math.max(105, Math.min(470, count/hard*470));
        rect(290,ey+4,470,40,P.track);
        rect(290,ey+4,fillW,40,!exact ? P.gray : count<=30 ? P.teal : count<=60 ? P.yellow : P.red);
        label(`${exact ? '' : '≥'}${count} 抽`,302,ey+32,24,P.dark,800,fillW-20);
        const chips = [];
        if (e.outcome === 'up') chips.push(['UP',P.teal]); else if (e.outcome === 'off') chips.push(['歪',P.red]);
        const burst = burstByPos.get(e.in_pool_pull);
        if (burst) chips.push([`十连${burst.exact ? '' : '至少'}${numerals[burst.five_count] || burst.five_count}金`,P.blue]);
        if (e.guaranteed === true) chips.push(['大保底',P.gold]);
        if (e.guarantee_conflict) chips.push(['待核验',P.red]);
        if (!exact) chips.push(['前序不完整',P.gray]);
        let cx = 1040;
        for (const [text,bg] of chips.reverse()) {
          const w = Math.round(measure(text,19,700)) + 26;
          if (cx - w < 772) break;
          cx -= w;
          rect(cx,ey+13,w,30,bg);
          label(text,cx+13,ey+34,19,P.dark,700,w-26);
          cx -= 8;
        }
        ey += ROW;
      }
      if(!part.events.length) {label('可见记录中尚未出现五星角色。',60,ey+36,24,P.faint); ey += ROW;}
      label(`截至本池记录末 · 已垫 ${p.tail_exact?'':'≥'}${p.tail_since_five} 抽${p.next_guaranteed === true ? ' · 下个五星为大保底¹' : ''}`,60,ey+31,21,P.muted,400,950);
      y += height+16;
    }
    label('¹ 保底与间隔依据可见历史推断，不代表官方实时状态。',42,canvas.height-60,20,P.faint);
    label('本机存档未必涵盖全部生涯 · 玩家工具，非官方服务 · 最新记录在前',42,canvas.height-25,20,P.faint);
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
