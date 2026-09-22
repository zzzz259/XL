/**
 * 角色档案 1080px 长图渲染器
 *
 * 来源：C:\Users\49432\Downloads\character_archive_demo_108_latest.html
 * 抽取日期：2026-09-20
 *
 * 本文件从上述单页应用的 <script> 中逐字抽取了 makeCanvasLongImage(c) 及其全部
 * Canvas 2D 绘图闭包依赖。后续若 HTML 源更新，需人工对照同步此文件。
 *
 * 唯一做的最小包装：
 * - 用 node-canvas 的 createCanvas() 替换 document.createElement('canvas')
 * - 导出 renderLongImage(characterData) -> Canvas
 * - 删除仅页面使用的 DOM/HTML 渲染函数、事件绑定、CHARACTERS 常量等
 */

const { createCanvas } = require('canvas');

const elementPalette = {
  '火属性': ['#c65a4a', '#7b2f29', '#f7e5df', '火'],
  '水属性': ['#4f79b7', '#274b7b', '#e7eef8', '水'],
  '木属性': ['#4f8b66', '#27563b', '#e5f0e7', '木'],
  '光属性': ['#b99134', '#715820', '#f6efd8', '光'],
  '暗属性': ['#6e5a91', '#41325e', '#ece7f4', '暗'],
};
const skillMeta = [
  ['leader_skill', 'LEADER', '队长技'],
  ['normal_skill', 'NORMAL', '普攻'],
  ['special_skill', 'TACTIC', '战技'],
  ['burst_skill', 'BURST', '总攻技'],
];
const passiveKeys = ['passive_skill_1', 'passive_skill_2', 'passive_skill_3'];
const awakeKeys = ['awakening_skill_1', 'awakening_skill_2', 'awakening_skill_3', 'awakening_skill_4', 'awakening_skill_5'];
const BADGE_SLOTS = [['花', 'FLOWER'], ['球', 'ORB'], ['羽', 'FEATHER']];
const CANVAS_NUM_RE = /[+-]?\d+(?:\.\d+)?(?:\/[+-]?\d+(?:\.\d+)?)*(?:%|秒|层|名|次|点|倍)?/g;

function splitName(s = '') {
  let [cn, en = ''] = s.split('/');
  return [cn, en];
}

function palette(c) {
  return elementPalette[c.element] || ['#607080', '#384550', '#e9edf0', '•'];
}

function parseSkill(txt = '') {
  let lines = String(txt || '').split('\n');
  let title = lines.shift() || '', cd = '';
  let m = title.match(/(\d+)秒/);
  if (m) {
    cd = m[1] + '秒';
    title = title.replace(m[0], '').trim();
  }
  let body = lines.join('\n').trim(), awakening = null;
  let am = body.match(/(?:^|\n)\s*觉醒(\d+)\s*\n([\s\S]*)$/);
  if (am) {
    body = body.slice(0, am.index).trim();
    awakening = { index: Number(am[1]), body: (am[2] || '').trim() };
  }
  return { title, cd, body, awakening };
}

function pct(v) {
  let n = Number(v);
  if (!Number.isFinite(n)) return '—';
  let p = n / 100;
  return `${Number.isInteger(p) ? p : p.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')}%`;
}

function skillAwakeningLinks(c) {
  const links = {};
  skillMeta.forEach(([k, t, l]) => {
    let sk = parseSkill(c[k]);
    if (sk.awakening) {
      let n = sk.awakening.index;
      (links[n] || (links[n] = [])).push(l);
    }
  });
  return links;
}

function parseBadge(s = '') {
  let parts = String(s).split(/\n\n+/);
  let get = (idx) => parts[idx] ? parts[idx].split('\n').slice(1).join(' ').trim() : '';
  let mainRaw = get(1), subRaw = get(2);
  let mainGroups = mainRaw.split(/\/\/|\|/).map(g => g.trim().split(/\s+/).filter(Boolean));
  while (mainGroups.length < 3) mainGroups.push([]);
  mainGroups = mainGroups.slice(0, 3);
  let subGroups = (subRaw.includes('//') || subRaw.includes('|')) ? subRaw.split(/\/\/|\|/).map(g => g.trim().split(/\s+/).filter(Boolean)) : null;
  if (subGroups) {
    while (subGroups.length < 3) subGroups.push([]);
    subGroups = subGroups.slice(0, 3);
  }
  return { badge: get(0).split('/').filter(Boolean), mainGroups, main: mainGroups.flat(), sub: subRaw.split(/\s+/).filter(Boolean), subGroups };
}

