import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createPowerChart } from '../results.mjs';

// Minimal DOM boundary for SVG construction; no browser layout or API is simulated.
const document = {
  createElementNS(namespace, tag) {
    return { namespace, tag, attributes: {}, children: [], textContent: '',
      setAttribute(name, value) { this.attributes[name] = String(value); },
      append(...children) { this.children.push(...children); },
    };
  },
};

async function forecastFor(day) {
  return JSON.parse(await readFile(new URL(`../../backend/reports/february/runs/${day}T190000Z.json`, import.meta.url), 'utf8'));
}

test('24-hour chart shows both actual series, grid and UTC labels without changing values', async () => {
  const source = await forecastFor('20260131');
  const forecast = { ...source, horizon_hours: 24, points: source.points.filter((point) => Date.parse(point.valid_at) <= Date.parse(source.as_of) + 24 * 3600000) };
  const before = JSON.stringify(forecast);
  const chart = createPowerChart(forecast, document);
  const grid = chart.children.find((node) => node.attributes.class === 'chart-grid');
  assert.equal(grid.children.length, 10, 'Five horizontal and five vertical grid lines');
  const lines = chart.children.filter((node) => node.tag === 'polyline');
  assert.equal(lines.length, 2);
  assert.equal(lines[0].attributes.stroke, '#c62828');
  assert.equal(lines[0].attributes['stroke-dasharray'], undefined);
  assert.equal(lines[1].attributes.stroke, '#1565c0');
  assert.equal(lines[1].attributes['stroke-dasharray'], '7 4');
  for (const line of lines) {
    const values = forecast.points.filter((point) => point.turbine_id === line.attributes['data-turbine']);
    const plotted = line.attributes.points.split(' ').map((pair) => pair.split(',').map(Number));
    assert.equal(plotted.length, 24);
    assert.equal(plotted[0][0], 44);
    assert.equal(plotted.at(-1)[0], 584);
    plotted.forEach(([, y], i) => assert.ok(Math.abs((220 - y) / 200 - values[i].normalized_power) < 1e-12));
  }
  const labels = chart.children.filter((node) => node.tag === 'text').map((node) => node.textContent);
  assert.ok(labels.includes('20:00'));
  assert.ok(labels.includes('19:00'));
  assert.equal(JSON.stringify(forecast), before);
});

test('48-hour single turbine 2 keeps its blue dashed identity and every forecast point', async () => {
  const source = await forecastFor('20260214');
  const forecast = { ...source, turbine_ids: ['turbine_2'], points: source.points.filter((point) => point.turbine_id === 'turbine_2') };
  const chart = createPowerChart(forecast, document);
  const lines = chart.children.filter((node) => node.tag === 'polyline');
  assert.equal(lines.length, 1);
  assert.equal(lines[0].attributes.stroke, '#1565c0');
  assert.equal(lines[0].attributes['stroke-dasharray'], '7 4');
  assert.equal(lines[0].attributes.points.split(' ').length, 48);
  assert.doesNotMatch(lines[0].attributes.points, /NaN|Infinity/);
});

test('public pages use Vento and privacy explains storage, deletion and external analysis', async () => {
  for (const file of ['index.html', 'privacy.html', 'terms.html']) {
    const html = await readFile(new URL(`../${file}`, import.meta.url), 'utf8');
    assert.match(html, /<title>[^<]*Vento/);
    assert.doesNotMatch(html, /ветропрогноз|полный цикл агента/iu);
    if (file === 'index.html') assert.match(html, /Полный цикл прогноза/);
    if (file === 'privacy.html') {
      for (const text of ['OpenAI', 'NOAA', 'Сбросить параметры', 'не удаляется автоматически', 'скачанные вами CSV']) assert.ok(html.includes(text), text);
      assert.doesNotMatch(html, /localStorage|store=false|backend|Ventoа/);
    }
  }
});
