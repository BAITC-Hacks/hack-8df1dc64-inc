import test from 'node:test';
import assert from 'node:assert/strict';
import { forecastRequest, requestForecast, csvResult } from '../api.mjs';
import { defaultSelection } from '../parameters.mjs';

test('UI sends precise UTC, explicit timestamp role and refresh choice', () => {
  assert.deepEqual(forecastRequest(defaultSelection(), 'end'), {
    as_of: '2026-01-31T19:00:00Z', horizon_hours: 24,
    turbine_ids: ['turbine_1', 'turbine_2'], timestamp_role: 'end', refresh_weather: false,
  });
  assert.equal(forecastRequest({ ...defaultSelection(), issueHour: '00', horizon: '48' }, 'start', true).refresh_weather, true);
  assert.throws(() => forecastRequest(defaultSelection(), 'unknown'));
  assert.throws(() => forecastRequest({ ...defaultSelection(), turbineIds: [] }, 'end'));
});

test('request uses agent endpoint and passes through actual server values', async () => {
  const body = forecastRequest(defaultSelection(), 'end');
  const fixture = { forecast: { points: [{ normalized_power: 0.321 }] }, analysis: { summary: 'Test boundary' }, cycle: [] };
  const result = await requestForecast('http://127.0.0.1:8000', body, async (url, options) => {
    assert.equal(url, 'http://127.0.0.1:8000/api/agent/forecasts');
    assert.deepEqual(JSON.parse(options.body), body);
    return { ok: true, json: async () => fixture };
  });
  assert.equal(result.forecast.points[0].normalized_power, 0.321);
});

test('provider and invalid JSON failures never produce successful UI result', async () => {
  await assert.rejects(requestForecast('', {}, async () => ({ ok: false, status: 503, json: async () => ({ error: { code: 'WEATHER_UNAVAILABLE' } }) })), /WEATHER_UNAVAILABLE/);
  await assert.rejects(requestForecast('', {}, async () => ({ ok: true, json: async () => ({}) })), /Неполный/);
  await assert.rejects(requestForecast('', {}, async () => ({ ok: false, json: async () => { throw Error('bad json'); } })), /формате/);
});

test('CSV preserves actual values and timestamp fields', () => {
  const csv = csvResult([{ turbine_id: 'turbine_1', valid_at: '2026-02-01T00:00:00Z', normalized_power: 0.27, wind_speed_ms: 3.1, temperature_c: -4, extrapolated: false }]);
  assert.match(csv, /"0.27","3.1","-4","false"/);
  assert.match(csv, /2026-02-01T00:00:00Z/);
});
