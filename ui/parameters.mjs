// Presentation and form validation only; no weather, power or forecast-grid calculation.
const dateFormatter = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC',
});

export const TURBINES = Object.freeze({ turbine_1: 'Турбина 1', turbine_2: 'Турбина 2' });

export function defaultSelection() {
  return { issueDate: '2026-01-31', issueHour: '19', horizon: '24', turbineIds: Object.keys(TURBINES) };
}

export function validateSelection(selection) {
  const { issueDate, issueHour, horizon, turbineIds } = selection ?? {};
  const errors = {};
  let parsed;
  if (typeof issueDate !== 'string' || !issueDate) {
    errors.date = 'Выберите дату выпуска прогноза.';
  } else {
    parsed = new Date(`${issueDate}T00:00:00Z`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(issueDate) || Number.isNaN(parsed.getTime()) ||
        parsed.toISOString().slice(0, 10) !== issueDate) {
      errors.date = 'Введите корректную календарную дату.';
    } else if (issueDate < '2026-01-31' || issueDate > '2026-02-28') {
      errors.date = 'Выберите дату с 31 января по 28 февраля 2026 года.';
    }
  }
  if (typeof issueHour !== 'string' || !/^(0\d|1\d|2[0-3])$/.test(issueHour)) {
    errors.hour = 'Выберите час запуска от 00:00 до 23:00 UTC.';
  }
  if (horizon !== '24' && horizon !== '48') {
    errors.horizon = 'Выберите горизонт 24 или 48 часов.';
  }
  if (!Array.isArray(turbineIds) || turbineIds.length === 0 ||
      turbineIds.some((id) => typeof id !== 'string' || !Object.hasOwn(TURBINES, id)) ||
      new Set(turbineIds).size !== turbineIds.length) {
    errors.turbines = 'Выберите хотя бы одну из двух турбин.';
  }
  if (Object.keys(errors).length) return { valid: false, errors };
  return {
    valid: true, errors,
    preview: {
      date: dateFormatter.format(parsed), time: `${issueHour}:00 UTC`,
      horizon: `${horizon} ч`, turbines: turbineIds.map((id) => TURBINES[id]).join(', '),
    },
  };
}

export function copySelection(selection) {
  return {
    issueDate: selection.issueDate, issueHour: selection.issueHour,
    horizon: selection.horizon, turbineIds: [...selection.turbineIds],
  };
}
