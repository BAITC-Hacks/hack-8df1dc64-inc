"""Integration through real CSV parsing, hashes, aggregation and calculation.

Sources generated here are artificial test fixtures, not organizer measurements or
historical weather archives. Production service has no generated-data fallback.
"""

import csv
import gzip
import hashlib
import io
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.model import utc_text
from backend.service import ForecastService, strict_json
from backend.tests.test_model import AS_OF
from backend.validation import ForecastValidationError
from data.history import COLUMNS, SOURCE_TZ


def write_history(directory):
    entries = []
    for turbine in ("turbine_1", "turbine_2"):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(COLUMNS)
        for index in range(24 * 6):
            stamp = AS_OF - timedelta(hours=24) + timedelta(minutes=index * 10)
            speed = 2 if index // 6 % 2 else 4
            power = {2: 0.2, 4: 0.6}[speed] * (1 if turbine == "turbine_1" else 0.5)
            writer.writerow([index, stamp.astimezone(SOURCE_TZ).strftime("%Y-%m-%d %H:%M:%S"), speed, power, -5])
        payload = stream.getvalue().encode("utf-8")
        name = turbine + ".csv.gz"
        (directory / name).write_bytes(gzip.compress(payload))
        entries.append({"turbine_id": turbine, "path": name,
                        "sha256_uncompressed": hashlib.sha256(payload).hexdigest(),
                        "bytes_uncompressed": len(payload)})
    (directory / "manifest.json").write_text(json.dumps({"files": entries}), encoding="utf-8")


def request(horizon=24):
    return {"as_of": utc_text(AS_OF), "horizon_hours": horizon,
            "turbine_ids": ["turbine_1", "turbine_2"], "timestamp_role": "start"}


def weather(horizon=24, speed=3):
    return {"source": "ARTIFICIAL_TEST_ONLY", "run_id": "test-001",
            "issued_at": utc_text(AS_OF - timedelta(hours=6)), "available_at": utc_text(AS_OF),
            "availability_evidence": "Artificial unit-test metadata, not an actual weather archive",
            "wind_height_m": 10,
            "points": [{"turbine_id": turbine, "valid_at": utc_text(AS_OF + timedelta(hours=hour)),
                        "wind_speed_ms": speed, "temperature_c": -3}
                       for turbine in ("turbine_1", "turbine_2") for hour in range(1, horizon + 1)]}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        write_history(self.directory)
        self.service = ForecastService(self.directory, self.directory)

    def test_end_to_end_forecast_24_and_48(self):
        for horizon in (24, 48):
            payload = {**request(horizon), "weather": weather(horizon)}
            before = deepcopy(payload)
            result = self.service.forecast(payload)
            self.assertEqual(payload, before)
            self.assertEqual(len(result["points"]), horizon * 2)
            self.assertFalse(result["timestamp_role_confirmed"])
            self.assertEqual(result["weather"]["input_mode"], "provided")
            self.assertEqual(len(result["history"]["source_hashes"]), 2)
            for row in result["points"]:
                self.assertAlmostEqual(row["normalized_power"], 0.4 if row["turbine_id"] == "turbine_1" else 0.2)
                self.assertFalse(row["extrapolated"])

    def test_archive_selects_subset_and_rereads_update(self):
        path = self.directory / "20260131T190000Z.json"
        body = request()
        body["turbine_ids"] = ["turbine_2"]
        for speed, expected in ((3, 0.2), (100, 0.3)):
            path.write_text(json.dumps(weather(48, speed)), encoding="utf-8")
            result = self.service.forecast(body)
            self.assertEqual(result["weather"]["input_mode"], "archive_file")
            self.assertEqual(len(result["points"]), 24)
            self.assertTrue(all(p["turbine_id"] == "turbine_2" for p in result["points"]))
            self.assertAlmostEqual(result["points"][0]["normalized_power"], expected)
            self.assertEqual(result["points"][0]["extrapolated"], speed == 100)

    def test_archive_does_not_hide_duplicate_or_late_release(self):
        for code in ("INCOMPLETE_WEATHER_GRID", "WEATHER_NOT_AVAILABLE"):
            batch = weather(48)
            if code == "INCOMPLETE_WEATHER_GRID":
                batch["points"].append(dict(batch["points"][0]))
            else:
                batch["available_at"] = utc_text(AS_OF + timedelta(hours=1))
            (self.directory / "20260131T190000Z.json").write_text(json.dumps(batch), encoding="utf-8")
            with self.assertRaises(ForecastValidationError) as error:
                self.service.forecast(request())
            self.assertEqual(error.exception.code, code)

    def test_missing_archive_is_error(self):
        for service in (self.service, ForecastService(self.directory)):
            with self.assertRaises(ForecastValidationError) as error:
                service.forecast(request())
            self.assertEqual(error.exception.code, "WEATHER_UNAVAILABLE")

    def test_summary_and_unknown_role(self):
        body = request()
        del body["horizon_hours"]
        summary = self.service.history_summary(body)
        self.assertEqual([t["hours"] for t in summary["turbines"]], [24, 24])
        for value in (None, "unknown", "", []):
            with self.assertRaises(ForecastValidationError) as error:
                self.service.history_summary({**body, "timestamp_role": value})
            self.assertEqual(error.exception.code, "INVALID_REQUEST")

    def test_missing_and_corrupted_history_are_errors(self):
        body = request()
        del body["horizon_hours"]
        source = self.directory / "turbine_1.csv.gz"
        source.write_bytes(gzip.compress(b"altered"))
        with self.assertRaises(ForecastValidationError) as error:
            self.service.history_summary(body)
        self.assertEqual(error.exception.code, "INVALID_HISTORY")
        source.unlink()
        with self.assertRaises(ForecastValidationError) as error:
            self.service.history_summary(body)
        self.assertEqual(error.exception.code, "HISTORY_UNAVAILABLE")

    def test_json_rejects_duplicates_and_nonfinite(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.assertRaises(ValueError):
                strict_json(text)


class OrganizerHistoryTests(unittest.TestCase):
    def test_real_sources_build_two_curves_with_provenance(self):
        result = ForecastService().history_summary({"as_of": utc_text(AS_OF),
                   "timestamp_role": "start", "turbine_ids": ["turbine_1", "turbine_2"]})
        self.assertEqual(len(result["turbines"]), 2)
        self.assertEqual(result["source_hashes"]["turbine_1"], "c4c341582fb2dd348b7187f0128cff265fe055f469413871ebb5db50eef58b5b")
        for turbine in result["turbines"]:
            self.assertGreater(turbine["hours"], 20000)
            self.assertLessEqual(turbine["last_hour"], utc_text(AS_OF))
            self.assertGreater(len(turbine["curve_knots"]), 2)
