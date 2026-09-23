# Backend: этапы B1–B4

Проверка входов, расчёт по истории двух ВЭС и HTTP API. Требуется **Python 3.11+**;
сторонние зависимости и ключи API для B1–B3 не нужны. Используется стандартная библиотека.

Контракты: [интерфейс](../docs/api-contract.md) и [схема данных](../docs/db-schema.md).
Запускать команды ниже из корня клона `hackathon-repo`.

## Тесты

```bash
bash backend/test.sh
```

Либо в PowerShell / любой оболочке с Python:

```text
python -m unittest discover -s backend/tests -t . -v
```

Если Bash не находит нужный интерпретатор, задайте `PYTHON_BIN` — путь к Python 3.11+.
Файл `.gitattributes` сохраняет окончания строк shell-скрипта LF при клонировании на Windows.

## Два ручных сценария

```text
python -m backend.manual_check
python -m backend.manual_check --late-weather
```

Первый проверяет 96 точек: две турбины, 48 часов; результат `input_validated`, код выхода 0.
Второй использует выпуск, опубликованный через минуту после момента запуска:
ожидается `WEATHER_NOT_AVAILABLE`, поле `weather.available_at`, код выхода 2.
Оба сценария используют явно искусственные данные для проверки правил.

## Использование из Python

```python
from backend.validation import ForecastValidationError, validate_forecast_inputs

try:
    validated = validate_forecast_inputs(request, weather)
except ForecastValidationError as error:
    print(error.as_dict())
    raise
```

`request` и `weather` — словари из контракта. Результат неизменяемый, даты приведены к UTC;
входные словари не меняются. Ошибки не маскируются успешным результатом.
Проверяются горизонт, типы и значения, время публикации и полная почасовая сетка.

## История и модель

История организаторов уже включена в `data/raw/*.csv.gz`; SHA-256 исходных CSV проверяется
перед расчётом. Data D1 формирует только полные часы из шести измерений. Backend использует
историю до `as_of`, не позднее конца января 2026 в фиксированном GMT+5.

Для каждой турбины строится отдельная эмпирическая кривая: средняя мощность по интервалам
ветра шириной 1 м/с, затем линейная интерполяция. За границами кривой берётся ближайший узел
с предупреждением. Минимум — 24 полных часа и два скоростных интервала.
Температура сохраняется в результате, но базовая модель её не использует.

Роль исходной метки неизвестна. Параметр `timestamp_role` (`start` или `end`) обязателен;
`timestamp_role_confirmed: false` и предупреждения нужно показывать в UI. Результат —
нормализованная мощность отдельной турбины, без перевода в кВт/кВт·ч и суммирования станции.

## Запуск HTTP API

Из корня клона ветки backend:

```text
python -m backend.server
```

