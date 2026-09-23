"""Validate forecast inputs against docs/api-contract.md and docs/db-schema.md."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re


_TURBINES = frozenset({"turbine_1", "turbine_2"})
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


class ForecastValidationError(ValueError):
    """A rejected input, with a stable code and path for callers."""

    def __init__(self, code: str, field: str, message: str) -> None:
        self.code = code
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True, slots=True)
class ForecastRequest:
    as_of: datetime
    horizon_hours: int
    turbine_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WeatherPoint:
    turbine_id: str
    valid_at: datetime
    wind_speed_ms: float
    temperature_c: float


@dataclass(frozen=True, slots=True)
class WeatherBatch:
    source: str
    run_id: str
    issued_at: datetime
    available_at: datetime
    availability_evidence: str
    wind_height_m: float
    points: tuple[WeatherPoint, ...]


@dataclass(frozen=True, slots=True)
class ValidatedForecastInput:
    request: ForecastRequest
    weather: WeatherBatch


def _object(value: object, fields: tuple[str, ...], path: str, code: str) -> dict:
    if not isinstance(value, dict):
        raise ForecastValidationError(code, path, "Expected an object.")
    for name in fields:
        if name not in value:
            raise ForecastValidationError(code, f"{path}.{name}", "Required field is missing.")
    if any(name not in fields for name in value):
        raise ForecastValidationError(code, path, "Unknown fields are not allowed.")
    return value


def _text(value: object, path: str, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ForecastValidationError(code, path, "Expected a nonempty string.")
    return value


def _timestamp(value: object, path: str, code: str, *, hourly: bool = False) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ForecastValidationError(
            code, path, "Expected an ISO 8601 timestamp with seconds and a timezone."
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ForecastValidationError(code, path, "Invalid or out-of-range timestamp.") from exc
    if hourly and (parsed.minute or parsed.second or parsed.microsecond):
        raise ForecastValidationError(code, path, "Expected a whole hour in UTC.")
    return parsed


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForecastValidationError("INVALID_WEATHER", path, "Expected a finite number.")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ForecastValidationError("INVALID_WEATHER", path, "Number is out of range.") from exc
    if not math.isfinite(result):
        raise ForecastValidationError("INVALID_WEATHER", path, "Expected a finite number.")
    return result


def validate_forecast_request(value: object) -> ForecastRequest:
    data = _object(value, ("as_of", "horizon_hours", "turbine_ids"), "request", "INVALID_REQUEST")
    as_of = _timestamp(data["as_of"], "request.as_of", "INVALID_REQUEST", hourly=True)
    horizon = data["horizon_hours"]
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 24 <= horizon <= 48:
        raise ForecastValidationError(
            "INVALID_REQUEST", "request.horizon_hours", "Expected an integer from 24 to 48."
        )
    try:
        as_of + timedelta(hours=horizon)
    except OverflowError as exc:
        raise ForecastValidationError(
            "INVALID_REQUEST", "request.as_of", "Forecast horizon exceeds the supported date range."
        ) from exc
    turbines = data["turbine_ids"]
    if not isinstance(turbines, list) or not turbines:
        raise ForecastValidationError(
            "INVALID_REQUEST", "request.turbine_ids", "Expected a nonempty array of turbine IDs."
        )
    seen: set[str] = set()
    for index, turbine in enumerate(turbines):
        path = f"request.turbine_ids[{index}]"
        if not isinstance(turbine, str) or turbine not in _TURBINES:
            raise ForecastValidationError("INVALID_REQUEST", path, "Unknown turbine ID.")
        if turbine in seen:
            raise ForecastValidationError("INVALID_REQUEST", path, "Duplicate turbine ID.")
        seen.add(turbine)
    return ForecastRequest(as_of, horizon, tuple(turbines))


def _weather(value: object, request: ForecastRequest) -> WeatherBatch:
    data = _object(
        value,
        ("source", "run_id", "issued_at", "available_at", "availability_evidence", "wind_height_m", "points"),
        "weather",
        "INVALID_WEATHER",
    )
    source = _text(data["source"], "weather.source", "INVALID_WEATHER")
    run_id = _text(data["run_id"], "weather.run_id", "INVALID_WEATHER")
    issued = _timestamp(data["issued_at"], "weather.issued_at", "INVALID_WEATHER")
    available = _timestamp(data["available_at"], "weather.available_at", "INVALID_WEATHER")
    evidence = _text(data["availability_evidence"], "weather.availability_evidence", "INVALID_WEATHER")
    height = _number(data["wind_height_m"], "weather.wind_height_m")
    if height <= 0:
        raise ForecastValidationError("INVALID_WEATHER", "weather.wind_height_m", "Height must be positive.")
    if issued > available:
        raise ForecastValidationError(
            "INVALID_WEATHER", "weather.available_at", "Publication cannot precede the model issue time."
        )
    if available > request.as_of:
        raise ForecastValidationError(
            "WEATHER_NOT_AVAILABLE", "weather.available_at", "Weather was published after the forecast cutoff."
        )
    if not isinstance(data["points"], list):
        raise ForecastValidationError("INVALID_WEATHER", "weather.points", "Expected an array of weather points.")
    expected = {
        (turbine, request.as_of + timedelta(hours=hour))
        for turbine in request.turbine_ids
        for hour in range(1, request.horizon_hours + 1)
    }
    seen: set[tuple[str, datetime]] = set()
    points: list[WeatherPoint] = []
    for index, raw in enumerate(data["points"]):
        path = f"weather.points[{index}]"
        row = _object(raw, ("turbine_id", "valid_at", "wind_speed_ms", "temperature_c"), path, "INVALID_WEATHER")
        turbine = _text(row["turbine_id"], f"{path}.turbine_id", "INVALID_WEATHER")
        valid_at = _timestamp(row["valid_at"], f"{path}.valid_at", "INVALID_WEATHER", hourly=True)
        speed = _number(row["wind_speed_ms"], f"{path}.wind_speed_ms")
        temperature = _number(row["temperature_c"], f"{path}.temperature_c")
        if speed < 0:
            raise ForecastValidationError("INVALID_WEATHER", f"{path}.wind_speed_ms", "Wind speed cannot be negative.")
        key = turbine, valid_at
        if key not in expected:
            raise ForecastValidationError(
                "INCOMPLETE_WEATHER_GRID", path, "Point is outside the requested turbine/hour grid."
            )
        if key in seen:
            raise ForecastValidationError("INCOMPLETE_WEATHER_GRID", path, "Duplicate turbine/hour point.")
        seen.add(key)
        points.append(WeatherPoint(turbine, valid_at, speed, temperature))
    missing = expected - seen
    if missing:
        turbine, hour = min(missing)
        raise ForecastValidationError(
            "INCOMPLETE_WEATHER_GRID",
            "weather.points",
            f"Missing {len(missing)} point(s); first: {turbine} at {hour.isoformat()}.",
        )
    turbine_order = {turbine: index for index, turbine in enumerate(request.turbine_ids)}
    points.sort(key=lambda point: (turbine_order[point.turbine_id], point.valid_at))
    return WeatherBatch(source, run_id, issued, available, evidence, height, tuple(points))


def validate_forecast_inputs(request: object, weather: object) -> ValidatedForecastInput:
    """Return immutable UTC inputs, or raise ForecastValidationError without mutating inputs.

    This validates supplied availability metadata. Authenticating a historical weather
    archive remains the responsibility of the source adapter.
    """
    validated_request = validate_forecast_request(request)
    return ValidatedForecastInput(validated_request, _weather(weather, validated_request))