function parseMats(s = '') {
  return String(s || '').split('|').map(x => x.trim()).filter(Boolean).map(raw => {
    let m = raw.match(/^(.*?)\s*\*\s*([0-9.]+)\s*$/);
    return m ? { name: m[1].trim(), qty: m[2] } : { name: raw, qty: '' };
  });
}

function starText(n) {
  return '★'.repeat(Number(n) || 0);
}

function wrapCanvas(ctx, text, maxWidth) {
  const out = [];
  String(text || '').split('\n').forEach(par => {
    if (!par) {
      out.push('');
      return;
    }
    let line = '';
    for (const ch of par) {
      const t = line + ch;
      if (ctx.measureText(t).width > maxWidth && line) {
        out.push(line);
        line = ch;
      } else {
        line = t;
      }
    }
    if (line) out.push(line);
  });
  return out;
}

function rr(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
  ctx.fill();
}

function drawCanvasRichLine(ctx, text, x, y, normal = '#4b5156', number = '#c93636') {
  let cursor = x, last = 0, re = new RegExp(CANVAS_NUM_RE.source, 'g'), s = String(text || '');
  for (const m of s.matchAll(re)) {
    let a = s.slice(last, m.index);
    ctx.fillStyle = normal;
    ctx.fillText(a, cursor, y);
    cursor += ctx.measureText(a).width;
    ctx.fillStyle = number;
    ctx.fillText(m[0], cursor, y);
    cursor += ctx.measureText(m[0]).width;
    last = m.index + m[0].length;
  }
  let tail = s.slice(last);
  ctx.fillStyle = normal;
  ctx.fillText(tail, cursor, y);
}

function materialPillRows(ctx, s, maxWidth, font = '21px Microsoft YaHei') {
  ctx.font = font;
  let rows = 1, w = 0;
  for (const m of parseMats(s)) {
    let pw = ctx.measureText(m.name).width + (m.qty ? ctx.measureText('×' + m.qty).width : 0) + 58;
    if (w && w + pw + 10 > maxWidth) {
      rows++;
      w = pw;
    } else {
      w += pw + (w ? 10 : 0);
    }
  }
  return parseMats(s).length ? rows : 0;
}

function drawMaterialPills(ctx, s, x, y, maxWidth, accent = '#7b4e31', soft = '#f0e2d5') {
  ctx.font = '21px Microsoft YaHei';
  let cx = x, cy = y;
  for (const m of parseMats(s)) {
    let qty = m.qty ? '×' + m.qty : '';
    ctx.font = '21px Microsoft YaHei';
    let nameW = ctx.measureText(m.name).width;
    ctx.font = '900 20px Microsoft YaHei';
    let qtyW = qty ? ctx.measureText(qty).width : 0;
    let pw = nameW + qtyW + 58;
    if (cx > x && cx + pw > x + maxWidth) {
      cx = x;
      cy += 48;
    }
    ctx.fillStyle = '#f5f2eb';
    rr(ctx, cx, cy, pw, 40, 11);
    ctx.fillStyle = soft;
    rr(ctx, cx + 6, cy + 6, 28, 28, 8);
    ctx.fillStyle = accent;
    ctx.font = '900 14px Microsoft YaHei';
    ctx.fillText((m.name || '材').slice(0, 1), cx + 13, cy + 26);
    ctx.font = '21px Microsoft YaHei';
    ctx.fillStyle = '#3f464b';
    ctx.fillText(m.name, cx + 42, cy + 27);
    if (qty) {
      ctx.font = '900 20px Microsoft YaHei';
      ctx.fillStyle = accent;
      ctx.fillText(qty, cx + 42 + nameW + 8, cy + 27);
    }
    cx += pw + 10;
  }
  return parseMats(s).length ? cy - y + 40 : 0;
}

