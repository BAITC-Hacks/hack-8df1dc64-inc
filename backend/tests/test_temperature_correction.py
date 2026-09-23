"""Separate correction tests; CSV/weather below are artificial test fixtures."""

import csv
import gzip
import hashlib
import io
import json
from copy import deepcopy
from datetime import datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from backend.agent import AgentService
from backend.model import utc_text
from backend.server import create_server
from backend.service import ForecastService
from backend.temperature_correction import reference_temperatures, temperature_corrected_power
from backend.tests.test_model import AS_OF
from backend.tests.test_service import request, weather
from backend.validation import ForecastValidationError
from data.history import COLUMNS, SOURCE_TZ


def write_temperature_history(directory, offset=0, future=False):
    """48 full hours with distinct turbine means; optional February test rows."""
    entries = []
    for turbine, difference in (("turbine_1", 0), ("turbine_2", 30)):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(COLUMNS)
        for index in range((48 + int(future)) * 6):
            stamp = AS_OF - timedelta(hours=48) + timedelta(minutes=index * 10)
            temperature = (-20 if index < 24 * 6 else 0) + difference + offset
            if index >= 48 * 6:
                temperature = 1000  # Forbidden test-month data must not affect T_ref.
            speed = 2 if index // 6 % 2 else 4
            writer.writerow([index, stamp.astimezone(SOURCE_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                             speed, {2: 0.2, 4: 0.6}[speed], temperature])
        payload = stream.getvalue().encode("utf-8")
        name = turbine + ".csv.gz"
        (directory / name).write_bytes(gzip.compress(payload))
        entries.append({"turbine_id": turbine, "path": name,
                        "sha256_uncompressed": hashlib.sha256(payload).hexdigest(),
                        "bytes_uncompressed": len(payload)})
    (directory / "manifest.json").write_text(json.dumps({"files": entries}), encoding="utf-8")


def payload_at(as_of=AS_OF, horizon=24):
    batch = weather(horizon)
    shift = as_of - AS_OF
    batch["issued_at"] = utc_text(as_of - timedelta(hours=6))
    batch["available_at"] = utc_text(as_of)
    for point in batch["points"]:
        point["valid_at"] = utc_text(datetime.fromisoformat(point["valid_at"].replace("Z", "+00:00")) + shift)
    return {**request(horizon), "as_of": utc_text(as_of), "weather": batch,
            "apply_temperature_correction": True}


class TemperatureCorrectionTests(unittest.TestCase):
    def test_reference_temperature_preserves_power_exactly(self):
        for temperature in (-80, 0, 15, 80):
            for power in (0, 0.123456789, 0.8, 1):
                with self.subTest(temperature=temperature, power=power):
                    self.assertEqual(temperature_corrected_power(power, temperature, temperature), power)

    def test_density_ratio_increases_cold_and_decreases_hot_power(self):
        self.assertAlmostEqual(temperature_corrected_power(0.4, -20, 10), 0.4 * 283.15 / 253.15)
        self.assertGreater(temperature_corrected_power(0.4, -20, 10), 0.4)
        self.assertLess(temperature_corrected_power(0.4, 40, 10), 0.4)

    def test_extreme_temperatures_and_power_are_clipped(self):
        for temperature in (-273.149999999, -100, 1000, 1e308):
            for power in (-0.5, 0, 0.4, 1, 2):
                with self.subTest(temperature=temperature, power=power):
                    result = temperature_corrected_power(power, temperature, 15)
                    self.assertGreaterEqual(result, 0)
                    self.assertLessEqual(result, 1)
        self.assertEqual(temperature_corrected_power(0.9, -100, 15), 1)
        self.assertEqual(temperature_corrected_power(0, -273.149999999, 1e308), 0)

    def test_nonfinite_and_impossible_inputs_are_rejected(self):
        for value in (float("nan"), float("inf"), -float("inf"), True, None, "15", 10 ** 400):
            for position in range(3):
                inputs = [0.4, 15, 15]
                inputs[position] = value
                with self.subTest(value=value, position=position), self.assertRaises(ValueError):
                    temperature_corrected_power(*inputs)
        for temperature in (-273.15, -1000):
            with self.assertRaises(ValueError):
                temperature_corrected_power(0.4, temperature, 15)
            with self.assertRaises(ValueError):
                temperature_corrected_power(0.4, 15, temperature)


class TemperatureServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        write_temperature_history(self.directory)
        self.service = ForecastService(self.directory)

    def test_default_and_explicit_false_preserve_full_response_before_and_after_correction(self):
        body = payload_at()
        del body["apply_temperature_correction"]
        with patch("backend.service.reference_temperatures", side_effect=AssertionError("Optional path used")), \
             patch("backend.service.temperature_corrected_power", side_effect=AssertionError("Optional path used")):
            baseline = self.service.forecast(body)
            self.assertEqual(self.service.forecast({**body, "apply_temperature_correction": False}), baseline)
        self.service.forecast({**body, "apply_temperature_correction": True})
        self.assertEqual(self.service.forecast(body), baseline)
        self.assertNotIn("temperature_correction", baseline["history"]["model"])
        self.assertTrue(all(point["normalized_power"] == 0.4 for point in baseline["points"]))

    def test_two_turbine_references_and_hourly_temperatures_24_and_48(self):
        for horizon in (24, 48):
            body = payload_at(horizon=horizon)
            for index, point in enumerate(body["weather"]["points"]):
                point["temperature_c"] = (-100, 0, 30, 1000)[index % 4]
            original = deepcopy(body)
            result = self.service.forecast(body)
            refs = result["history"]["model"]["temperature_correction"]["reference_temperature_c"]
            self.assertEqual(refs, {"turbine_1": -10, "turbine_2": 20})
            self.assertEqual(body, original)
            self.assertEqual(len(result["points"]), horizon * 2)
            for point in result["points"]:
                expected = min(1, 0.4 * (273.15 + refs[point["turbine_id"]]) / (273.15 + point["temperature_c"]))
                self.assertAlmostEqual(point["normalized_power"], expected)
                self.assertFalse(point["extrapolated"])

    def test_reference_means_cached_once_and_returned_metadata_cannot_mutate_cache(self):
        with patch("backend.service.reference_temperatures", wraps=reference_temperatures) as averages:
            first = self.service.forecast(payload_at())
            first["history"]["model"]["temperature_correction"]["reference_temperature_c"]["turbine_1"] = 999
            later = self.service.forecast(payload_at(AS_OF + timedelta(days=14)))
            self.assertEqual(averages.call_count, 1)
            self.assertEqual(later["history"]["model"]["temperature_correction"]["reference_temperature_c"]["turbine_1"], -10)

    def test_reference_uses_as_of_cutoff_and_never_february_observations(self):
        write_temperature_history(self.directory, future=True)
        for as_of, expected in ((AS_OF - timedelta(hours=24), -20),
                                (AS_OF, -10), (AS_OF + timedelta(days=14), -10)):
            with self.subTest(as_of=as_of):
                result = self.service.forecast(payload_at(as_of))
                refs = result["history"]["model"]["temperature_correction"]["reference_temperature_c"]
                self.assertEqual(refs, {"turbine_1": expected, "turbine_2": expected + 30})
                self.assertLessEqual(result["history"]["turbines"][0]["last_hour"], utc_text(min(as_of, AS_OF)))

    def test_changed_training_source_invalidates_reference_cache(self):
        self.service.forecast(payload_at())
        write_temperature_history(self.directory, offset=10)
        result = self.service.forecast(payload_at())
        self.assertEqual(result["history"]["model"]["temperature_correction"]["reference_temperature_c"],
                         {"turbine_1": 0, "turbine_2": 30})

    def test_flag_requires_boolean_and_is_not_a_history_summary_parameter(self):
        for value in (None, 0, 1, "true", "false", [], {}):
            with self.subTest(value=value), self.assertRaises(ForecastValidationError) as caught:
                self.service.forecast({**payload_at(), "apply_temperature_correction": value})
            self.assertEqual(caught.exception.code, "INVALID_REQUEST")
            self.assertEqual(caught.exception.field, "request.apply_temperature_correction")
        with self.assertRaises(ForecastValidationError):
            self.service.history_summary({"as_of": utc_text(AS_OF), "turbine_ids": ["turbine_1"],
                                          "timestamp_role": "start", "apply_temperature_correction": True})

    def test_impossible_forecast_temperature_rejected_only_when_enabled(self):
        body = payload_at()
        body["weather"]["points"][0]["temperature_c"] = -273.15
        with self.assertRaises(ForecastValidationError) as caught:
            self.service.forecast(body)
        self.assertEqual(caught.exception.code, "INVALID_WEATHER")
        self.assertEqual(self.service.forecast({**body, "apply_temperature_correction": False})["points"][0]["normalized_power"], 0.4)

    def test_impossible_training_temperature_rejected_only_when_enabled(self):
        write_temperature_history(self.directory, offset=-300)
        with self.assertRaises(ForecastValidationError) as caught:
            self.service.forecast(payload_at())
        self.assertEqual(caught.exception.code, "INVALID_HISTORY")
        self.assertEqual(self.service.forecast({**payload_at(), "apply_temperature_correction": False})["points"][0]["normalized_power"], 0.4)

    def test_agent_preserves_flag_on_recalculation_and_passes_reference_to_analysis(self):
        first = payload_at()["weather"]
        updated = deepcopy(first)
        for point in updated["points"]:
            point["temperature_c"] = 30
        with patch("backend.agent.WeatherProvider") as provider, patch("backend.agent.OpenAIAnalyzer") as analyzer:
            provider.return_value.resolve.side_effect = [(first, {"mode": "test"}), (updated, {"mode": "test"})]
            analyzer.return_value.analyze.return_value = {"recommended_action": "refresh_weather"}
            result = AgentService(self.service).forecast({**request(), "apply_temperature_correction": True})
            self.assertIn("recalculate", [step["step"] for step in result["cycle"]])
            self.assertAlmostEqual(result["forecast"]["points"][0]["normalized_power"], 0.4 * 263.15 / 303.15)
            self.assertIn("temperature_correction", analyzer.return_value.analyze.call_args.args[0]["model"])

    def test_http_accepts_explicit_flag_and_rejects_non_boolean(self):
        server = create_server(("127.0.0.1", 0), self.service)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for flag, expected_status in ((True, 200), (False, 200), ("true", 422)):
                connection = HTTPConnection(*server.server_address, timeout=10)
                try:
                    connection.request("POST", "/api/forecasts", json.dumps({**payload_at(), "apply_temperature_correction": flag}),
                                       {"Content-Type": "application/json"})
                    response = connection.getresponse()
                    result = json.loads(response.read())
                    self.assertEqual(response.status, expected_status)
                    if expected_status == 200:
                        expected_power = 0.4 * 263.15 / 270.15 if flag else 0.4
                        self.assertAlmostEqual(result["points"][0]["normalized_power"], expected_power)
                finally:
                    connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
