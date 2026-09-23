import { describeSelection } from './parameters.mjs';

const form = document.querySelector('#forecast-form');
const dateInput = document.querySelector('#issue-date');
const error = document.querySelector('#form-error');
const summary = document.querySelector('#selection-summary');

function updateSelection() {
  const selected = new FormData(form);
  const result = describeSelection(selected.get('issue-date'), selected.get('horizon'));
  error.hidden = !result.error;
  error.textContent = result.error ?? '';
  dateInput.setAttribute('aria-invalid', result.field === 'date' ? 'true' : 'false');
  summary.textContent = result.summary ?? '';
}

form.addEventListener('input', updateSelection);
form.addEventListener('change', updateSelection);
// No backend request is defined until docs/api-contract.md is agreed.
// Also prevent implicit submission with Enter while the service is unavailable.
form.addEventListener('submit', (event) => {
  event.preventDefault();
  updateSelection();
});
updateSelection();
