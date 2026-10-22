'use strict';

// Drawing from the report avoids remote scripts, screenshots of credentials, and DOM canvas limits.
const LongImage = (() => {
  const W = 1080, HEADER = 380, FOOTER = 100, POOL_BASE = 180, ROW = 66;
  function planPages(report, category = 'all', maximum = 10000) {
    const pages = []; let page = {chunks: [], height: HEADER + FOOTER};
    const pools = (report.analysis?.pools || []).filter(p => category === 'all' || p.category === category);
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

  function draw(report, page, index, total, category) {
    const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = page.height;
    const c = canvas.getContext('2d');
    if (!c) throw new Error('当前浏览器无法生成图片，请更换浏览器。');
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
    rect(0,0,W,canvas.height,'#111820'); rect(40,40,6,56,'#d8bb86');
    label(report.account?.label || (report.preview_source ? '本地记录预览' : '招募档案'),64,80,38,'#f3e4c7',650,950);
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
        label(e.name,60,ey+33,27,'#edf1f5',600,218);
        const count = e.pulls_since_five, color = !e.interval_exact ? '#67778b' : count<=30 ? '#41c3aa' : count<=60 ? '#dfbd66' : '#e78372';
        rect(290,ey+6,470,41,'#131b25'); rect(290,ey+6,Math.max(105, Math.min(470,count/(p.hard_pity || 70)*470)),41,color);
        label(`${e.interval_exact ? '' : '≥'}${count} 抽`,302,ey+36,25,'#101921',650);
        let tags = e.outcome === 'up' ? 'UP' : e.outcome === 'off' ? '歪' : '';
        if(e.guaranteed === true) tags += ' · 大保底¹';
        if(!e.interval_exact) tags = '前序不完整';
        label(tags,780,ey+34,22,'#e2c38f',500,242);
        if(e.carried_pulls) label(`跨池 ${e.carried_pulls} 抽`,60,ey+57,18,'#93a4b8',400,218);
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

  return {planPages, makeZip, draw};
})();

if (typeof module !== 'undefined') module.exports = LongImage;
if (typeof document !== 'undefined') {
  let urls = [], generating = false;
  $('image-close').addEventListener('click', () => $('image-dialog').close());
  $('image-dialog').addEventListener('close', () => {for(const url of urls) URL.revokeObjectURL(url); urls=[]; $('image-previews').replaceChildren();});
  $('export-image').addEventListener('click', async () => {
    if (!state.report || generating) return;
    generating = true; $('image-previews').replaceChildren(); $('image-zip').hidden = true;
    const report = state.report, category = state.category;
    $('image-status').textContent = '正在本机生成图片…'; $('image-dialog').showModal();
    try {
      await document.fonts.ready;
      const pages = LongImage.planPages(report, category), files=[];
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
        img.src=url; img.alt=`抽卡记录长图 ${i+1}`; details.append(node('summary','展开图片预览'),img); item.append(link,details); $('image-previews').append(item);
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
    finally {generating=false;}
  });
}
