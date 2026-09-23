"""Connect the published Data adapter, validated weather and empirical model."""

import csv
import json
from copy import deepcopy
from datetime import timedelta
from itertools import chain
from pathlib import Path
from threading import RLock

from data.history import RAW, hourly_history, iter_observations, verify_sources
from backend.model import MODEL, HISTORY_END, build_curves, interpolate, utc_text
from backend.temperature_correction import reference_temperatures, temperature_corrected_power
from backend.validation import (ForecastValidationError, _object, _timestamp,
                                validate_forecast_inputs, validate_forecast_request)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError("Non-finite JSON number: " + value)

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def warnings():
    return [
        {"code": "TIMESTAMP_ROLE_UNCONFIRMED", "message": "Start/end timestamp role is an explicit assumption, not confirmed by the organizer."},
        {"code": "WIND_HEIGHT_UNKNOWN", "message": "Historical wind measurement height is unknown; no height correction is applied."},
        {"code": "TELEMETRY_AVAILABILITY_UNCONFIRMED", "message": "Observation publication times are unknown; interval end is used as the history cutoff."},
    ]


def parse_request(payload, forecast):
    fields = ("as_of", "turbine_ids", "timestamp_role")
    if forecast:
        fields += ("horizon_hours",)
        if isinstance(payload, dict) and "weather" in payload:
            fields += ("weather",)
        if isinstance(payload, dict) and "apply_temperature_correction" in payload:
            fields += ("apply_temperature_correction",)
    body = _object(payload, fields, "request", "INVALID_REQUEST")
    if forecast and type(body.get("apply_temperature_correction", False)) is not bool:
        raise ForecastValidationError("INVALID_REQUEST", "request.apply_temperature_correction", "Expected a boolean.")
    role = body["timestamp_role"]
    if role not in ("start", "end"):
        raise ForecastValidationError("INVALID_REQUEST", "request.timestamp_role", "Explicitly choose start or end.")
    base = {"as_of": body["as_of"], "turbine_ids": body["turbine_ids"],
            "horizon_hours": body["horizon_hours"] if forecast else 24}
    return validate_forecast_request(base), role, base


