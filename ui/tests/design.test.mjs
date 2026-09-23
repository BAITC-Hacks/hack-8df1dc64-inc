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
  assert.doesNotMatch(css, /gradient\(|box-shadow\s*:|drop-shadow\(|backdrop-filter\s*:|transition\s*:|border-left\s*:/i);
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

test('station illustration has unique identifiers and resolves every reused SVG shape', async () => {
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
  const svg = html.match(/<svg\b[^>]*>[\s\S]*?<\/svg>/)?.[0];
  assert.ok(svg, 'Station illustration must be present');
  const ids = [...svg.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length, 'SVG identifiers must be unique');
  const references = [...svg.matchAll(/<use\b[^>]*href="#([^"]+)"/g)];
  assert.ok(references.length > 0, 'Illustration must reuse its shared geometry');
  for (const [, id] of references) assert.ok(ids.includes(id), `Missing SVG shape: ${id}`);
  assert.doesNotMatch(svg, /<image\b|https?:\/\//i, 'Illustration must work without remote image assets');
});

test('decorative motion is confined to the illustration and opted out by reduced-motion', async () => {
  const css = await readFile(new URL('../styles.css', import.meta.url), 'utf8');
  const motionBlock = css.match(/@media \(prefers-reduced-motion: no-preference\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}/);
  assert.ok(motionBlock, 'Animation must require no-preference, leaving a static fallback');
  assert.doesNotMatch(css.replace(motionBlock[0], ''), /animation(?:-[\w-]+)?\s*:/, 'No animation outside the motion preference gate');
  const rules = [...motionBlock[1].matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  assert.ok(rules.some(([, , body]) => /animation\s*:/.test(body)));
  for (const [, selector] of rules) assert.match(selector.trim(), /^\.station-art \.[\w-]+$/, 'Motion must stay within station artwork');

  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
  const rotors = [...html.matchAll(/<g transform="translate\(([^)]+)\)(?: scale\([^)]+\))?">\s*<g class="turbine-spin[^\"]*">([\s\S]*?)<\/g>\s*<\/g>/g)];
  assert.equal(rotors.length, 2, 'Both rotors keep a fixed parent position');
  for (const [, , content] of rotors) {
    assert.match(content, /<use href="#turbine-rotor"/);
    assert.doesNotMatch(content, /<path/, 'Towers and hills must not rotate with the blades');
  }
  assert.match(css, /\.station-art \.turbine-spin\s*\{[^}]*transform-origin:\s*0 0;/);
});
