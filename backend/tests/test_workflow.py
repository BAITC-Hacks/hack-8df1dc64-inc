"""Workflow uses real bundled NOAA data; network-only branches are isolated in tests."""
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from backend.agent import AgentService
from backend.service import ForecastService
from backend.tests.test_service import request, write_history
from backend.validation import ForecastValidationError, validate_forecast_request
from backend.weather_provider import WeatherProvider, select_weather
from data.weather_gfs import digest

ROOT = Path(__file__).resolve().parents[2]
STEM = "20260131T190000Z"


def archive():
    folder = ROOT / "data" / "weather"
    return (json.loads((folder / (STEM + ".json")).read_text()),
            json.loads((folder / (STEM + ".provenance.json")).read_text()))


class WorkflowTests(TestCase):
    def test_real_archive_provenance_and_subset(self):
        with TemporaryDirectory() as directory:
            provider = WeatherProvider(ROOT / "data" / "weather", directory)
            query = validate_forecast_request({k: v for k, v in request().items() if k != "timestamp_role"})
            batch, source = provider.resolve(query)
            self.assertEqual(source["mode"], "bundled")
            self.assertEqual(len(source["provenance_sha256"]), 64)
            self.assertEqual(len(select_weather(batch, query)["points"]), 48)

    def test_refresh_writes_version_then_uses_cache_without_download(self):
        with TemporaryDirectory() as directory:
            provider = WeatherProvider(ROOT / "data" / "weather", directory)
            query = validate_forecast_request({k: v for k, v in request().items() if k != "timestamp_role"})
            batch, proof = archive()
            with patch("backend.weather_provider.build_archive", return_value=(batch, proof)) as download:
                result, source = provider.resolve(query, True)
                self.assertEqual(source["mode"], "downloaded")
                cached, source = provider.resolve(query)
                self.assertEqual(cached, result)
                self.assertEqual(source["mode"], "cache")
                self.assertEqual(download.call_count, 1)
                download.assert_called_once_with(query.as_of, 48, workers=1)

    def test_tampered_bundle_fails_without_network_replacement(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            batch, proof = archive()
            batch["points"][0]["wind_speed_ms"] += 1
            (folder / (STEM + ".json")).write_text(json.dumps(batch))
            (folder / (STEM + ".provenance.json")).write_text(json.dumps(proof))
            provider = WeatherProvider(folder, folder / "cache")
            query = validate_forecast_request({k: v for k, v in request().items() if k != "timestamp_role"})
            with patch("backend.weather_provider.build_archive") as download:
                with self.assertRaises(ForecastValidationError) as error:
                    provider.resolve(query)
                self.assertEqual(error.exception.code, "INVALID_WEATHER")
                download.assert_not_called()

    def test_agent_refresh_decision_is_bounded_and_recalculates_changed_weather(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            write_history(folder)
            service = ForecastService(folder)
            first, proof = archive()
            updated = deepcopy(first)
            for point in updated["points"]:
                point["wind_speed_ms"] = 3
            # Provider boundary and LLM boundary only; real history/model run twice.
            with patch("backend.agent.WeatherProvider") as provider_class, patch("backend.agent.OpenAIAnalyzer") as analyzer_class:
                provider_class.return_value.resolve.side_effect = [(first, {"mode": "bundled", "provenance_sha256": digest(proof)}),
                                                                   (updated, {"mode": "downloaded", "provenance_sha256": "test-proof"})]
                analyzer_class.return_value.analyze.return_value = {"recommended_action": "refresh_weather"}
                result = AgentService(service).forecast(request())
                self.assertEqual(provider_class.return_value.resolve.call_count, 2)
                self.assertEqual(analyzer_class.return_value.analyze.call_count, 2)
                self.assertIn("recalculate", [s["step"] for s in result["cycle"]])
                self.assertAlmostEqual(result["forecast"]["points"][0]["normalized_power"], 0.4)

    def test_history_cache_cannot_be_mutated_and_respects_cutoffs(self):
        with TemporaryDirectory() as directory:
            write_history(Path(directory))
            service = ForecastService(directory)
            query = {k: v for k, v in request().items() if k != "horizon_hours"}
            first = service.history_summary(query)
            first["turbines"][0]["curve_knots"][0]["normalized_power"] = 999
            second = service.history_summary({**query, "as_of": "2026-02-10T19:00:00Z"})
            self.assertLessEqual(second["turbines"][0]["curve_knots"][0]["normalized_power"], 1)
            self.assertEqual(second["as_of"], "2026-02-10T19:00:00Z")
            with self.assertRaises(ForecastValidationError):
                service.history_summary({**query, "as_of": "2026-01-31T18:00:00Z"})

    def test_refresh_type_and_provided_weather_combination_rejected(self):
        for body in ({**request(), "refresh_weather": "true"}, {**request(), "refresh_weather": True, "weather": {}}):
            with self.assertRaises(ForecastValidationError) as error:
                AgentService().forecast(body)
            self.assertEqual(error.exception.code, "INVALID_REQUEST")
