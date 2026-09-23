export async function initializeInterface(loadApp, { form, region, loading, empty, preview }) {
  form.inert = true;
  region.setAttribute('aria-busy', 'true');
  loading.hidden = false;
  empty.hidden = true;
  try {
    await loadApp();
    form.inert = false;
    return { state: 'ready' };
  } catch {
    preview.hidden = true;
    empty.hidden = false;
    empty.textContent = 'Не удалось загрузить форму. Обновите страницу, чтобы повторить попытку.';
    return { state: 'error' };
  } finally {
    loading.hidden = true;
    region.setAttribute('aria-busy', 'false');
  }
}
