"""Run one inspectable B1 scenario with explicitly artificial weather inputs."""

import argparse
from datetime import datetime, timedelta, timezone
import json

from backend.validation import ForecastValidationError, validate_forecast_inputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--late-weather", action="store_true", help="Publish weather after cutoff; expect exit 2.")
    args = parser.parse_args()
    cutoff = datetime(2026, 2, 10, tzinfo=timezone.utc)
    request = {"as_of": cutoff.isoformat(), "horizon_hours": 48, "turbine_ids": ["turbine_1", "turbine_2"]}
    availability = cutoff + timedelta(minutes=1) if args.late_weather else cutoff - timedelta(hours=1)
    weather = {
        "source": "synthetic-manual-check",
        "run_id": "manual-scenario-1",
        "issued_at": (cutoff - timedelta(hours=6)).isoformat(),
        "available_at": availability.isoformat(),
        "availability_evidence": "Artificial data to exercise validation, not a real archive.",
        "wind_height_m": 100,
        "points": [
            {
                "turbine_id": turbine,
                "valid_at": (cutoff + timedelta(hours=hour)).isoformat(),
                "wind_speed_ms": 5 + hour / 20,
                "temperature_c": -4 + hour / 10,
            }
            for turbine in request["turbine_ids"]
            for hour in range(1, 49)
        ],
    }
    try:
        result = validate_forecast_inputs(request, weather)
    except ForecastValidationError as exc:
        print(json.dumps(exc.as_dict(), indent=2))
        return 2
    print(json.dumps({
        "status": "input_validated",
        "source": result.weather.source,
        "point_count": len(result.weather.points),
        "first_hour": result.weather.points[0].valid_at.isoformat(),
        "last_hour": result.weather.points[-1].valid_at.isoformat(),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
