"""Validate the user's Open-Meteo format and compute facts before LLM analysis."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import fsum
import re

from backend.model import utc_text
from backend.validation import ForecastValidationError, _number, _object, _text

VARIABLES = ("temperature_2m", "wind_speed_80m", "wind_speed_100m", "wind_speed_120m")


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def stats(values):
    return {"min": min(values), "max": max(values), "mean": fsum(v / len(values) for v in values)}


def summarize_weather(payload):
    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], list):
        payload = payload[0]
    if not isinstance(payload, list) or len(payload) != 2:
        raise ForecastValidationError("INVALID_WEATHER", "weather", "Expected two locations in turbine order.")
    locations, previous_times = [], None
    for index, raw in enumerate(payload):
        path = f"weather[{index}]"
        fields = ("latitude", "longitude", "generationtime_ms", "utc_offset_seconds", "timezone",
                  "timezone_abbreviation", "elevation", "hourly_units", "hourly")
        if isinstance(raw, dict) and "location_id" in raw:
            fields += ("location_id",)
        row = _object(raw, fields, path, "INVALID_WEATHER")
        if "location_id" in row and (type(row["location_id"]) is not int or row["location_id"] != index):
            raise ForecastValidationError("INVALID_WEATHER", path + ".location_id", "Location order must match turbine_1, turbine_2.")
        lat = _number(row["latitude"], path + ".latitude")
        lon = _number(row["longitude"], path + ".longitude")
        generation = _number(row["generationtime_ms"], path + ".generationtime_ms")
        _number(row["elevation"], path + ".elevation")
        for name in ("timezone", "timezone_abbreviation"):
            _text(row[name], path + "." + name, "INVALID_WEATHER")
        offset = row["utc_offset_seconds"]
        if not -90 <= lat <= 90 or not -180 <= lon <= 180 or generation < 0:
            raise ForecastValidationError("INVALID_WEATHER", path, "Invalid coordinates or generation time.")
        if type(offset) is not int or abs(offset) > 14 * 3600:
            raise ForecastValidationError("INVALID_WEATHER", path + ".utc_offset_seconds", "Invalid UTC offset.")
        units = _object(row["hourly_units"], ("time",) + VARIABLES, path + ".hourly_units", "INVALID_WEATHER")
        expected_units = {"time": "iso8601", "temperature_2m": "°C", **{v: "m/s" for v in VARIABLES[1:]}}
        if units != expected_units:
            raise ForecastValidationError("INVALID_WEATHER", path + ".hourly_units", "Expected ISO time, Celsius and m/s.")
        hourly = _object(row["hourly"], ("time",) + VARIABLES, path + ".hourly", "INVALID_WEATHER")
        times = hourly["time"]
        if not isinstance(times, list) or not 24 <= len(times) <= 48:
            raise ForecastValidationError("INVALID_WEATHER", path + ".hourly.time", "Expected 24 to 48 hourly timestamps.")
        parsed = []
        for stamp in times:
            try:
                if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:00", stamp):
                    raise ValueError()
                parsed.append(datetime.fromisoformat(stamp).replace(tzinfo=timezone(timedelta(seconds=offset))).astimezone(timezone.utc))
            except (ValueError, OverflowError) as exc:
                raise ForecastValidationError("INVALID_WEATHER", path + ".hourly.time", "Expected valid local whole hours.") from exc
        if any(b - a != timedelta(hours=1) for a, b in zip(parsed, parsed[1:])) or (previous_times is not None and previous_times != parsed):
            raise ForecastValidationError("INVALID_WEATHER", path + ".hourly.time", "Hours must be consecutive and identical for both turbines.")
        previous_times = parsed
        series = {}
        for variable in VARIABLES:
            values = hourly[variable]
            if not isinstance(values, list) or len(values) != len(times):
                raise ForecastValidationError("INVALID_WEATHER", path + ".hourly." + variable, "Hourly array length mismatch.")
            series[variable] = [_number(v, path + ".hourly." + variable) for v in values]
            if variable.startswith("wind") and min(series[variable]) < 0:
                raise ForecastValidationError("INVALID_WEATHER", path + ".hourly." + variable, "Negative wind speed.")
        locations.append({"turbine_id": f"turbine_{index + 1}", "latitude": lat, "longitude": lon,
                          "hours": len(parsed), "first_time": utc_text(parsed[0]), "last_time": utc_text(parsed[-1]),
                          "temperature_c": stats(series["temperature_2m"]),
                          "wind_by_height": {str(h): stats(series[f"wind_speed_{h}m"]) for h in (80, 100, 120)},
                          "max_hourly_wind_change_ms": max(abs(b - a) for v in VARIABLES[1:] for a, b in zip(series[v], series[v][1:]))})
    warnings = [{"code": "WEATHER_PROVENANCE_UNVERIFIED", "message": "No issue/publication times: this weather input cannot establish historical forecast availability."}]
    if (locations[0]["latitude"], locations[0]["longitude"]) == (locations[1]["latitude"], locations[1]["longitude"]):
        warnings.append({"code": "SHARED_WEATHER_GRID", "message": "Both turbines use the same returned weather grid coordinates."})
    return {"kind": "open_meteo_weather", "input_sha256": fingerprint(payload),
            "historical_availability_verified": False, "locations": locations, "warnings": warnings}
