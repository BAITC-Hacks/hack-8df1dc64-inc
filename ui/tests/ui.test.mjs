import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { defaultSelection, validateSelection } from '../parameters.mjs';
import { createUiServer } from '../server.mjs';

test('initial historical run: 31 January, 24 hours', () => {
  const result = validateSelection(defaultSelection());
  assert.equal(result.valid, true);
  assert.match(result.preview.date, /31 января 2026/);
  assert.equal(result.preview.horizon, '24 ч');
});

test('another run: 14 February, 48 hours', () => {
  const result = validateSelection({ ...defaultSelection(), issueDate: '2026-02-14', horizon: '48' });
  assert.equal(result.valid, true);
  assert.match(result.preview.date, /14 февраля 2026/);
  assert.equal(result.preview.horizon, '48 ч');
});

test('last test date is selectable', () => {
  assert.equal(validateSelection({ ...defaultSelection(), issueDate: '2026-02-28', horizon: '48' }).valid, true);
});

test('missing, impossible and out-of-period dates produce an error without a summary', () => {
  for (const value of ['', '2026-02-29', '2026-02-30', 'not-a-date', '2026-01-30', '2026-03-01']) {
    const result = validateSelection({ ...defaultSelection(), issueDate: value });
    assert.ok(result.errors.date, value);
    assert.equal(result.preview, undefined, value);
  }
});

test('unsupported horizons cannot be described as valid', () => {
  for (const horizon of ['0', '25', '72', '', null]) {
    assert.ok(validateSelection({ ...defaultSelection(), horizon }).errors.horizon);
  }
});

test('static server serves the page and every linked asset, without API routes', async (t) => {
  const server = createUiServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const base = `http://127.0.0.1:${server.address().port}`;
  const page = await fetch(base);
  assert.equal(page.status, 200);
  const html = await page.text();
  assert.match(html, /<html lang="ru">/);
  assert.match(html, /id="issue-date"/);
  assert.match(html, /id="issue-hour"/);
  assert.match(html, /name="turbine" value="turbine_1"/);
  assert.match(html, /name="turbine" value="turbine_2"/);
  assert.match(html, /id="reset-selection"/);
  assert.match(html, /id="preview-time"/);
  assert.match(html, /type="submit" disabled/);
  assert.match(html, /Прогноз ещё не сформирован/);
  for (const [path, mime] of [['styles.css', 'text/css'], ['app.mjs', 'text/javascript'], ['parameters.mjs', 'text/javascript'], ['draft.mjs', 'text/javascript']]) {
    const result = await fetch(`${base}/${path}`);
    assert.equal(result.status, 200, path);
    assert.ok(result.headers.get('content-type').startsWith(mime), path);
    assert.ok((await result.text()).length > 0, path);
  }
  assert.equal((await fetch(`${base}/api/forecast`, { method: 'POST' })).status, 404);
  assert.equal((await fetch(`${base}/README.md`)).status, 404);
});
