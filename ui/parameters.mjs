// UI input validation only. Forecast dates, weather and power are calculated by backend.
const dateFormatter = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC',
});

export function describeSelection(issueDate, horizon) {
  if (!issueDate) return { error: 'Выберите дату выпуска прогноза.', field: 'date' };
  const parsed = new Date(`${issueDate}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(issueDate) || Number.isNaN(parsed.getTime()) ||
      parsed.toISOString().slice(0, 10) !== issueDate) {
    return { error: 'Введите корректную календарную дату.', field: 'date' };
  }
  if (issueDate < '2026-01-31' || issueDate > '2026-02-28') {
    return { error: 'Выберите дату с 31 января по 28 февраля 2026 года.', field: 'date' };
  }
  if (horizon !== '24' && horizon !== '48') {
    return { error: 'Выберите горизонт 24 или 48 часов.', field: 'horizon' };
  }
  return { summary: `Дата выпуска: ${dateFormatter.format(parsed)} · Горизонт: ${horizon} ч` };
}
