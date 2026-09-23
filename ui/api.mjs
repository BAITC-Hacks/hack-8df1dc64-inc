import { validateSelection } from './parameters.mjs';

export function forecastRequest(selection, timestampRole, refresh = false) {
  if (!validateSelection(selection).valid) throw new Error('Исправьте параметры прогноза.');
  if (!['start', 'end'].includes(timestampRole)) throw new Error('Выберите смысл временной метки.');
  return { as_of: `${selection.issueDate}T${selection.issueHour}:00:00Z`,
    horizon_hours: Number(selection.horizon), turbine_ids: [...selection.turbineIds],
    timestamp_role: timestampRole, refresh_weather: refresh };
}

export async function requestForecast(base, payload, fetcher = fetch) {
  const response = await fetcher(`${base}/api/agent/forecasts`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  let result;
  try { result = await response.json(); } catch { throw new Error('Сервис вернул ответ в неизвестном формате.'); }
  if (!response.ok) {
    const code = result.error?.code ?? `HTTP_${response.status}`;
    const messages = {
      OPENAI_NOT_CONFIGURED: 'На сервере не настроен ключ OpenAI.',
      OPENAI_AUTH_ERROR: 'OpenAI отклонил ключ или доступ сервера.',
      OPENAI_RATE_LIMITED: 'Достигнут лимит OpenAI. Повторите позже.',
      WEATHER_UNAVAILABLE: 'Не удалось получить архив NOAA. Проверьте доступ к источнику и повторите запуск.',
      INVALID_WEATHER: 'Погодный архив не прошёл проверку.',
    };
    throw new Error(`${messages[code] ?? result.error?.message ?? 'Запрос не выполнен.'} (${code})`);
  }
  if (!Array.isArray(result.forecast?.points) || !result.analysis?.summary || !Array.isArray(result.cycle)) {
    throw new Error('Неполный результат сервиса. Расчёт не показан.');
  }
  return result;
}

export function csvResult(points) {
  const fields = ['turbine_id', 'valid_at', 'normalized_power', 'wind_speed_ms', 'temperature_c', 'extrapolated'];
  const quote = (value) => `"${String(value).replaceAll('"', '""')}"`;
  return [fields.join(','), ...points.map((point) => fields.map((key) => quote(point[key])).join(','))].join('\r\n');
}
