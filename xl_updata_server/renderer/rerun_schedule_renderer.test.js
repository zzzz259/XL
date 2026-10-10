const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');
const { createCanvas } = require('canvas');

const { classicPoolDatesForYears, getClassicPoolMarks } = require('./classic_pool_markers');
const { renderRerunSchedule } = require('./rerun_schedule_renderer');

test('classic pool markers include the reference half-anniversary date', () => {
  const marks = getClassicPoolMarks('2027.01.26', 21);
  assert.deepEqual(marks, ['#半周年典藏（预估）']);
});

test('classic pool markers include an anniversary inside the rerun window', () => {
  const marks = getClassicPoolMarks('2026.07.07', 21);
  assert.deepEqual(marks, ['#周年典藏（预估）']);
});

test('classic pool marker window starts at 10:00 and ends exclusively at 05:00', () => {
  const dates = [
    { type: 'half_anniversary', date: '2026-07-07' },
    { type: 'anniversary', date: '2026-07-28' },
    { type: 'anniversary', date: '2026-07-29' },
  ];
  assert.deepEqual(getClassicPoolMarks('2026.07.07', 21, dates), [
    '#周年典藏（预估）',
  ]);
});

test('classic pool markers preserve both types when one window hits both', () => {
  const dates = [
    { type: 'anniversary', date: '2026-07-21' },
    { type: 'half_anniversary', date: '2026-07-10' },
  ];
  assert.deepEqual(getClassicPoolMarks('2026.07.07', 21, dates), [
    '#半周年典藏（预估）',
    '#周年典藏（预估）',
  ]);
});

test('classic pool date rules can be generated for selected years', () => {
  assert.deepEqual(classicPoolDatesForYears([2027]), [
    { type: 'half_anniversary', date: '2027-02-05' },
    { type: 'anniversary', date: '2027-07-21' },
  ]);
});

test('classic pool marker rules reject unknown types and malformed dates', () => {
  assert.throws(
    () => getClassicPoolMarks('2026.07.07', 21, [{ type: 'unknown', date: '2026-07-21' }]),
    TypeError,
  );
  assert.throws(
    () => getClassicPoolMarks('2026.07.07', 21, [{ type: 'anniversary', date: '2026-02-30' }]),
    TypeError,
  );
});

function referenceSchedule() {
  return {
    anchorDate: '2026.09.22',
    periodDays: 21,
    current: {
      newName: '罗蕾娜',
      rerunName: '雪莉',
      startsAt: '2026.09.22 10:00',
      endsAt: '2026.10.13 05:00',
    },
    next: {
      name: '朝雾',
      startsAt: '10.13 10:00',
      endsAt: '11.03 05:00',
    },
    firstReruns: [
      ['朝雾', '2026.10.13'],
      ['锥', '2026.11.03'],
      ['司礼者', '2026.11.24'],
      ['云罗', '2026.12.15'],
      ['浮光', '2027.01.05'],
      ['艾格尼丝', '2027.01.26'],
      ['認', '2027.02.16'],
      ['莎尔拉', '2027.03.09'],
      ['阿芙佳朵', '2027.03.30'],
      ['迷迭香', '2027.04.20'],
      ['罗蕾娜', '2027.05.11'],
    ].map(([name, date]) => ({ name, date })),
  };
}

test('renders the reference schedule at 920 by 1600 pixels', () => {
  const canvas = renderRerunSchedule(referenceSchedule());
  assert.equal(canvas.width, 920);
  assert.equal(canvas.height, 1600);
  assert.deepEqual(
    [...canvas.toBuffer('image/png').subarray(0, 8)],
    [137, 80, 78, 71, 13, 10, 26, 10],
  );
});

