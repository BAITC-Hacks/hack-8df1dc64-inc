"""Inspect real organizer history under an explicitly chosen timestamp assumption."""

import argparse
import json
from backend.service import ForecastService
from backend.validation import ForecastValidationError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--timestamp-role", choices=("start", "end"), required=True)
    parser.add_argument("--turbine", choices=("turbine_1", "turbine_2"), action="append")
    args = parser.parse_args()
    try:
        result = ForecastService().history_summary({"as_of": args.as_of,
                    "timestamp_role": args.timestamp_role,
                    "turbine_ids": args.turbine or ["turbine_1", "turbine_2"]})
    except ForecastValidationError as exc:
        print(json.dumps({"error": exc.as_dict()}))
        return 2
    compact = {**result, "turbines": [{**{k: v for k, v in t.items() if k != "curve_knots"},
                                      "curve_knot_count": len(t["curve_knots"])} for t in result["turbines"]]}
    print(json.dumps(compact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
