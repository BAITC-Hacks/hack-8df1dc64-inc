import { TURBINES } from './parameters.mjs';
import { csvResult } from './api.mjs';

const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 3 });
export function formatDuration(durationMs) {
  // API durations are milliseconds; show both units without locale ambiguity.
  return `${(durationMs / 1000).toFixed(2)} с (${Math.round(durationMs)} мс)`;
}
const warningTexts = {
  TIMESTAMP_ROLE_UNCONFIRMED: 'Смысл временной метки выбран как допущение и не подтверждён организатором.',
  WIND_HEIGHT_UNKNOWN: 'Высота исторического измерения ветра неизвестна; поправка высоты не применена.',
  TELEMETRY_AVAILABILITY_UNCONFIRMED: 'Время публикации телеметрии неизвестно; используется конец интервала.',
  WIND_OUTSIDE_CURVE: 'Часть скоростей вне диапазона исторической кривой; использовано ближайшее крайнее значение.',
};
const steps = { acquire_weather: 'Получение и проверка погоды', refresh_weather: 'Обновление NOAA',
  prepare_and_forecast: 'Подготовка истории и расчёт', analyze: 'Анализ OpenAI',
  agent_refresh_weather: 'Проверка обновления по решению агента', recalculate: 'Повторный расчёт', reanalyze: 'Повторный анализ' };
let downloadUrl;

function element(tag, text, parent) {
  const node = document.createElement(tag);
  node.textContent = text;
  parent.append(node);
  return node;
}

export function renderResult(result) {
  const root = document.querySelector('#forecast-output');
  root.replaceChildren();
  root.hidden = false;
  const { forecast, analysis } = result;
  element('h3', 'Нормализованная мощность по часам', root);
  element('p', `Запуск ${forecast.as_of}; горизонт ${forecast.horizon_hours} ч. Значения в исходной шкале 0–1, без перевода в кВт.`, root);
  const ns = 'http://www.w3.org/2000/svg';
  const chart = document.createElementNS(ns, 'svg');
  chart.setAttribute('viewBox', '0 0 600 230');
  chart.setAttribute('role', 'img');
  chart.setAttribute('aria-label', 'График нормализованной мощности; точные значения приведены в таблице ниже.');
  chart.classList.add('power-chart');
  for (const y of [0, 0.5, 1]) {
    const label = document.createElementNS(ns, 'text');
    label.setAttribute('x', '0'); label.setAttribute('y', String(200 - y * 180)); label.textContent = String(y);
    chart.append(label);
  }
  forecast.turbine_ids.forEach((turbine, index) => {
    const points = forecast.points.filter((point) => point.turbine_id === turbine);
    const line = document.createElementNS(ns, 'polyline');
    line.setAttribute('points', points.map((point, i) => `${40 + i * 550 / (points.length - 1)},${200 - point.normalized_power * 180}`).join(' '));
    line.setAttribute('fill', 'none'); line.setAttribute('stroke', index === 0 ? '#843d25' : '#303839');
    line.setAttribute('stroke-width', '2'); if (index) line.setAttribute('stroke-dasharray', '6 3');
    chart.append(line);
    element('p', `${TURBINES[turbine]}: ${index ? 'тёмная пунктирная' : 'терракотовая сплошная'} линия`, root);
  });
  root.append(chart);
  element('p', `От ${forecast.points[0].valid_at} до ${forecast.points[forecast.horizon_hours - 1].valid_at}. Время UTC, метка конца часа.`, root);
  const details = element('details', '', root);
  element('summary', 'Таблица всех часов', details);
  const scroll = element('div', '', details); scroll.className = 'table-scroll';
  const table = element('table', '', scroll);
  const head = element('tr', '', element('thead', '', table));
  for (const title of ['Турбина', 'Конец часа UTC', 'Мощность 0–1', 'Ветер, м/с', 'Температура, °C', 'Вне кривой']) element('th', title, head);
  const body = element('tbody', '', table);
  for (const point of forecast.points) {
    const row = element('tr', '', body);
    for (const value of [TURBINES[point.turbine_id], point.valid_at, number.format(point.normalized_power),
      number.format(point.wind_speed_ms), number.format(point.temperature_c), point.extrapolated ? 'Да' : 'Нет']) element('td', value, row);
  }
  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
  downloadUrl = URL.createObjectURL(new Blob([csvResult(forecast.points)], { type: 'text/csv;charset=utf-8' }));
  const download = element('a', 'Скачать CSV', root); download.href = downloadUrl; download.download = 'wind-forecast.csv';
  element('h3', 'Анализ OpenAI', root);
  element('p', analysis.summary, root);
  const risks = element('ul', '', root);
  for (const risk of analysis.risks) element('li', risk, risks);
  element('p', analysis.reason, root);
  element('h3', 'Ограничения расчёта', root);
  const warnings = element('ul', '', root);
  for (const warning of forecast.warnings) element('li', warningTexts[warning.code] ?? warning.message, warnings);
  element('p', 'Архив NOAA имеет сетку 0.5°. Погода двух близких турбин может совпадать. Точность на февральских фактах пока не оценена.', root);
  element('h3', 'Источник и выполненные этапы', root);
  element('p', `${forecast.weather.source}; выпуск ${forecast.weather.run_id}; доступен ${forecast.weather.available_at}.`, root);
  const trace = element('ol', '', root);
  for (const item of result.cycle) element('li', `${steps[item.step] ?? item.step}: ${formatDuration(item.duration_ms)}`, trace);
  element('p', `Модель анализа: ${analysis.model}. Ответ: ${analysis.response_id}.`, root);
}