class ForecastService:
    def __init__(self, raw_dir=RAW, weather_archive_dir=None):
        self.raw_dir = Path(raw_dir)
        self.weather_archive_dir = Path(weather_archive_dir) if weather_archive_dir else None
        self._history_cache = {}
        self._history_lock = RLock()

    def _history(self, request, role, apply_temperature_correction=False):
        with self._history_lock:
            return self._history_locked(request, role, apply_temperature_correction)

    def _history_locked(self, request, role, apply_temperature_correction=False):
        try:
            manifest = verify_sources(self.raw_dir)
            selected = [f for f in manifest["files"] if f["turbine_id"] in request.turbine_ids]
            key = (min(request.as_of, HISTORY_END), role, request.turbine_ids,
                   tuple((f["turbine_id"], f["sha256_uncompressed"]) for f in selected),
                   apply_temperature_correction)
            if key in self._history_cache:
                cached = deepcopy(self._history_cache[key])
                cached["as_of"] = utc_text(request.as_of)
                return cached
            rows = chain.from_iterable(iter_observations(self.raw_dir / f["path"], f["turbine_id"])
                                       for f in selected)
            history = hourly_history(rows, request.as_of, role)
        except OSError as exc:
            raise ForecastValidationError("HISTORY_UNAVAILABLE", "history", "Historical source cannot be read.") from exc
        except (ValueError, KeyError, TypeError, EOFError, csv.Error) as exc:
            code = "INSUFFICIENT_HISTORY" if str(exc).startswith("No complete historical hours") else "INVALID_HISTORY"
            raise ForecastValidationError(code, "history", str(exc)) from exc
        result = {"as_of": utc_text(request.as_of), "timestamp_role": role,
                "timestamp_role_confirmed": False, "source_timezone": "UTC+05:00",
                "source_hashes": {f["turbine_id"]: f["sha256_uncompressed"] for f in selected},
                **{key: value for key, value in history.items() if key != "points"},
                "model": dict(MODEL), "turbines": build_curves(history["points"], request.as_of, request.turbine_ids),
                "warnings": warnings()}
        if apply_temperature_correction:
            try:
                references = reference_temperatures(history["points"])
            except ValueError as exc:
                raise ForecastValidationError("INVALID_HISTORY", "history.temperature_c", str(exc)) from exc
            result["model"]["temperature_correction"] = {"reference_temperature_c": references}
        if len(self._history_cache) >= 16:
            self._history_cache.pop(next(iter(self._history_cache)))
        self._history_cache[key] = deepcopy(result)
        return result

    def history_summary(self, payload):
        request, role, _ = parse_request(payload, False)
        return self._history(request, role)

    def _archive(self, request):
        if self.weather_archive_dir is None:
            raise ForecastValidationError("WEATHER_UNAVAILABLE", "weather", "WEATHER_ARCHIVE_DIR is not configured.")
        path = self.weather_archive_dir / request.as_of.strftime("%Y%m%dT%H%M%SZ.json")
        try:
            weather = strict_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError) as exc:
            raise ForecastValidationError("WEATHER_UNAVAILABLE", "weather", "Archived weather file is missing, unreadable or invalid JSON.") from exc
        if not isinstance(weather, dict) or not isinstance(weather.get("points"), list):
            raise ForecastValidationError("INVALID_WEATHER", "weather", "Archive must contain a WeatherBatch with points.")
        selected = []
        for row in weather["points"]:
            row = _object(row, ("turbine_id", "valid_at", "wind_speed_ms", "temperature_c"),
                          "weather.points", "INVALID_WEATHER")
            stamp = _timestamp(row["valid_at"], "weather.points.valid_at", "INVALID_WEATHER", hourly=True)
            if row["turbine_id"] in request.turbine_ids and request.as_of < stamp <= request.as_of + timedelta(hours=request.horizon_hours):
                selected.append(row)
        return {**weather, "points": selected}

    def forecast(self, payload):
        request, role, base = parse_request(payload, True)
        provided = "weather" in payload
        weather = payload["weather"] if provided else self._archive(request)
        validated = validate_forecast_inputs(base, weather)
        apply_correction = payload.get("apply_temperature_correction", False)
        history = self._history(request, role, apply_correction)
        curves = {t["turbine_id"]: t["curve_knots"] for t in history["turbines"]}
        points = []
        for index, point in enumerate(validated.weather.points):
            power, extrapolated = interpolate(curves[point.turbine_id], point.wind_speed_ms)
            if apply_correction:
                reference = history["model"]["temperature_correction"]["reference_temperature_c"][point.turbine_id]
                try:
                    power = temperature_corrected_power(power, point.temperature_c, reference)
                except ValueError as exc:
                    raise ForecastValidationError("INVALID_WEATHER", f"weather.points[{index}].temperature_c", str(exc)) from exc
            points.append({"turbine_id": point.turbine_id, "valid_at": utc_text(point.valid_at),
                           "normalized_power": power, "wind_speed_ms": point.wind_speed_ms,
                           "temperature_c": point.temperature_c, "extrapolated": extrapolated})
        notices = warnings()
        if any(p["extrapolated"] for p in points):
            notices.append({"code": "WIND_OUTSIDE_CURVE", "message": "Some winds are outside the curve support; nearest endpoint power is used."})
        batch = validated.weather
        return {"as_of": utc_text(request.as_of), "horizon_hours": request.horizon_hours,
                "turbine_ids": list(request.turbine_ids), "timestamp_role": role,
                "timestamp_role_confirmed": False, "history": history,
                "weather": {"source": batch.source, "run_id": batch.run_id,
                            "issued_at": utc_text(batch.issued_at), "available_at": utc_text(batch.available_at),
                            "availability_evidence": batch.availability_evidence, "wind_height_m": batch.wind_height_m,
                            "input_mode": "provided" if provided else "archive_file"},
                "points": points, "warnings": notices}
