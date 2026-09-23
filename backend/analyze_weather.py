"""Validate an Open-Meteo JSON file and optionally make one real OpenAI call."""

import argparse
import json
from pathlib import Path

from backend.config import load_env
from backend.openai_analysis import AnalysisError, OpenAIAnalyzer
from backend.service import strict_json
from backend.validation import ForecastValidationError
from backend.weather_analysis import summarize_weather


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--validate-only", action="store_true", help="Compute input facts only; no LLM call or analysis success claimed")
    args = parser.parse_args()
    try:
        load_env()
        summary = summarize_weather(strict_json(args.path.read_text(encoding="utf-8")))
        result = {"input_summary": summary}
        if not args.validate_only:
            result["analysis"] = OpenAIAnalyzer().analyze(summary)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (AnalysisError, ForecastValidationError) as exc:
        print(json.dumps({"error": exc.as_dict()}))
        return 2
    except (OSError, ValueError, RecursionError):
        print(json.dumps({"error": {"code": "INPUT_UNAVAILABLE", "field": "file", "message": "Cannot read input JSON or local configuration."}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
