// Real HTTP/OpenAI smoke check using the same client as the browser. No mock fallback.
import { writeFile } from 'node:fs/promises';
import { forecastRequest, requestForecast } from './api.mjs';

const [base = 'http://127.0.0.1:8000', output] = process.argv.slice(2);
const cases = [
  { issueDate: '2026-01-31', issueHour: '19', horizon: '24', turbineIds: ['turbine_1', 'turbine_2'] },
  { issueDate: '2026-02-14', issueHour: '19', horizon: '48', turbineIds: ['turbine_2'] },
];
const report = { checked_at: new Date().toISOString(), cases: [] };
for (const [index, selection] of cases.entries()) {
  const payload = forecastRequest(selection, 'end', index === 1 && process.env.LIVE_REFRESH === '1');
  const result = await requestForecast(base, payload);
  if (result.forecast.points.length !== Number(selection.horizon) * selection.turbineIds.length) {
    throw new Error('Unexpected number of forecast points');
  }
  if (!result.analysis.response_id || result.forecast.timestamp_role_confirmed !== false) {
    throw new Error('Missing live analysis or assumption marker');
  }
  report.cases.push({ request: payload, result });
  console.log(JSON.stringify({ as_of: payload.as_of, points: result.forecast.points.length,
    source: result.weather_source.mode, model: result.analysis.model,
    response_id: result.analysis.response_id, cycle: result.cycle }));
}
if (output) await writeFile(output, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