Сервер слушает `http://127.0.0.1:8000`. Для другого порта: `--port 8001`.
Проверка в PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
$body = '{"as_of":"2026-01-31T19:00:00Z","turbine_ids":["turbine_1","turbine_2"],"timestamp_role":"start"}'
Invoke-RestMethod http://127.0.0.1:8000/api/history/summary -Method Post -ContentType 'application/json' -Body $body
```

Пример `start` — явное допущение, не подтверждение организатора. Сводка содержит число
полных часов, кривые обеих турбин, контрольные суммы и предупреждения. Чтение всей истории
может занять несколько секунд. Сервер предназначен для локального демо.

### Погода и прогноз

Оператор помещает реальный архивный пакет B1 в отдельный локальный каталог, затем задаёт:

```powershell
$env:WEATHER_ARCHIVE_DIR = 'C:\wind-weather-archive'
python -m backend.server
```

Для `as_of=2026-01-31T19:00:00Z` имя файла — `20260131T190000Z.json`. Содержимое —
`WeatherBatch` из контракта, с доказательством времени доступности выпуска и нужными часами.
Из файла можно выбрать 24/48 часов и одну/две турбины. Каждый запрос перечитывает файл.
Некорректный JSON файла возвращает `WEATHER_UNAVAILABLE`, неверный пакет — ошибки B1.
Не кладите наблюдения или реанализ на место архивного прогноза погоды.

```powershell
$body = '{"as_of":"2026-01-31T19:00:00Z","horizon_hours":24,"turbine_ids":["turbine_1","turbine_2"],"timestamp_role":"start"}'
Invoke-RestMethod http://127.0.0.1:8000/api/forecasts -Method Post -ContentType 'application/json' -Body $body
```

Без файла/настройки ожидается HTTP 503 `WEATHER_UNAVAILABLE`. Можно явно передать `weather`
в теле запроса вместо каталога. Backend проверяет пакет, но не удостоверяет подлинность
указанного источника. Тестовые погодные пакеты находятся только в тестах и не включаются
в основной сценарий. Проверка двух вариантов реальной истории:

```text
python -m backend.manual_history --as-of 2026-01-31T19:00:00Z --timestamp-role start
python -m backend.manual_history --as-of 2026-01-15T19:00:00Z --timestamp-role end --turbine turbine_2
```

Для Linux/macOS переменные задаются через `export WEATHER_ARCHIVE_DIR=/path/to/archive`.
`UI_ORIGINS` задаёт CORS-адреса через запятую; по умолчанию localhost:3000 и 127.0.0.1:3000.
Сервер и CLI анализа читают `.env` из корня клона. Переменные окружения имеют приоритет;
после замены ключа перезапустите сервер. Файл `.env` исключён из Git.

## B4: реальный анализ OpenAI

В локальном `.env` задайте `OPENAI_API_KEY` (значение не публикуйте); необязательная
`OPENAI_MODEL` по умолчанию `gpt-4o-mini`. Используется официальный Responses API,
структурированный JSON-ответ и `store:false`. Один запрос — один вызов, тайм-аут 45 секунд;
автоматических повторных платных вызовов нет. Код использует стандартную библиотеку Python.

Пример пользователя сохранён в `backend/examples/open_meteo_user_sample.json` без ключа.
Команды из корня клона:

```text
python -m backend.analyze_weather backend/examples/open_meteo_user_sample.json --validate-only
python -m backend.analyze_weather backend/examples/open_meteo_user_sample.json
```

Первая команда только проверяет данные и вычисляет числовую сводку. Вторая реально отправляет
сводку в OpenAI и возвращает `analysis`: ID ответа, модель, объяснение, риски и рекомендацию.
При отсутствии ключа, отказе API или неверном ответе возвращается ошибка, CLI завершается с 2.
В `test.sh` платных вызовов нет: транспортные ошибки проверяются изолированно на тестовых
ответах; реальные успешные вызовы проверены отдельно, см. [отчёт B4](b4-verification.md).

После `python -m backend.server` в другом терминале PowerShell:

```powershell
$weather = Get-Content backend/examples/open_meteo_user_sample.json -Raw -Encoding utf8 | ConvertFrom-Json
$body = @{weather=$weather} | ConvertTo-Json -Depth 20 -Compress
Invoke-RestMethod http://127.0.0.1:8000/api/analysis/weather -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

`POST /api/agent/forecasts` принимает запрос B3 и выполняет расчёт, затем анализ OpenAI.
Возвращает `{forecast, analysis}`. Числа прогноза LLM не меняет; рекомендации остаются
пояснениями. Для повторного расчёта обновите архив и повторите запрос. Погодный пример
без `issued_at`/`available_at` не принимается как архив B1. Детали — в контрактах B4.

Реализация следует [Responses API](https://developers.openai.com/api/docs/guides/text) и
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## Граница готовности B4 (историческая запись)

B1 проверяет предоставленные метаданные, но не подтверждает их подлинность. Погодный адаптер
должен обосновать историческую доступность выбранного выпуска.
История, расчёт B2/API B3 и шаг анализа OpenAI B4 подключены. Автономный сбор архивной погоды,
полный агентный цикл, прогон февраля и подключение UI остаются следующими этапами. Февральских фактических
мощностей пока нет; качество модели не измерено. Высота исторического ветра и время
доступности телеметрии неизвестны. Тесты проверяют код и совместимость Data/Backend/HTTP,
но не доказывают точность прогноза или завершение агентного сценария.


## B5: полный запуск и февраль

Текущий запуск описан в [общем README](../README.md). Сервер по умолчанию читает `data/weather`;
`POST /api/agent/forecasts` сам проверяет provenance и при необходимости получает NOAA.
`refresh_weather: true` принудительно обновляет погоду. OpenAI может запросить ещё одну проверку;
при изменении входов выполняются пересчёт и повторный анализ, максимум два вызова OpenAI.
Windows ecCodes декодирует GRIB последовательно: параллельная инициализация приводила к завершению процесса.
Реальная повторная загрузка после исправления прошла.

Февраль: `python -m backend.batch_forecast --output-dir backend/runtime/new-february --timestamp-role end`.
Каталог должен быть новым. Готовые 28 запусков и CSV: `backend/reports/february`.
Проверка реального API через клиент UI: `node ui/smoke-api.mjs` при запущенном backend.
Результаты двух живых запросов находятся в `backend/reports/live-cycle.json`.

Data, backend и UI объединены. Февральская точность неизвестна без фактической мощности;
измеренные Data январские ошибки приведены в `data/reports/D2-handoff.md`. Docker-файлы подготовлены,
но Docker Engine отсутствует на этой машине, поэтому контейнерный запуск ещё не подтверждён.
