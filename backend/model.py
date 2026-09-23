"""Per-turbine descriptive wind curves; no weather or power is fabricated."""

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timezone
from math import floor, fsum

from backend.validation import ForecastValidationError, _number, _object, _timestamp

HISTORY_END = datetime(2026, 1, 31, 19, tzinfo=timezone.utc)
MODEL = {"name": "empirical-wind-curve-v1", "bin_width_ms": 1.0, "minimum_hours": 24}


def utc_text(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_curves(points, as_of, turbine_ids):
    """Reject invalid/leaking history before computing descriptive bin averages."""
    groups = {turbine: [] for turbine in turbine_ids}
    seen = set()
    for index, point in enumerate(points):
        path = f"history.points[{index}]"
        row = _object(point, ("turbine_id", "valid_at", "wind_speed_ms", "temperature_c",
                              "normalized_power", "sample_count"), path, "INVALID_HISTORY")
        turbine = row["turbine_id"]
        if not isinstance(turbine, str) or turbine not in groups:
            raise ForecastValidationError("INVALID_HISTORY", path, "Unexpected turbine.")
        stamp = _timestamp(row["valid_at"], path + ".valid_at", "INVALID_HISTORY", hourly=True)
        if stamp > min(as_of, HISTORY_END):
            raise ForecastValidationError("INVALID_HISTORY", path, "Historical hour exceeds the cutoff.")
        if (turbine, stamp) in seen:
            raise ForecastValidationError("INVALID_HISTORY", path, "Duplicate historical hour.")
        seen.add((turbine, stamp))
        if type(row["sample_count"]) is not int or row["sample_count"] != 6:
            raise ForecastValidationError("INVALID_HISTORY", path, "A full hour must have six samples.")
        try:
            speed = _number(row["wind_speed_ms"], path + ".wind_speed_ms")
            power = _number(row["normalized_power"], path + ".normalized_power")
            _number(row["temperature_c"], path + ".temperature_c")
        except ForecastValidationError as exc:
            raise ForecastValidationError("INVALID_HISTORY", exc.field, exc.message) from exc
        if speed < 0 or not 0 <= power <= 1:
            raise ForecastValidationError("INVALID_HISTORY", path, "Wind must be nonnegative and power in [0,1].")
        groups[turbine].append((stamp, speed, power))

    result = []
    for turbine, rows in groups.items():
        bins = defaultdict(list)
        for _, speed, power in rows:
            bins[floor(speed)].append((speed, power))
        if len(rows) < MODEL["minimum_hours"] or len(bins) < 2:
            raise ForecastValidationError("INSUFFICIENT_HISTORY", f"history.{turbine}",
                                          "Need at least 24 complete hours and two wind bins.")
        knots = [{"wind_speed_ms": fsum(x / len(values) for x, _ in values),
                  "normalized_power": fsum(y / len(values) for _, y in values),
                  "sample_count": len(values)} for _, values in sorted(bins.items())]
        result.append({"turbine_id": turbine, "hours": len(rows),
                       "first_hour": utc_text(min(r[0] for r in rows)),
                       "last_hour": utc_text(max(r[0] for r in rows)),
                       "mean_normalized_power": fsum(r[2] / len(rows) for r in rows),
                       "wind_min_ms": min(r[1] for r in rows),
                       "wind_max_ms": max(r[1] for r in rows), "curve_knots": knots})
    return result


def interpolate(knots, speed):
    """Return (power, outside_support) for a validated curve and wind speed."""
    index = bisect_left([k["wind_speed_ms"] for k in knots], speed)
    if index == 0:
        return knots[0]["normalized_power"], speed < knots[0]["wind_speed_ms"]
    if index == len(knots):
        return knots[-1]["normalized_power"], True
    left, right = knots[index - 1], knots[index]
    weight = (speed - left["wind_speed_ms"]) / (right["wind_speed_ms"] - left["wind_speed_ms"])
    return (1 - weight) * left["normalized_power"] + weight * right["normalized_power"], False
