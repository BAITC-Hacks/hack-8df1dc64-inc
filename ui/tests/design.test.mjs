import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { initializeInterface } from '../initialization.mjs';

function loadingElements() {
  return {
    form: { inert: false },
    region: { attributes: {}, setAttribute(name, value) { this.attributes[name] = value; } },
    loading: { hidden: true }, empty: { hidden: false, textContent: '' }, preview: { hidden: false },
  };
}

test('skeleton tracks a pending module load and releases the form only after completion', async () => {
  const elements = loadingElements();
  let complete;
  const pending = new Promise((resolve) => { complete = resolve; });
  const initialization = initializeInterface(() => pending, elements);
  assert.equal(elements.form.inert, true);
  assert.equal(elements.loading.hidden, false);
  assert.equal(elements.region.attributes['aria-busy'], 'true');
  complete();
  assert.deepEqual(await initialization, { state: 'ready' });
  assert.equal(elements.form.inert, false);
  assert.equal(elements.loading.hidden, true);
  assert.equal(elements.region.attributes['aria-busy'], 'false');
});

test('failed module load hides the skeleton and any partial preview, exposing a retry message', async () => {
  const elements = loadingElements();
  const result = await initializeInterface(() => Promise.reject(new Error('Module unavailable')), elements);
  assert.deepEqual(result, { state: 'error' });
  assert.equal(elements.form.inert, true);
  assert.equal(elements.loading.hidden, true);
  assert.equal(elements.preview.hidden, true);
  assert.equal(elements.empty.hidden, false);
  assert.match(elements.empty.textContent, /Не удалось загрузить форму/);
  assert.equal(elements.region.attributes['aria-busy'], 'false');
});

test('loaded styling and public pages respect the user design restrictions', async () => {
  const css = await readFile(new URL('../styles.css', import.meta.url), 'utf8');
  assert.doesNotMatch(css, /gradient\(|box-shadow\s*:|drop-shadow\(|backdrop-filter\s*:|transition\s*:|animation\s*:|border-left\s*:/i);
  assert.doesNotMatch(css, /(?:background|background-color)\s*:\s*(?:white\b|#fff(?:fff)?\b)/i);
  assert.doesNotMatch(css, /font-family\s*:[^;]*(?:\bInter\b|\bGeist\b|Space Grotesk)/i);
  for (const match of css.matchAll(/border-radius\s*:\s*([^;]+)/g)) assert.equal(match[1].trim(), '0');
  for (const file of ['index.html', 'terms.html', 'privacy.html']) {
    const html = await readFile(new URL(`../${file}`, import.meta.url), 'utf8');
    assert.doesNotMatch(html, /\u2014|lucide|\p{Extended_Pictographic}/iu, file);
    assert.match(html, /href="\.\/styles.css"/, file);
    assert.match(html, /href="\.\/privacy.html"|Конфиденциальность/, file);
    assert.match(html, /href="\.\/terms.html"|Условия использования/, file);
  }
});
