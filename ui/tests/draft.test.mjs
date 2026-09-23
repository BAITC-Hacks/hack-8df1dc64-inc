import test from 'node:test';
import assert from 'node:assert/strict';
import { defaultSelection, validateSelection } from '../parameters.mjs';
import { createDraftStore, DRAFT_KEY } from '../draft.mjs';

// A Web Storage test double, used only for persistence tests; no backend responses are mocked.
function memoryStorage() {
  const entries = new Map();
  return {
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value),
    removeItem: (key) => entries.delete(key),
  };
}

test('two turbine setup at 19 UTC is rendered without shifting the date', () => {
  const result = validateSelection(defaultSelection());
  assert.equal(result.valid, true);
  assert.deepEqual(result.preview, {
    date: '31 января 2026 г.', time: '19:00 UTC', horizon: '24 ч', turbines: 'Турбина 1, Турбина 2',
  });
});

test('one turbine with 48 hours at midnight is supported', () => {
  const result = validateSelection({ issueDate: '2026-02-14', issueHour: '00', horizon: '48', turbineIds: ['turbine_2'] });
  assert.equal(result.valid, true);
  assert.equal(result.preview.date, '14 февраля 2026 г.');
  assert.equal(result.preview.time, '00:00 UTC');
  assert.equal(result.preview.turbines, 'Турбина 2');
  assert.equal(result.preview.horizon, '48 ч');
});

test('invalid selections expose field errors and never produce a preview', () => {
  const result = validateSelection({ issueDate: '2026-02-30', issueHour: '24', horizon: '72', turbineIds: [] });
  assert.equal(result.valid, false);
  assert.deepEqual(Object.keys(result.errors), ['date', 'hour', 'horizon', 'turbines']);
  assert.equal(result.preview, undefined);
});

test('missing, duplicated and unknown turbines are rejected', () => {
  for (const turbineIds of [[], ['turbine_1', 'turbine_1'], ['turbine_3'], ['toString'], null, 'turbine_1', [true]]) {
    assert.ok(validateSelection({ ...defaultSelection(), turbineIds }).errors.turbines);
  }
});

test('only whole, explicitly selected UTC hours are accepted', () => {
  for (const issueHour of ['', '24', '-1', '19:30', '7', 7, null]) {
    assert.ok(validateSelection({ ...defaultSelection(), issueHour }).errors.hour);
  }
  assert.equal(validateSelection({ ...defaultSelection(), issueHour: '23' }).valid, true);
});

test('saved parameters are restored by a new store instance, with only allowed fields', () => {
  const storage = memoryStorage();
  const selection = { issueDate: '2026-02-14', issueHour: '00', horizon: '48', turbineIds: ['turbine_2'] };
  const first = createDraftStore(() => storage);
  assert.equal(first.load().state, 'empty');
  assert.equal(first.save({ ...selection, unrelated: 'not saved' }).state, 'saved');
  const restored = createDraftStore(() => storage).load();
  assert.deepEqual(restored, { state: 'restored', selection });
  assert.equal(JSON.parse(storage.getItem(DRAFT_KEY)).selection.unrelated, undefined);
});

test('clearing a draft removes only this UI key and restores no previous selection', () => {
  const storage = memoryStorage();
  const store = createDraftStore(() => storage);
  storage.setItem('another-application', 'keep');
  store.save(defaultSelection());
  assert.equal(store.clear().state, 'cleared');
  assert.equal(createDraftStore(() => storage).load().state, 'empty');
  assert.equal(storage.getItem('another-application'), 'keep');
  const defaults = defaultSelection();
  defaults.turbineIds.pop();
  assert.equal(defaultSelection().turbineIds.length, 2);
});

test('invalid current input removes the old saved selection to avoid stale restoration', () => {
  const storage = memoryStorage();
  const store = createDraftStore(() => storage);
  store.save(defaultSelection());
  assert.equal(store.save({ ...defaultSelection(), turbineIds: [] }).state, 'invalid');
  assert.equal(store.load().state, 'empty');
});

test('damaged, unknown-version or incomplete stored settings are not restored', () => {
  const storage = memoryStorage();
  const store = createDraftStore(() => storage);
  for (const raw of ['{', 'null', '[]', JSON.stringify({ version: 2, selection: defaultSelection() }), JSON.stringify({ version: 1, selection: { ...defaultSelection(), issueDate: '2030-01-01' } }), JSON.stringify({ version: 1 })]) {
    storage.setItem(DRAFT_KEY, raw);
    assert.equal(store.load().state, 'invalid', raw);
  }
});

test('denied storage access does not report save, restore or clear success', () => {
  const store = createDraftStore(() => { throw new Error('Storage access denied'); });
  assert.equal(store.load().state, 'unavailable');
  assert.equal(store.save(defaultSelection()).state, 'unavailable');
  assert.equal(store.save({ ...defaultSelection(), turbineIds: [] }).state, 'unavailable');
  assert.equal(store.clear().state, 'unavailable');
});

test('quota failures are surfaced without corrupting an existing draft', () => {
  const storage = memoryStorage();
  const store = createDraftStore(() => storage);
  store.save(defaultSelection());
  storage.setItem = () => { throw new Error('Quota exceeded'); };
  assert.equal(store.save({ ...defaultSelection(), horizon: '48' }).state, 'unavailable');
  assert.equal(store.load().selection.horizon, '24');
});
