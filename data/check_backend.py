"""Exercise the real B3 HTTP API with bundled NOAA weather and January holdouts.

Run after data/backend branches have been combined. Does not implement a model.
"""
import argparse
import json
import math
import threading
import urllib.request
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path

from data.history import RAW, hourly_history, iter_observations, verify_sources


def run_checks(weather_dir):
    from backend.server import create_server
    from backend.service import ForecastService

    manifest = verify_sources()
    observations = chain.from_iterable(iter_observations(RAW / f["path"], f["turbine_id"])
                                      for f in manifest["files"])
    # These targets are used only for scoring, never passed to ForecastService.
    targets = hourly_history(observations, datetime(2026, 1, 31, 19, tzinfo=timezone.utc), "end")["points"]
    actual = {(p["turbine_id"], p["valid_at"]): p["normalized_power"] for p in targets}
    cases = [("2026-01-15T19:00:00Z", 24, ["turbine_1"]),
             ("2026-01-23T19:00:00Z", 48, ["turbine_1", "turbine_2"]),
             ("2026-01-31T19:00:00Z", 48, ["turbine_1", "turbine_2"])]
    report = {"timestamp_role": "end", "timestamp_role_confirmed": False,
              "evaluation_scope": "Two January holdout windows only; February actuals unavailable",
              "power_units": "organizer normalized power, not kW", "cases": []}
    server = create_server(("127.0.0.1", 0), service=ForecastService(weather_archive_dir=weather_dir))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for as_of, horizon, turbines in cases:
            body = {"as_of": as_of, "horizon_hours": horizon, "turbine_ids": turbines, "timestamp_role": "end"}
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/forecasts",
                                             data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.load(response)
                if response.status != 200:
                    raise ValueError("Backend did not return HTTP 200")
            if len(result["points"]) != len(turbines) * horizon or result["timestamp_role_confirmed"] is not False:
                raise ValueError("Backend result grid/assumption mismatch")
            if result["weather"]["input_mode"] != "archive_file" or result["weather"]["available_at"] > as_of:
                raise ValueError("Backend did not use the historically available archive")
            if any(t["last_hour"] > as_of for t in result["history"]["turbines"]):
                raise ValueError("Backend used future training history")
            metrics = []
            for turbine in turbines:
                predictions = [p for p in result["points"] if p["turbine_id"] == turbine]
                matched = [(p["normalized_power"], actual[(turbine, p["valid_at"])])
                           for p in predictions if (turbine, p["valid_at"]) in actual]
                item = {"turbine_id": turbine, "forecast_hours": len(predictions), "scored_hours": len(matched)}
                if matched:
                    n = len(matched)
                    baseline = next(t["mean_normalized_power"] for t in result["history"]["turbines"] if t["turbine_id"] == turbine)
                    item.update({"mae": math.fsum(abs(a - b) for a, b in matched) / n,
                                 "rmse": math.sqrt(math.fsum((a - b) ** 2 for a, b in matched) / n),
                                 "training_mean_baseline_mae": math.fsum(abs(baseline - b) for _, b in matched) / n})
                metrics.append(item)
            report["cases"].append({"request": body, "http_status": 200, "points": len(result["points"]),
                                     "weather": result["weather"], "metrics": metrics})
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weather-dir", type=Path, default=Path(__file__).parent / "weather")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_checks(args.weather_dir.resolve())
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "http_cases": len(report["cases"])}))


if __name__ == "__main__":
    main()
