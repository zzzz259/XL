"use strict";

const { createCanvas } = require("canvas");
const { getClassicPoolMarks } = require("./classic_pool_markers");
const fs = require("node:fs");

const WIDTH = 920;
const HEIGHT_BASE = 940;
const ROW_TOP = 750;
const ROW_HEIGHT = 48;
const ROW_GAP = 12;
const FONT_FAMILY = "sans-serif";
const COLORS = Object.freeze({
  background: "#eef0f2",
  topbar: "#1f6b8b",
  ink: "#21364d",
  muted: "#708294",
  accent: "#2d5b79",
  accentText: "#26729a",
  gold: "#b37b34",
  border: "#bfd0dc",
  forecast: "#d7e5ef",
  tableHeader: "#d8e2e9",
  white: "#fbfbfc",
  rowMuted: "#788ba0",
  halfAnniversaryBg: "#fff0f0",
  halfAnniversaryText: "#b24d4d",
  halfAnniversaryBorder: "#e5b6b6",
  anniversaryBg: "#f2efff",
  anniversaryText: "#6e54c8",
  anniversaryBorder: "#cbc2ff",
});

function requireString(value, field) {
  if (typeof value !== "string" || value.trim() === "") {
    throw new TypeError(`${field} 必须是非空字符串`);
  }
}

function validateSchedule(schedule) {
  if (!schedule || typeof schedule !== "object" || Array.isArray(schedule)) {
    throw new TypeError("schedule 必须是对象");
  }
  requireString(schedule.anchorDate, "anchorDate");
  if (!Number.isInteger(schedule.periodDays) || schedule.periodDays <= 0) {
    throw new TypeError("periodDays 必须是正整数");
  }
  for (const key of ["current", "next"]) {
    if (!schedule[key] || typeof schedule[key] !== "object" || Array.isArray(schedule[key])) {
      throw new TypeError(`${key} 必须是对象`);
    }
  }
  for (const key of ["newName", "rerunName", "startsAt", "endsAt"]) {
    requireString(schedule.current[key], `current.${key}`);
  }
  for (const key of ["name", "startsAt", "endsAt"]) {
    requireString(schedule.next[key], `next.${key}`);
  }
  if (!Array.isArray(schedule.firstReruns)) {
    throw new TypeError("firstReruns 必须是数组");
  }
  schedule.firstReruns.forEach((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new TypeError(`firstReruns[${index}] 必须是对象`);
    }
    requireString(item.name, `firstReruns[${index}].name`);
    requireString(item.date, `firstReruns[${index}].date`);
  });
}

function roundedRect(ctx, x, y, width, height, radius, fill, stroke = null) {
  ctx.beginPath();
  ctx.roundRect(x, y, width, height, radius);
  ctx.fillStyle = fill;
  ctx.fill();
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
}

function drawText(ctx, value, x, y, size, color, weight = "normal", align = "left", maxWidth) {
  ctx.font = `${weight} ${size}px ${FONT_FAMILY}`;
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = "alphabetic";
  if (maxWidth) ctx.fillText(value, x, y, maxWidth);
  else ctx.fillText(value, x, y);
}

function drawHeader(ctx, schedule) {
  ctx.fillStyle = COLORS.topbar;
  ctx.fillRect(0, 0, WIDTH, 16);

  drawText(ctx, "★", 52, 74, 24, "#c08a43", "bold");
  drawText(ctx, "星落 / 排期工具", 86, 78, 28, "#5d7488", "bold");
  drawText(ctx, "普通角色复刻表", 53, 181, 62, COLORS.ink, "bold");
  drawText(
    ctx,
    `版本锚点  ${schedule.anchorDate}    •    每期起始相隔 ${schedule.periodDays} 天`,
    54,
    228,
    26,
    COLORS.muted,
  );
}

function drawCurrentCard(ctx, current) {
  roundedRect(ctx, 53, 260, 813, 160, 24, "#f8f8f8", COLORS.border);
  roundedRect(ctx, 74, 276, 154, 46, 16, "#2d6583");
  const badge = current.badge || "当前已确认";
  const badgeSize = fitFontSize(ctx, badge, 136, 26, 15);
  drawText(ctx, badge, 151, 307, badgeSize, COLORS.white, "bold", "center", 136);

  const confirmed = current.mode !== "predicted";
  const leftLabel = confirmed ? `${current.newName} / 新` : `${current.newName} / 预计`;
  const rightLabel = confirmed ? `${current.rerunName} / 复刻` : "非官方排期";
  drawText(ctx, leftLabel, 79, 365, 40, COLORS.accent, "bold", "left", 321);
  drawText(ctx, rightLabel, 478, 365, 38, COLORS.gold, "bold", "left", 352);
  drawText(ctx, `${current.startsAt}  —  ${current.endsAt}`, 79, 410, 24, COLORS.muted, "normal", "left", 541);
}

function drawNextCard(ctx, next) {
  roundedRect(ctx, 53, 451, 813, 117, 23, COLORS.forecast, COLORS.border);
  drawText(ctx, "下一期预测", 80, 495, 28, "#4d7392", "bold");
  drawText(ctx, next.name, 79, 542, 38, COLORS.ink, "bold", "left", 156);
  drawText(ctx, `${next.startsAt}  —  ${next.endsAt}`, 274, 539, 26, "#3a627f", "normal", "left", 376);
}

function fitFontSize(ctx, text, maxWidth, startSize, minSize = 12) {
  for (let size = startSize; size >= minSize; size -= 1) {
    ctx.font = `bold ${size}px ${FONT_FAMILY}`;
    if (ctx.measureText(text).width <= maxWidth) return size;
  }
  return minSize;
}