function measureUpgradeBox(ctx, arr, width) {
  let h = 58;
  for (const x of (arr || [])) {
    h += 33 + materialPillRows(ctx, x, width - 38) * 48 + 12;
  }
  return h + 20;
}

function makeCanvasLongImage(c) {
  const W = 1080, M = 52, CW = W - M * 2, p = palette(c), [cn, en] = splitName(c.name), bad = parseBadge(c.badge_info), links = skillAwakeningLinks(c);
  const temp = createCanvas();
  temp.width = W;
  temp.height = 18000;
  const ctx = temp.getContext('2d');
  ctx.fillStyle = '#f6f2ea';
  ctx.fillRect(0, 0, temp.width, temp.height);
  let y = 52;
  const sectionTitle = (n, t) => {
    ctx.fillStyle = p[0];
    ctx.font = 'italic 18px Georgia';
    ctx.fillText(n, M + 4, y + 24);
    ctx.fillStyle = '#171c20';
    ctx.font = '900 31px Microsoft YaHei';
    ctx.fillText(t, M + 105, y + 27);
    y += 62;
  };
  const pill = (text, x, py, bg, fg, font = '700 17px Microsoft YaHei', pad = 13, h = 34) => {
    ctx.font = font;
    let w = ctx.measureText(text).width + pad * 2;
    ctx.fillStyle = bg;
    rr(ctx, x, py, w, h, h / 2);
    ctx.fillStyle = fg;
    ctx.fillText(text, x + pad, py + h - 10);
    return w;
  };
  const tagPills = (items, x, py, maxW, opts = {}) => {
    let cx = x, cy = py, rowH = opts.rowH || 40;
    items.forEach(it => {
      let text = typeof it === 'string' ? it : it.text,
        bg = typeof it === 'string' ? '#f3efe7' : it.bg || '#f3efe7',
        fg = typeof it === 'string' ? '#4e555a' : it.fg || '#4e555a';
      ctx.font = opts.font || '700 18px Microsoft YaHei';
      let w = ctx.measureText(text).width + (opts.pad || 14) * 2;
      if (cx > x && cx + w > x + maxW) {
        cx = x;
        cy += rowH;
      }
      pill(text, cx, cy, bg, fg, opts.font || '700 18px Microsoft YaHei', opts.pad || 14, opts.h || 34);
      cx += w + 8;
    });
    return cy - py + (items.length ? (opts.h || 34) : 0);
  };
  const drawRichParagraph = (text, x, py, maxW, font = '24px Microsoft YaHei', lineH = 39, color = '#4b5156') => {
    ctx.font = font;
    let lines = wrapCanvas(ctx, text, maxW), yy = py;
    lines.forEach(line => {
      drawCanvasRichLine(ctx, line, x, yy, color, '#c93636');
      yy += lineH;
    });
    return lines.length * lineH;
  };

  // Header with complete identity information.
  ctx.font = '23px Microsoft YaHei';
  let descLines = wrapCanvas(ctx, c.description, CW - 88).slice(0, 3);
  const headerH = 356 + Math.max(0, descLines.length - 1) * 30;
  ctx.fillStyle = '#fffdf8';
  rr(ctx, M, y, CW, headerH, 30);
  ctx.fillStyle = p[0];
  rr(ctx, M, y, 10, headerH, 5);
  ctx.fillStyle = '#171c20';
  ctx.font = '900 62px Microsoft YaHei';
  ctx.fillText(cn, M + 42, y + 78);
  ctx.fillStyle = '#767d83';
  ctx.font = 'italic 24px Georgia';
  ctx.fillText(en || '', M + 44, y + 113);
  let tx = M + 42, tagY = y + 138;
  [[c.element, p[2], p[1]], [c.profession, '#eeece7', '#555c62'], [`${starText(c.star)} ${c.star}★`, '#252a2f', '#fff']].forEach(([t, bg, fg]) => {
    let w = pill(t, tx, tagY, bg, fg);
    tx += w + 9;
  });
  const ident = [['阵营', c.faction], ['生日', c.birthday], ['身高', c.height], ['CV', c.cv]], gap = 10, iw = (CW - 84 - gap * 3) / 4, iy = y + 192;
  ident.forEach((it, i) => {
    let x = M + 42 + i * (iw + gap);
    ctx.fillStyle = i === 0 ? p[2] : '#f5f1e9';
    rr(ctx, x, iy, iw, 68, 13);
    ctx.fillStyle = '#858b90';
    ctx.font = '13px Microsoft YaHei';
    ctx.fillText(it[0], x + 14, iy + 21);
    ctx.fillStyle = i === 0 ? p[1] : '#252b30';
    ctx.font = '900 17px Microsoft YaHei';
    let val = String(it[1] || '—');
    if (ctx.measureText(val).width > iw - 28) {
      ctx.font = '900 14px Microsoft YaHei';
    }
    ctx.fillText(val, x + 14, iy + 49);
  });
  ctx.fillStyle = '#4d5358';
  ctx.font = '23px Microsoft YaHei';
  descLines.forEach((l, i) => ctx.fillText(l, M + 44, y + 302 + i * 30));
  y += headerH + 24;

  // Primary stats + 8 well-spaced secondary stat tiles.
  const statH = 338;
  ctx.fillStyle = '#fffdf8';
  rr(ctx, M, y, CW, statH, 24);
  const vals = [['最大攻击力', Number(c.max_atk).toLocaleString(), `初始 ${c.init_atk}`], ['最大防御力', Number(c.max_def).toLocaleString(), `初始 ${c.init_def}`], ['最大生命值', Number(c.max_hp).toLocaleString(), `初始 ${c.init_hp}`]];
  vals.forEach((v, i) => {
    let x = M + 28 + i * (CW - 56) / 3;
    ctx.fillStyle = '#7a8085';
    ctx.font = '15px Microsoft YaHei';
    ctx.fillText(v[0], x, y + 38);
    ctx.fillStyle = '#171c20';
    ctx.font = '900 37px Microsoft YaHei';
    ctx.fillText(v[1], x, y + 82);
    ctx.fillStyle = '#8a9095';
    ctx.font = '15px Microsoft YaHei';
    ctx.fillText(v[2], x, y + 109);
  });
  const minis = [['暴击率', pct(c.crt), true], ['格挡率', pct(c.blk), true], ['暴击伤害', pct(c.crt_int), true], ['格挡强度', pct(c.blk_int), true], ['移动速度', c.spd_move, false], ['攻击间隔', c.spd_atk, false], ['攻击距离', c.range_atk, false], ['重量', c.weight, false]], mg = 9, mw = (CW - 56 - mg * 3) / 4, mh = 78, my = y + 137;
  minis.forEach((v, i) => {
    let row = Math.floor(i / 4), col = i % 4, x = M + 28 + col * (mw + mg), yy = my + row * (mh + 9);
    ctx.fillStyle = v[2] ? p[2] : '#f5f1e9';
    rr(ctx, x, yy, mw, mh, 13);
    ctx.fillStyle = '#858b90';
    ctx.font = '13px Microsoft YaHei';
    ctx.fillText(v[0], x + 14, yy + 23);
    ctx.fillStyle = v[2] ? p[1] : '#252b30';
    ctx.font = '900 24px Microsoft YaHei';
    ctx.fillText(String(v[1]), x + 14, yy + 56);
  });
  y += statH + 24;

  // Skills. Base skill is separated from its awakening add-on.
  sectionTitle('02 / SKILLS', '技能');
  const drawSkill = (key, type, label) => {
    let sk = parseSkill(c[key]);
    ctx.font = '24px Microsoft YaHei';
    let bodyLines = wrapCanvas(ctx, sk.body, CW - 58);
    let addonLines = [];
    if (sk.awakening) {
      ctx.font = '21px Microsoft YaHei';
      addonLines = wrapCanvas(ctx, sk.awakening.body, CW - 92);
    }
    let h = 105 + bodyLines.length * 39 + 24 + (sk.awakening ? (62 + addonLines.length * 34 + 22) : 0);
    ctx.fillStyle = '#fffdf8';
    rr(ctx, M, y, CW, h, 20);
    if (key === 'burst_skill') {
      ctx.fillStyle = p[0];
      rr(ctx, M, y, 7, h, 4);
    }
    ctx.fillStyle = p[1];
    ctx.font = '900 15px Microsoft YaHei';
    ctx.fillText(type, M + 28, y + 31);
    ctx.fillStyle = '#171c20';
    ctx.font = '900 26px Microsoft YaHei';
    ctx.fillText(sk.title || label, M + 28, y + 69);
    if (sk.cd) {
      let txt = `总攻 CD  ${sk.cd}`;
      ctx.font = '900 17px Microsoft YaHei';
      let w = ctx.measureText(txt).width + 30;
      ctx.fillStyle = key === 'burst_skill' ? p[1] : '#252a2f';
      rr(ctx, M + CW - w - 24, y + 22, w, 38, 19);
      ctx.fillStyle = '#fff';
      ctx.fillText(txt, M + CW - w - 9, y + 48);
    }
    ctx.font = '24px Microsoft YaHei';
    let yy = y + 108;
    bodyLines.forEach(line => {
      drawCanvasRichLine(ctx, line, M + 28, yy);
      yy += 39;
    });
    if (sk.awakening) {
      yy += 8;
      let ah = 52 + addonLines.length * 34 + 12;
      ctx.fillStyle = p[2];
      rr(ctx, M + 26, yy, CW - 52, ah, 14);
      ctx.fillStyle = p[1];
      rr(ctx, M + 26, yy, 5, ah, 3);
      ctx.fillStyle = p[1];
      ctx.font = '900 14px Microsoft YaHei';
      ctx.fillText(`AWAKE ${sk.awakening.index} · 觉醒追加`, M + 46, yy + 25);
      ctx.fillStyle = '#747b80';
      ctx.font = '13px Microsoft YaHei';
      ctx.textAlign = 'right';
      ctx.fillText(`关联觉醒 ${sk.awakening.index}`, M + CW - 46, yy + 25);
      ctx.textAlign = 'start';
      ctx.font = '21px Microsoft YaHei';
      let ay = yy + 55;
      addonLines.forEach(line => {
        drawCanvasRichLine(ctx, line, M + 46, ay, '#444b50', '#c93636');
        ay += 34;
      });
    }
    y += h + 14;
  };
  skillMeta.forEach(([k, t, l]) => drawSkill(k, t, l));
  y += 14;

  // Passive and awakening groups are visually distinct.
  sectionTitle('02 / TRAITS', '被动与觉醒');
  const groupHead = (code, title, awake = false) => {
    ctx.fillStyle = awake ? p[2] : '#252a2f';
    rr(ctx, M, y, awake ? 150 : 116, 31, 9);
    ctx.fillStyle = awake ? p[1] : '#fff';
    ctx.font = '900 13px Microsoft YaHei';
    ctx.fillText(code, M + 13, y + 21);
    ctx.fillStyle = '#20262a';
    ctx.font = '900 23px Microsoft YaHei';
    ctx.fillText(title, M + (awake ? 168 : 134), y + 23);
    y += 47;
  };
  const drawTrait = (key, kind, idx, awake = false) => {
    let sk = parseSkill(c[key]);
    ctx.font = '21px Microsoft YaHei';
    let lines = wrapCanvas(ctx, sk.body, CW - 82);
    let linked = awake ? (links[idx] || []) : [];
    let h = 82 + lines.length * 34 + (linked.length ? 36 : 0) + 22;
    ctx.fillStyle = awake ? '#fffdf8' : '#faf7f1';
    rr(ctx, M, y, CW, h, 17);
    if (awake) {
      ctx.fillStyle = p[0];
      rr(ctx, M, y, 5, h, 3);
    }
    ctx.fillStyle = awake ? p[1] : '#fff';
    if (!awake) {
      ctx.fillStyle = '#252a2f';
    }
    rr(ctx, M + 24, y + 20, awake ? 112 : 106, 28, 8);
    ctx.fillStyle = '#fff';
    ctx.font = '900 12px Microsoft YaHei';
    ctx.fillText(`${awake ? 'AWAKE' : 'PASSIVE'} ${idx}`, M + 36, y + 39);
    ctx.fillStyle = '#171c20';
    ctx.font = '900 22px Microsoft YaHei';
    ctx.fillText(sk.title, M + 154, y + 40);
    ctx.font = '21px Microsoft YaHei';
    let yy = y + 79;
    lines.forEach(line => {
      drawCanvasRichLine(ctx, line, M + 28, yy);
      yy += 34;
    });
    if (linked.length) {
      ctx.font = '900 13px Microsoft YaHei';
      let txt = '↗ 关联 ' + linked.join(' / '), w = ctx.measureText(txt).width + 24;
      ctx.fillStyle = p[2];
      rr(ctx, M + 28, yy + 3, w, 27, 8);
      ctx.fillStyle = p[1];
      ctx.fillText(txt, M + 40, yy + 22);
    }
    y += h + 10;
  };
  groupHead('PASSIVE', '被动技能');
  passiveKeys.forEach((k, i) => drawTrait(k, 'PASSIVE', i + 1, false));
  y += 10;
  groupHead('AWAKENING', '觉醒能力', true);
  awakeKeys.forEach((k, i) => drawTrait(k, 'AWAKE', i + 1, true));
  y += 18;

  // Badge build: tokenized recommendation blocks.
  sectionTitle('03 / BUILD', '徽章与属性推荐');
  const badgeStart = y;
  ctx.fillStyle = '#fffdf8';
  rr(ctx, M, y, CW, 500, 20);
  let by = y + 28;
  ctx.fillStyle = '#7a8085';
  ctx.font = '900 14px Microsoft YaHei';
  ctx.fillText('推荐徽章套装', M + 28, by + 13);
  by += 28;
  let used = tagPills(bad.badge.map(x => ({ text: x, bg: p[2], fg: p[1] })), M + 28, by, CW - 56, { font: '900 18px Microsoft YaHei', h: 36, rowH: 43 });
  by += used + 25;
  const sg = 10, sw = (CW - 76 - sg * 2) / 3, sy = by;
  let slotHeights = [];
  BADGE_SLOTS.forEach(([slot, en], i) => {
    let main = bad.mainGroups[i] || [], sub = bad.subGroups ? (bad.subGroups[i] || []) : [];
    ctx.font = '700 16px Microsoft YaHei';
    let mr = Math.max(1, Math.ceil(main.length / 2)), sr = bad.subGroups ? Math.max(1, Math.ceil(sub.length / 2)) : 0;
    slotHeights.push(102 + mr * 40 + (bad.subGroups ? (34 + sr * 40) : 0));
  });
  let sh = Math.max(...slotHeights, 150);
  BADGE_SLOTS.forEach(([slot, en], i) => {
    let x = M + 28 + i * (sw + sg);
    ctx.fillStyle = '#faf7f1';
    rr(ctx, x, sy, sw, sh, 15);
    ctx.fillStyle = p[2];
    rr(ctx, x + 12, sy + 12, 34, 34, 10);
    ctx.fillStyle = p[1];
    ctx.font = '900 18px Microsoft YaHei';
    ctx.fillText(slot, x + 21, sy + 35);
    ctx.fillStyle = '#747b80';
    ctx.font = 'italic 11px Georgia';
    ctx.fillText(en, x + 56, sy + 34);
    ctx.fillStyle = '#858b90';
    ctx.font = '12px Microsoft YaHei';
    ctx.fillText('推荐主属性', x + 14, sy + 68);
    let yy = sy + 79;
    yy += tagPills((bad.mainGroups[i] || []).map(v => ({ text: v, bg: '#fff', fg: '#3f464b' })), x + 14, yy, sw - 28, { font: '800 15px Microsoft YaHei', h: 31, rowH: 37, pad: 10 });
    if (bad.subGroups) {
      yy += 15;
      ctx.fillStyle = '#858b90';
      ctx.font = '12px Microsoft YaHei';
      ctx.fillText('推荐副属性', x + 14, yy + 11);
      yy += 19;
      tagPills((bad.subGroups[i] || []).map(v => ({ text: v, bg: '#fff', fg: '#3f464b' })), x + 14, yy, sw - 28, { font: '800 15px Microsoft YaHei', h: 31, rowH: 37, pad: 10 });
    }
  });
  by = sy + sh + 20;
  if (!bad.subGroups) {
    ctx.fillStyle = '#f5f1e9';
    let subH = 78 + Math.max(0, Math.ceil(bad.sub.length / 5) - 1) * 40;
    rr(ctx, M + 28, by, CW - 56, subH, 14);
    ctx.fillStyle = '#777e84';
    ctx.font = '900 13px Microsoft YaHei';
    ctx.fillText('共通推荐副属性', M + 44, by + 25);
    tagPills(bad.sub.map(v => ({ text: v, bg: '#fff', fg: '#3f464b' })), M + 44, by + 36, CW - 88, { font: '800 16px Microsoft YaHei', h: 32, rowH: 39, pad: 11 });
    by += subH + 20;
  }
  const badgeH = by - badgeStart + 2; // redraw base height by covering unused placeholder if needed
  if (badgeH < 500) {
    ctx.fillStyle = '#f6f2ea';
    ctx.fillRect(M, badgeStart + badgeH, CW, 500 - badgeH);
  }
  y = badgeStart + badgeH + 22;

  // Growth materials, with larger type and pills for phone reading.
  sectionTitle('04 / GROWTH', '养成材料');
  const growthTop = y;
  ctx.fillStyle = '#fffdf8';
  rr(ctx, M, y, CW, 3000, 20);
  let gy = y + 28;
  ctx.fillStyle = '#777e84';
  ctx.font = '900 16px Microsoft YaHei';
  ctx.fillText('突破材料', M + 28, gy + 15);
  gy += 38;
  (c.breakthrough_costs || []).forEach((x, i) => {
    ctx.font = '21px Microsoft YaHei';
    let rows = materialPillRows(ctx, x, CW - 88), ch = 52 + rows * 48;
    ctx.fillStyle = '#faf7f1';
    rr(ctx, M + 22, gy, CW - 44, ch, 14);
    ctx.fillStyle = p[1];
    ctx.font = '900 17px Microsoft YaHei';
    ctx.fillText(`突破阶段 ${i + 1}`, M + 40, gy + 31);
    drawMaterialPills(ctx, x, M + 40, gy + 47, CW - 80, p[1], p[2]);
    gy += ch + 12;
  });
  gy += 10;
  const colGap = 14, colW = (CW - 78) / 2, boxY = gy;
  const drawUpgradeBox = (title, arr, x) => {
    let bh = measureUpgradeBox(ctx, arr, colW);
    ctx.fillStyle = '#faf7f1';
    rr(ctx, x, boxY, colW, bh, 14);
    ctx.fillStyle = '#171c20';
    ctx.font = '900 19px Microsoft YaHei';
    ctx.fillText(title, x + 18, boxY + 33);
    let uy = boxY + 55;
    (arr || []).forEach((row, i) => {
      ctx.fillStyle = p[1];
      ctx.font = '900 15px Microsoft YaHei';
      ctx.fillText(`Lv.${i + 1} → ${i + 2}`, x + 18, uy + 20);
      uy += 31;
      uy += drawMaterialPills(ctx, row, x + 18, uy, colW - 36, p[1], p[2]) + 13;
    });
    return bh;
  };
  let bh1 = drawUpgradeBox('普攻 / 技能升级', c.normal_skill_upgrade_costs, M + 22),
    bh2 = drawUpgradeBox('被动升级', c.passive_skill_upgrade_costs, M + 22 + colW + colGap);
  gy = boxY + Math.max(bh1, bh2) + 24;
  const growthH = gy - growthTop;
  ctx.fillStyle = '#f6f2ea';
  ctx.fillRect(M, growthTop + growthH, CW, 3000 - growthH);
  y = growthTop + growthH + 34;
  ctx.fillStyle = '#858b90';
  ctx.font = '16px Microsoft YaHei';
  ctx.textAlign = 'center';
  ctx.fillText('CHARACTER ARCHIVE · 手机长图版 · 已排除角色语音与角色故事', W / 2, y + 18);
  ctx.textAlign = 'start';
  y += 52;

  // Crop the oversized working canvas to actual content height.
  const out = createCanvas();
  out.width = W;
  out.height = Math.ceil(y);
  out.getContext('2d').drawImage(temp, 0, 0, W, out.height, 0, 0, W, out.height);
  return out;
}

function renderLongImage(characterData) {
  return makeCanvasLongImage(characterData);
}

module.exports = { renderLongImage };
