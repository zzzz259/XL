#!/usr/bin/env node
/**
 * 角色档案长图批量渲染 CLI
 *
 * 用法：
 *   node render_batch.js <payload.json>
 *
 * payload 格式：
 *   {
 *     "characters": { "<id>": { <角色数据对象> }, ... },
 *     "ids": ["<id>", ...],
 *     "out_dir": "...",
 *     "name_template": "{id}_{name}_角色档案_长图.png"
 *   }
 *
 * 输出（stdout）：
 *   {
 *     "results": [
 *       { "id": "<id>", "ok": true, "path": "...", "warnings": [] },
 *       { "id": "<id>", "ok": false, "error": "..." },
 *       ...
 *     ]
 *   }
 *
 * 退出码：全部失败 = 1，否则 = 0
 */

const fs = require('fs');
const path = require('path');
const { registerFont } = require('canvas');
const { renderLongImage } = require('./card_image_renderer');

const RENDERER_DIR = __dirname;
const ASSETS_DIR = path.resolve(RENDERER_DIR, '..', 'assets');
const FONTS_DIR = path.join(ASSETS_DIR, 'fonts');

function safeName(fullName) {
  return String(fullName || '未知').split('/')[0].replace(/[\\/:*?"<>|]/g, '_');
}

function registerFonts() {
  const warnings = [];
  const regular = path.join(FONTS_DIR, 'NotoSansCJKsc-Regular.otf');
  const bold = path.join(FONTS_DIR, 'NotoSansCJKsc-Bold.otf');

  if (!fs.existsSync(regular)) {
    warnings.push(`字体缺失: ${regular}，将依赖系统字体回退`);
  } else {
    registerFont(regular, { family: 'Microsoft YaHei', weight: 'normal' });
    // Georgia 斜体回退：使用 Noto Sans CJK SC Regular 作为 serif 别名，
    // 后续可替换为真正的 Georgia 或 Liberation Serif。
    registerFont(regular, { family: 'Georgia', style: 'italic' });
  }
  if (!fs.existsSync(bold)) {
    warnings.push(`字体缺失: ${bold}，粗体将使用常规字重`);
  } else {
    registerFont(bold, { family: 'Microsoft YaHei', weight: 'bold' });
    registerFont(bold, { family: 'Microsoft YaHei', weight: '900' });
  }
  return warnings;
}

function renderOne(id, character, outDir, nameTemplate) {
  const warnings = [];
  const name = safeName(character.name);
  const fileName = nameTemplate
    .replace(/\{id\}/g, id)
    .replace(/\{name\}/g, name);
  const outputPath = path.resolve(outDir, fileName);

  const canvas = renderLongImage(character);
  const height = canvas.height;
  const buffer = canvas.toBuffer('image/png');

  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  const tempPath = outputPath + '.part';
  fs.writeFileSync(tempPath, buffer);
  fs.renameSync(tempPath, outputPath);

  return { ok: true, path: outputPath, height, warnings };
}

function main() {
  const payloadPath = process.argv[2];
  if (!payloadPath) {
    console.error('用法：node render_batch.js <payload.json>');
    process.exit(1);
  }

  let payload;
  try {
    payload = JSON.parse(fs.readFileSync(payloadPath, 'utf-8'));
  } catch (err) {
    console.error(`读取 payload 失败: ${err.message}`);
    process.exit(1);
  }

  const characters = payload.characters || {};
  const ids = payload.ids || [];
  const outDir = payload.out_dir || '.';
  const nameTemplate = payload.name_template || '{id}_{name}_角色档案_长图.png';

  const fontWarnings = registerFonts();

  fs.mkdirSync(outDir, { recursive: true });

  const results = [];
  for (const id of ids) {
    const character = characters[id];
    if (!character) {
      results.push({ id, ok: false, error: `characters 中未找到 id ${id}` });
      continue;
    }
    try {
      const rendered = renderOne(id, character, outDir, nameTemplate);
      rendered.warnings = rendered.warnings.concat(fontWarnings);
      results.push({ id, ok: true, path: rendered.path, height: rendered.height, warnings: rendered.warnings });
    } catch (err) {
      results.push({ id, ok: false, error: String(err.stack || err.message || err) });
    }
  }

  const anyOk = results.some(r => r.ok);
  console.log(JSON.stringify({ results }, null, 2));
  process.exit(anyOk ? 0 : 1);
}

main();
