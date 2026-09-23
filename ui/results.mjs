import { TURBINES } from './parameters.mjs';
import { csvResult } from './api.mjs';

const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 3 });
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

const seriesStyles = {
  turbine_1: { color: '#c62828', description: 'красная сплошная', dash: null },
  turbine_2: { color: '#1565c0', description: 'синяя прерывистая', dash: '7 4' },
};

export function createPowerChart(forecast, document = globalThis.document) {
  const ns = 'http://www.w3.org/2000/svg';
  function svgElement(tag, attributes, text) {
    const node = document.createElementNS(ns, tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
    if (text !== undefined) node.textContent = text;
    return node;
  }
  const chart = svgElement('svg', {
    viewBox: '0 0 600 260', role: 'img', class: 'power-chart',
    'aria-label': 'Почасовая мощность: турбина 1 красная сплошная, турбина 2 синяя прерывистая. Время UTC. Точные значения в таблице ниже.',
  });
  const grid = svgElement('g', { class: 'chart-grid', stroke: '#d9dde1', 'stroke-width': 1 });
  chart.append(grid);
  for (const value of [0, 0.25, 0.5, 0.75, 1]) {
    const y = 220 - value * 200;
    grid.append(svgElement('line', { x1: 44, x2: 584, y1: y, y2: y }));
    chart.append(svgElement('text', { x: 36, y: y + 4, 'text-anchor': 'end' }, number.format(value)));
  }
  const timeline = forecast.points.filter((point) => point.turbine_id === forecast.turbine_ids[0]);
  const xAt = (index, length) => 44 + index * 540 / Math.max(1, length - 1);
  const ticks = [...new Set([0, 0.25, 0.5, 0.75, 1].map((ratio) => Math.round(ratio * (timeline.length - 1))))];
  for (const index of ticks) {
    const x = xAt(index, timeline.length);
    grid.append(svgElement('line', { x1: x, x2: x, y1: 20, y2: 220 }));
    chart.append(svgElement('text', { x, y: 243, 'text-anchor': index === 0 ? 'start' : index === timeline.length - 1 ? 'end' : 'middle' }, timeline[index].valid_at.slice(11, 16)));
  }
  for (const turbine of forecast.turbine_ids) {
    const points = forecast.points.filter((point) => point.turbine_id === turbine);
    const style = seriesStyles[turbine];
    const line = svgElement('polyline', {
      'data-turbine': turbine,
      points: points.map((point, index) => `${xAt(index, points.length)},${220 - point.normalized_power * 200}`).join(' '),
      fill: 'none', stroke: style.color, 'stroke-width': 2.5, 'stroke-linejoin': 'round',
    });
    if (style.dash) line.setAttribute('stroke-dasharray', style.dash);
    chart.append(line);
  }
  return chart;
}

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
  for (const turbine of forecast.turbine_ids) element('p', `${TURBINES[turbine]}: ${seriesStyles[turbine].description} линия`, root);
  root.append(createPowerChart(forecast));
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
  for (const item of result.cycle) element('li', `${steps[item.step] ?? item.step}: ${number.format(item.duration_ms / 1000)} с`, trace);
  element('p', `Модель анализа: ${analysis.model}. Ответ: ${analysis.response_id}.`, root);
}
