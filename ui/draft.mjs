import { copySelection, validateSelection } from './parameters.mjs';

export const DRAFT_KEY = 'wind-forecast.selection.v1';

// Only this UI's parameters are persisted. Storage errors are returned, never disguised as success.
export function createDraftStore(getStorage) {
  function clear() {
    try {
      getStorage().removeItem(DRAFT_KEY);
      return { state: 'cleared' };
    } catch {
      return { state: 'unavailable' };
    }
  }
  return {
    clear,
    load() {
      let raw;
      try { raw = getStorage().getItem(DRAFT_KEY); } catch { return { state: 'unavailable' }; }
      if (raw === null) return { state: 'empty' };
      try {
        const draft = JSON.parse(raw);
        if (draft?.version !== 1 || !validateSelection(draft.selection).valid) return { state: 'invalid' };
        return { state: 'restored', selection: copySelection(draft.selection) };
      } catch {
        return { state: 'invalid' };
      }
    },
    save(selection) {
      if (!validateSelection(selection).valid) {
        return { state: clear().state === 'unavailable' ? 'unavailable' : 'invalid' };
      }
      try {
        getStorage().setItem(DRAFT_KEY, JSON.stringify({ version: 1, selection: copySelection(selection) }));
        return { state: 'saved' };
      } catch {
        return { state: 'unavailable' };
      }
    },
  };
}
