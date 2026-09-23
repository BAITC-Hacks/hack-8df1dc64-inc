# Пример Open-Meteo от пользователя

`open_meteo_user_sample.json` сохраняет переданные пользователем числовые значения и
вложенную оболочку массива. Это эталон входа **для анализа**, не готовый WeatherBatch B1.
Общий источник двух турбин, 48 часовых меток за 25–26 января 2026, GMT+5.

Пользователь указал запрос:

https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=43.645150,43.643198&longitude=78.535604,78.538828&start_date=2026-01-25&end_date=2026-01-26&hourly=temperature_2m,wind_speed_80m,wind_speed_100m,wind_speed_120m&wind_speed_unit=ms&timezone=Asia%2FAlmaty

В ответе обе точки имеют одни координаты погодной сетки. Backend сохраняет обе турбины
и выдаёт предупреждение, не удаляя вторую как дубликат. `generationtime_ms` не используется
как время выпуска. Публикация и выпуск в этом ответе не указаны.

[Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api)
объединяет первые часы разных выпусков. Для полного горизонта конкретного выпуска
документация направляет к [Single Runs API](https://open-meteo.com/en/docs/single-runs-api).
Поэтому этот пример не доказывает, что все его часы были доступны при одном запуске в прошлом.
