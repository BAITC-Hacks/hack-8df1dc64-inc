import { initializeInterface } from './initialization.mjs';

await initializeInterface(() => import('./app.mjs'), {
  form: document.querySelector('#forecast-form'),
  region: document.querySelector('#selection-region'),
  loading: document.querySelector('#selection-loading'),
  empty: document.querySelector('#selection-empty'),
  preview: document.querySelector('#selection-summary'),
});