test('renderer CLI reads projected JSON and writes a PNG artifact', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'rerun-render-'));
  try {
    const input = path.join(directory, 'schedule.json');
    const output = path.join(directory, 'current.png');
    fs.writeFileSync(input, JSON.stringify({ render: referenceSchedule() }));
    const result = spawnSync(process.execPath, [
      path.join(__dirname, 'rerun_schedule_renderer.js'), input, output,
    ], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual([...fs.readFileSync(output).subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('adds one 60-pixel row when the forecast queue grows', () => {
  const schedule = referenceSchedule();
  schedule.firstReruns.push({ name: '新增候选', date: '2027.05.25' });
  assert.equal(renderRerunSchedule(schedule).height, 1660);
});

test('draws a classic-pool marker pill in the shifted remark column', () => {
  const schedule = referenceSchedule();
  schedule.firstReruns[0].date = '2026.07.07';
  schedule.classicPoolDates = [{ type: 'anniversary', date: '2026-07-21' }];
  const canvas = renderRerunSchedule(schedule);
  const pixel = canvas.getContext('2d').getImageData(460, 770, 1, 1).data;
  assert.deepEqual([...pixel].slice(0, 3), [242, 239, 255]);
});

test('draws a red estimate pill for a half-anniversary period', () => {
  const schedule = referenceSchedule();
  schedule.firstReruns[0].date = '2027.01.26';
  const canvas = renderRerunSchedule(schedule);
  const pixel = canvas.getContext('2d').getImageData(460, 770, 1, 1).data;
  assert.deepEqual([...pixel].slice(0, 3), [255, 240, 240]);
});

test('keeps both estimate pills visible when one period hits both markers', () => {
  const schedule = referenceSchedule();
  schedule.firstReruns[0].date = '2026.07.07';
  schedule.classicPoolDates = [
    { type: 'half_anniversary', date: '2026-07-10' },
    { type: 'anniversary', date: '2026-07-21' },
  ];
  const context = renderRerunSchedule(schedule).getContext('2d');
  assert.deepEqual([...context.getImageData(460, 760, 1, 1).data].slice(0, 3), [255, 240, 240]);
  assert.deepEqual([...context.getImageData(460, 780, 1, 1).data].slice(0, 3), [242, 239, 255]);
});

test('leaves the remark column clear when no classic pool event matches', () => {
  const schedule = referenceSchedule();
  schedule.firstReruns[0].date = '2026.07.07';
  schedule.classicPoolDates = [];
  const canvas = renderRerunSchedule(schedule);
  const pixel = canvas.getContext('2d').getImageData(460, 770, 1, 1).data;
  assert.deepEqual([...pixel].slice(0, 3), [251, 251, 252]);
});

test('rejects a row without its display date', () => {
  const schedule = referenceSchedule();
  schedule.firstReruns[0] = { name: '朝雾' };
  assert.throws(() => renderRerunSchedule(schedule), /firstReruns\[0\]\.date/);
});

test('renders dense Chinese forecast glyphs with native bold instead of synthetic stroke', () => {
  const actual = renderRerunSchedule(referenceSchedule());
  const expected = createCanvas(920, 1600);
  const expectedContext = expected.getContext('2d');
  expectedContext.fillStyle = '#d7e5ef';
  expectedContext.fillRect(53, 451, 813, 117);
  expectedContext.font = 'bold 38px sans-serif';
  expectedContext.fillStyle = '#21364d';
  expectedContext.textAlign = 'left';
  expectedContext.textBaseline = 'alphabetic';
  expectedContext.fillText('朝雾', 79, 542, 156);

  const actualPixels = actual.getContext('2d').getImageData(80, 505, 180, 55).data;
  const expectedPixels = expectedContext.getImageData(80, 505, 180, 55).data;
  let differingPixels = 0;
  for (let offset = 0; offset < actualPixels.length; offset += 4) {
    if (
      actualPixels[offset] !== expectedPixels[offset]
      || actualPixels[offset + 1] !== expectedPixels[offset + 1]
      || actualPixels[offset + 2] !== expectedPixels[offset + 2]
    ) {
      differingPixels += 1;
    }
  }
  assert.equal(differingPixels, 0, `forecast glyph region has ${differingPixels} pixels differing from native bold`);
});
