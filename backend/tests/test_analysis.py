"""B4 validation and provider boundary tests. Recorded replies below are TEST ONLY.

The production path always sends an HTTPS request; these tests replace only network
I/O to deterministically exercise failures without charges or real credentials.
"""

from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from backend.agent import AgentService
from backend.config import load_env
from backend.openai_analysis import AnalysisError, OpenAIAnalyzer, decode_analysis
from backend.service import ForecastService
from backend.tests.test_service import request, weather, write_history
from backend.validation import ForecastValidationError
from backend.weather_analysis import summarize_weather

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "open_meteo_user_sample.json"


def provider_reply():
    output = {"summary": "TEST ONLY analysis", "risks": ["TEST ONLY risk"],
              "recommended_action": "review_inputs", "reason": "TEST ONLY reason"}
    return {"id": "test-response", "model": "test-model", "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(output)}]}]}


def sample():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


class WeatherAnalysisTests(unittest.TestCase):
    def test_user_sample_exact_facts_and_wrappers(self):
        original = sample()
        before = deepcopy(original)
        result = summarize_weather(original)
        self.assertEqual(original, before)
        self.assertEqual(result, summarize_weather(original[0]))
        self.assertFalse(result["historical_availability_verified"])
        self.assertEqual({w["code"] for w in result["warnings"]}, {"WEATHER_PROVENANCE_UNVERIFIED", "SHARED_WEATHER_GRID"})
        for loc in result["locations"]:
            self.assertEqual(loc["hours"], 48)
            self.assertEqual(loc["first_time"], "2026-01-24T19:00:00Z")
            self.assertEqual(loc["last_time"], "2026-01-26T18:00:00Z")
            self.assertEqual(loc["wind_by_height"]["100"]["max"], 8.16)
            self.assertAlmostEqual(loc["wind_by_height"]["100"]["mean"], 5.04875)
            self.assertAlmostEqual(loc["max_hourly_wind_change_ms"], 2.66)

    def test_24_hours_distinct_coordinates(self):
        body = sample()[0]
        for location in body:
            location["hourly"] = {key: values[:24] for key, values in location["hourly"].items()}
        body[1]["longitude"] = 78.5
        result = summarize_weather(body)
        self.assertEqual(result["locations"][0]["hours"], 24)
        self.assertEqual(len(result["warnings"]), 1)

    def test_invalid_values_units_and_metadata(self):
        for field, value in (("latitude", 91), ("longitude", -181), ("elevation", float("inf")),
                             ("utc_offset_seconds", True), ("utc_offset_seconds", 15*3600),
                             ("generationtime_ms", -1), ("location_id", 1)):
            with self.subTest(field=field):
                body = sample()[0]
                body[0][field] = value
                with self.assertRaises(ForecastValidationError):
                    summarize_weather(body)
        for value in (None, -1, float("nan"), "3", True):
            body = sample()[0]
            body[0]["hourly"]["wind_speed_100m"][0] = value
            with self.assertRaises(ForecastValidationError):
                summarize_weather(body)
        body = sample()[0]
        body[0]["hourly_units"]["wind_speed_100m"] = "km/h"
        with self.assertRaises(ForecastValidationError):
            summarize_weather(body)

    def test_missing_duplicate_unsorted_or_mismatched_times(self):
        for change in ("duplicate", "gap", "short-array", "different-grid", "bad-date"):
            body = sample()[0]
            if change == "duplicate":
                body[0]["hourly"]["time"][1] = body[0]["hourly"]["time"][0]
            elif change == "gap":
                for key in body[0]["hourly"]:
                    del body[0]["hourly"][key][1]
            elif change == "short-array":
                body[0]["hourly"]["temperature_2m"].pop()
            elif change == "different-grid":
                body[1]["utc_offset_seconds"] = 0
            else:
                body[0]["hourly"]["time"][0] = "2026-02-30T00:00"
            with self.subTest(change=change), self.assertRaises(ForecastValidationError):
                summarize_weather(body)


class OpenAIAnalysisTests(unittest.TestCase):
    def test_real_request_shape_and_output_decoding(self):
        facts = summarize_weather(sample())
        with patch("backend.openai_analysis.build_opener") as factory:
            factory.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(provider_reply()).encode()
            result = OpenAIAnalyzer("TEST-KEY", "gpt-4o-mini").analyze(facts)
            outgoing = factory.return_value.open.call_args.args[0]
            sent = json.loads(outgoing.data)
            self.assertEqual(outgoing.full_url, "https://api.openai.com/v1/responses")
            self.assertEqual(outgoing.get_header("Authorization"), "Bearer TEST-KEY")
            self.assertFalse(sent["store"])
            self.assertTrue(sent["text"]["format"]["strict"])
            self.assertEqual(json.loads(sent["input"]), facts)
            self.assertEqual(result["response_id"], "test-response")

    def test_http_failures_are_sanitized_without_retry(self):
        for status, code in ((401, "OPENAI_AUTH_ERROR"), (403, "OPENAI_AUTH_ERROR"),
                             (429, "OPENAI_RATE_LIMITED"), (500, "OPENAI_UNAVAILABLE"), (302, "OPENAI_UNAVAILABLE")):
            with self.subTest(status=status), patch("backend.openai_analysis.build_opener") as factory:
                factory.return_value.open.side_effect = HTTPError("url", status, "SECRET upstream error", {}, io.BytesIO(b"SECRET"))
                with self.assertRaises(AnalysisError) as error:
                    OpenAIAnalyzer("TEST-KEY").analyze({})
                self.assertEqual(error.exception.code, code)
                self.assertNotIn("SECRET", str(error.exception))
                self.assertEqual(factory.return_value.open.call_count, 1)

    def test_no_key_and_network_failure(self):
        with self.assertRaises(AnalysisError) as error:
            OpenAIAnalyzer("").analyze({})
        self.assertEqual(error.exception.code, "OPENAI_NOT_CONFIGURED")
        with patch("backend.openai_analysis.build_opener") as factory:
            factory.return_value.open.side_effect = URLError("secret internal reason")
            with self.assertRaises(AnalysisError) as error:
                OpenAIAnalyzer("TEST-KEY").analyze({})
            self.assertEqual(error.exception.code, "OPENAI_UNAVAILABLE")

    def test_refusal_incomplete_and_invalid_schema(self):
        cases = [None, {}, {**provider_reply(), "status": "incomplete"},
                 {**provider_reply(), "output": [{"type": "message", "content": [{"type": "refusal"}]}]}]
        invalid = provider_reply()
        invalid["output"][0]["content"][0]["text"] = '{"summary":"only"}'
        cases.append(invalid)
        for value in cases:
            with self.assertRaises(AnalysisError) as error:
                decode_analysis(value)
            self.assertEqual(error.exception.code, "OPENAI_INVALID_RESPONSE")

    def test_agent_preserves_forecast_and_propagates_failure(self):
        with TemporaryDirectory() as directory:
            write_history(Path(directory))
            forecasts = ForecastService(directory)
            payload = {**request(), "weather": weather()}
            expected = forecasts.forecast(payload)
            with patch("backend.openai_analysis.build_opener") as factory:
                factory.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(provider_reply()).encode()
                agent = AgentService(forecasts, OpenAIAnalyzer("TEST-KEY"))
                result = agent.forecast(payload)
                self.assertEqual(result["forecast"], expected)
                facts = json.loads(json.loads(factory.return_value.open.call_args.args[0].data)["input"])
                self.assertAlmostEqual(facts["turbines"][0]["normalized_power"]["mean"], 0.4)
                factory.return_value.open.side_effect = URLError("offline")
                with self.assertRaises(AnalysisError):
                    agent.forecast(payload)

    def test_invalid_weather_prevents_paid_call(self):
        analyzer = MagicMock()
        with self.assertRaises(ForecastValidationError):
            AgentService(analyzer=analyzer).weather_analysis({"weather": []})
        analyzer.analyze.assert_not_called()


class EnvTests(unittest.TestCase):
    def test_env_precedence_quotes_and_no_execution(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('# comment\nOPENAI_API_KEY="file-value"\nOPENAI_MODEL=gpt-4o-mini\nLITERAL=$(whoami)\n', encoding="utf-8")
            environ = {"OPENAI_API_KEY": "existing"}
            load_env(path, environ)
            self.assertEqual(environ["OPENAI_API_KEY"], "existing")
            self.assertEqual(environ["OPENAI_MODEL"], "gpt-4o-mini")
            self.assertEqual(environ["LITERAL"], "$(whoami)")

    def test_bad_env_does_not_leak_values_or_partially_load(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('OPENAI_API_KEY=hidden\nBROKEN SECRET\n', encoding="utf-8")
            environ = {}
            with self.assertRaises(ValueError) as error:
                load_env(path, environ)
            self.assertEqual(environ, {})
            self.assertNotIn("SECRET", str(error.exception))
