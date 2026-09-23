import { defaultSelection, validateSelection } from './parameters.mjs';
import { createDraftStore } from './draft.mjs';

const form = document.querySelector('#forecast-form');
const dateInput = document.querySelector('#issue-date');
const hourInput = document.querySelector('#issue-hour');
const preview = document.querySelector('#selection-summary');
const previewEmpty = document.querySelector('#selection-empty');
const storageStatus = document.querySelector('#storage-status');
const store = createDraftStore(() => window.localStorage);
const storageMessages = {
  empty: 'Параметры сохраняются только в этом браузере.',
  restored: 'Восстановлены ваши последние параметры.',
  saved: 'Параметры сохранены в этом браузере.',
  invalid: 'Неполные или некорректные параметры не сохранены.',
  unavailable: 'Сохранение недоступно. Текущий выбор действует до перезагрузки страницы.',
  cleared: 'Сохранённые параметры удалены. Установлен начальный выбор.',
};

for (let hour = 0; hour < 24; hour++) {
  const value = String(hour).padStart(2, '0');
  hourInput.add(new Option(`${value}:00`, value));
}

function readSelection() {
  const selected = new FormData(form);
  return {
    issueDate: selected.get('issue-date'), issueHour: selected.get('issue-hour'),
    horizon: selected.get('horizon'), turbineIds: selected.getAll('turbine'),
  };
}

function applySelection(selection) {
  dateInput.value = selection.issueDate;
  hourInput.value = selection.issueHour;
  for (const input of form.querySelectorAll('[name="horizon"]')) input.checked = input.value === selection.horizon;
  for (const input of form.querySelectorAll('[name="turbine"]')) input.checked = selection.turbineIds.includes(input.value);
}

function updateSelection() {
  const result = validateSelection(readSelection());
  for (const [field, selector] of Object.entries({
    date: '#issue-date', hour: '#issue-hour', horizon: '[name="horizon"]', turbines: '[name="turbine"]',
  })) {
    const error = document.querySelector(`#${field}-error`);
    error.hidden = !result.errors[field];
    error.textContent = result.errors[field] ?? '';
    for (const input of form.querySelectorAll(selector)) input.setAttribute('aria-invalid', String(Boolean(result.errors[field])));
  }
  preview.hidden = !result.valid;
  previewEmpty.hidden = result.valid;
  for (const field of ['date', 'time', 'horizon', 'turbines']) {
    document.querySelector(`#preview-${field}`).textContent = result.preview?.[field] ?? '';
  }
  document.querySelector('#selection-announcement').textContent = result.valid
    ? `${result.preview.date}, ${result.preview.time}. ${result.preview.horizon}. ${result.preview.turbines}.`
    : 'Для сводки исправьте отмеченные параметры.';
}

function persistChange() {
  updateSelection();
  storageStatus.textContent = storageMessages[store.save(readSelection()).state];
}
form.addEventListener('input', persistChange);
form.addEventListener('change', persistChange);
// No backend request is defined until docs/api-contract.md is agreed.
// Also prevent implicit submission with Enter while the service is unavailable.
form.addEventListener('submit', (event) => {
  event.preventDefault();
  updateSelection();
});
document.querySelector('#reset-selection').addEventListener('click', () => {
  const result = store.clear();
  applySelection(defaultSelection());
  updateSelection();
  storageStatus.textContent = result.state === 'unavailable'
    ? 'Начальный выбор установлен. Браузер не разрешил удалить сохранённые параметры.'
    : storageMessages.cleared;
});
const saved = store.load();
applySelection(saved.selection ?? defaultSelection());
storageStatus.textContent = saved.state === 'invalid'
  ? 'Сохранённые параметры повреждены или устарели. Установлен начальный выбор.'
  : storageMessages[saved.state];
updateSelection();
