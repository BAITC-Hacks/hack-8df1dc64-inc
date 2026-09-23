import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { describeSelection } from '../parameters.mjs';
import { createUiServer } from '../server.mjs';

test('initial historical run: 31 January, 24 hours', () => {
  const result = describeSelection('2026-01-31', '24');
  assert.equal(result.error, undefined);
  assert.match(result.summary, /31 января 2026.*Горизонт: 24 ч/);
});

test('another run: 14 February, 48 hours', () => {
  const result = describeSelection('2026-02-14', '48');
  assert.equal(result.error, undefined);
  assert.match(result.summary, /14 февраля 2026.*Горизонт: 48 ч/);
});

test('last test date is selectable', () => {
  assert.equal(describeSelection('2026-02-28', '48').error, undefined);
});

test('missing, impossible and out-of-period dates produce an error without a summary', () => {
  for (const value of ['', '2026-02-29', '2026-02-30', 'not-a-date', '2026-01-30', '2026-03-01']) {
    const result = describeSelection(value, '24');
    assert.ok(result.error, value);
    assert.equal(result.summary, undefined, value);
  }
});

test('unsupported horizons cannot be described as valid', () => {
  for (const horizon of ['0', '25', '72', '', null]) {
    assert.ok(describeSelection('2026-02-01', horizon).error);
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
  assert.match(html, /type="submit" disabled/);
  assert.match(html, /Прогноз ещё не сформирован/);
  for (const [path, mime] of [['styles.css', 'text/css'], ['app.mjs', 'text/javascript'], ['parameters.mjs', 'text/javascript']]) {
    const result = await fetch(`${base}/${path}`);
    assert.equal(result.status, 200, path);
    assert.ok(result.headers.get('content-type').startsWith(mime), path);
    assert.ok((await result.text()).length > 0, path);
  }
  assert.equal((await fetch(`${base}/api/forecast`, { method: 'POST' })).status, 404);
  assert.equal((await fetch(`${base}/README.md`)).status, 404);
});