function drawMarkerTag(ctx, x, y, width, height, label, type, fontSize) {
  const isHalfAnniversary = type === "half_anniversary";
  const fill = isHalfAnniversary ? COLORS.halfAnniversaryBg : COLORS.anniversaryBg;
  const border = isHalfAnniversary ? COLORS.halfAnniversaryBorder : COLORS.anniversaryBorder;
  const textColor = isHalfAnniversary ? COLORS.halfAnniversaryText : COLORS.anniversaryText;
  roundedRect(ctx, x, y, width, height, Math.min(11, height / 2), fill, border);
  ctx.font = `bold ${fontSize}px ${FONT_FAMILY}`;
  const metrics = ctx.measureText(label);
  const baseline = y + height / 2
    + (metrics.actualBoundingBoxAscent - metrics.actualBoundingBoxDescent) / 2;
  drawText(ctx, label, x + width / 2, baseline, fontSize, textColor, "bold", "center", width - 12);
}

function drawRowMarkers(ctx, marks, y) {
  if (!marks.length) return;
  const x = 440;
  const width = 218;
  const gap = marks.length > 1 ? 2 : 0;
  const pillHeight = marks.length > 1 ? 16 : 32;
  const totalHeight = marks.length * pillHeight + (marks.length - 1) * gap;
  const top = y + (ROW_HEIGHT - totalHeight) / 2;

  marks.forEach((label, index) => {
    const type = label.startsWith("#半周年") ? "half_anniversary" : "anniversary";
    const size = fitFontSize(ctx, label, width - 14, marks.length > 1 ? 12 : 18, 10);
    drawMarkerTag(ctx, x, top + index * (pillHeight + gap), width, pillHeight, label, type, size);
  });
}

function drawScheduleTable(ctx, schedule) {
  drawText(ctx, "普通首次复刻顺序", 55, 658, 48, COLORS.ink, "bold");
  roundedRect(ctx, 665, 614, 118, 44, 15, "#d2dde5");
  drawText(ctx, "理论档期", 684, 643, 22, "#53697d", "bold");

  roundedRect(ctx, 53, 679, 813, 55, 14, COLORS.tableHeader);
  drawText(ctx, "顺位", 85, 715, 28, "#5b7084", "bold");
  drawText(ctx, "角色名称", 225, 715, 28, "#5b7084", "bold");
  drawText(ctx, "备注", 440, 715, 28, "#5b7084", "bold");
  drawText(ctx, "理论开始日期", 688, 715, 28, "#5b7084", "bold");

  schedule.firstReruns.forEach((item, index) => {
    const y = ROW_TOP + index * (ROW_HEIGHT + ROW_GAP);
    if (index % 2 === 0) roundedRect(ctx, 53, y, 813, ROW_HEIGHT, 14, COLORS.white);
    const rankColor = index < 3 ? COLORS.accentText : COLORS.rowMuted;
    drawText(ctx, String(index + 1).padStart(2, "0"), 88, y + 33, 27, rankColor, "bold");
    const nameSize = fitFontSize(ctx, item.name, 200, 34, 28);
    drawText(ctx, item.name, 225, y + 34, nameSize, COLORS.ink, "bold", "left", 200);
    drawRowMarkers(
      ctx,
      getClassicPoolMarks(item.date, schedule.periodDays, schedule.classicPoolDates),
      y,
    );
    drawText(ctx, item.date, 708, y + 33, 25, "#496c87", "normal", "left", 145);
  });

  const lastRowBottom = schedule.firstReruns.length
    ? ROW_TOP + (schedule.firstReruns.length - 1) * (ROW_HEIGHT + ROW_GAP) + ROW_HEIGHT
    : ROW_TOP;
  const footerY = lastRowBottom + 22;
  ctx.beginPath();
  ctx.moveTo(53, footerY);
  ctx.lineTo(866, footerY);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#c2d0d9";
  ctx.stroke();

  drawText(ctx, "日期为固定 21 天间隔的理论排期，不代表官方已确认。", 55, footerY + 47, 23, COLORS.muted);
  drawText(ctx, "特殊限定池若占用普通周期，后续时间可能整体顺延。", 55, footerY + 89, 23, COLORS.muted);
  drawText(ctx, "展示无需角色头像；名称、日期、备注及队列均可由数据生成。", 55, footerY + 147, 23, "#8499aa");
}

/**
 * Render a compact first-rerun schedule in the user-approved reference style.
 * Dates are display strings supplied by the caller; this renderer never infers them.
 *
 * @param {object} schedule schedule data matching the design document contract
 * @returns {import('canvas').Canvas} a 920px-wide Canvas ready for PNG encoding
 */
function renderRerunSchedule(schedule) {
  validateSchedule(schedule);

  const height = HEIGHT_BASE + (ROW_HEIGHT + ROW_GAP) * schedule.firstReruns.length;
  const canvas = createCanvas(WIDTH, height);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = COLORS.background;
  ctx.fillRect(0, 0, WIDTH, height);

  drawHeader(ctx, schedule);
  drawCurrentCard(ctx, schedule.current);
  drawNextCard(ctx, schedule.next);
  drawScheduleTable(ctx, schedule);
  return canvas;
}

module.exports = { renderRerunSchedule };

if (require.main === module) {
  const [, , inputPath, outputPath] = process.argv;
  if (!inputPath || !outputPath) {
    process.stderr.write("usage: node rerun_schedule_renderer.js <input.json> <output.png>\n");
    process.exitCode = 2;
  } else {
    try {
      const payload = JSON.parse(fs.readFileSync(inputPath, "utf8"));
      const schedule = payload.render || payload;
      const canvas = renderRerunSchedule(schedule);
      fs.writeFileSync(outputPath, canvas.toBuffer("image/png"));
    } catch (error) {
      process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
      process.exitCode = 1;
    }
  }
}
