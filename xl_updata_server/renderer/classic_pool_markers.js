'use strict';

const HALF_ANNIVERSARY_DATES = Object.freeze({
  2026: '2026-02-16',
  2027: '2027-02-05',
  2028: '2028-01-25',
  2029: '2029-02-12',
  2030: '2030-02-02',
});

const MARKER_LABELS = Object.freeze({
  half_anniversary: '#半周年典藏（预估）',
  anniversary: '#周年典藏（预估）',
});

function dateParts(value, pattern, separator) {
  if (typeof value !== 'string' || !pattern.test(value)) {
    throw new TypeError(`日期格式无效: ${value}`);
  }

  const [year, month, day] = value.split(separator).map(Number);
  const normalized = new Date(Date.UTC(year, month - 1, day));
  if (
    normalized.getUTCFullYear() !== year
    || normalized.getUTCMonth() !== month - 1
    || normalized.getUTCDate() !== day
  ) {
    throw new TypeError(`日期无效: ${value}`);
  }
  return { year, month, day };
}

function classicPoolDatesForYears(years) {
  if (!Array.isArray(years)) {
    throw new TypeError('years 必须是数组');
  }
  const uniqueYears = [...new Set(years)];
  if (uniqueYears.some((year) => !Number.isInteger(year))) {
    throw new TypeError('years 中的年份必须是整数');
  }

  const dates = [];
  for (const year of uniqueYears.sort((a, b) => a - b)) {
    const halfAnniversary = HALF_ANNIVERSARY_DATES[year];
    if (halfAnniversary) {
      dates.push({ type: 'half_anniversary', date: halfAnniversary });
    }
    dates.push({ type: 'anniversary', date: `${year}-07-21` });
  }
  return dates;
}

function validatePoolDates(poolDates) {
  if (!Array.isArray(poolDates)) {
    throw new TypeError('classicPoolDates 必须是数组');
  }
  return poolDates.map((event, index) => {
    if (!event || typeof event !== 'object' || Array.isArray(event)) {
      throw new TypeError(`classicPoolDates[${index}] 必须是对象`);
    }
    if (!Object.hasOwn(MARKER_LABELS, event.type)) {
      throw new TypeError(`classicPoolDates[${index}].type 不受支持`);
    }
    const parts = dateParts(event.date, /^\d{4}-\d{2}-\d{2}$/, '-');
    return { type: event.type, ...parts };
  });
}

function getClassicPoolMarks(startDate, periodDays, classicPoolDates) {
  const start = dateParts(startDate, /^\d{4}\.\d{2}\.\d{2}$/, '.');
  if (!Number.isInteger(periodDays) || periodDays <= 0) {
    throw new TypeError('periodDays 必须是正整数');
  }

  const startInstant = Date.UTC(start.year, start.month - 1, start.day, 2);
  const endInstant = Date.UTC(start.year, start.month - 1, start.day + periodDays, -3);
  const endDate = new Date(endInstant + 3 * 60 * 60 * 1000);
  const years = [start.year, endDate.getUTCFullYear()];
  const sourceDates = classicPoolDates === undefined
    ? classicPoolDatesForYears(years)
    : classicPoolDates;
  const poolDates = validatePoolDates(sourceDates);
  const matchedTypes = new Set();

  for (const event of poolDates) {
    const eventInstant = Date.UTC(event.year, event.month - 1, event.day, -8);
    if (eventInstant >= startInstant && eventInstant < endInstant) {
      matchedTypes.add(event.type);
    }
  }

  return ['half_anniversary', 'anniversary']
    .filter((type) => matchedTypes.has(type))
    .map((type) => MARKER_LABELS[type]);
}

module.exports = { classicPoolDatesForYears, getClassicPoolMarks };
