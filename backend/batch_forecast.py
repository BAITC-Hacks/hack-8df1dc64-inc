"""Reproduce February forecasts from verified NOAA packages, without paid LLM calls."""

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.model import utc_text
from backend.service import ForecastService
from backend.validation import validate_forecast_request
from backend.weather_provider import WeatherProvider, select_weather


def run_batch(output_dir, timestamp_role, provider=None):
    if timestamp_role not in ("start", "end"):
        raise ValueError("Explicit timestamp_role required")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "runs").mkdir()
    provider = provider if provider is not None else WeatherProvider()
    service = ForecastService()
    start = datetime(2026, 1, 31, 19, tzinfo=timezone.utc)
    rows = []
    for day in range(28):
        cutoff = start + timedelta(days=day)
        base = {"as_of": utc_text(cutoff), "horizon_hours": 48, "turbine_ids": ["turbine_1", "turbine_2"]}
        request = validate_forecast_request(base)
        weather, _ = provider.resolve(request)
        result = service.forecast({**base, "timestamp_role": timestamp_role, "weather": select_weather(weather, request)})
        (output_dir / "runs" / cutoff.strftime("%Y%m%dT%H%M%SZ.json")).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        for point in result["points"]:
            if point["valid_at"] <= utc_text(cutoff + timedelta(hours=24)):
                rows.append({"as_of": base["as_of"], **{k: v for k, v in point.items()}})
        print(json.dumps({"completed": day + 1, "total": 28, "as_of": base["as_of"]}), flush=True)
    keys = {(r["turbine_id"], r["valid_at"]) for r in rows}
    expected = {(t, utc_text(start + timedelta(hours=h))) for t in ("turbine_1", "turbine_2") for h in range(1, 673)}
    if keys != expected or len(rows) != 1344:
        raise ValueError("Incomplete or duplicate February coverage")
    with (output_dir / "february.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["as_of", "turbine_id", "valid_at", "normalized_power", "wind_speed_ms", "temperature_c", "extrapolated"])
        writer.writeheader()
        writer.writerows(rows)
    summary = {"runs": 28, "forecast_points": 2688, "csv_rows": len(rows), "unique_hours_per_turbine": 672,
               "first_hour": min(r["valid_at"] for r in rows), "last_hour": max(r["valid_at"] for r in rows),
               "timestamp_role": timestamp_role, "timestamp_role_confirmed": False,
               "limitations": ["February actual power is unavailable; no February accuracy claim.",
                               "Normalized power, not kW/kWh.", "No OpenAI calls in batch mode."]}
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timestamp-role", choices=("start", "end"), required=True)
    args = parser.parse_args()
    print(json.dumps(run_batch(args.output_dir, args.timestamp_role)))


if __name__ == "__main__":
    main()
