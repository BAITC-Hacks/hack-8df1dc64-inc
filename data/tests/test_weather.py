"""Synthetic unit cases and integrity checks of the separately stored real archive."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.prepare_weather import write_archive
from data.weather_gfs import (COORDINATES, check_available, choose_run, decode_field,
                              field_ranges, hourly_points, parse_as_of, required_steps,
                              verify_archive)

UTC = timezone.utc


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.as_of = datetime(2026, 1, 31, 19, tzinfo=UTC)
        self.run = datetime(2026, 1, 31, 12, tzinfo=UTC)

    def test_cutoff_conversion_and_bad_cutoffs(self):
        self.assertEqual(parse_as_of("2026-02-01T00:00:00+05:00"), self.as_of)
        for value in ("2026-01-31T19:00:00", "2026-01-31T19:01:00Z", None):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                parse_as_of(value)

    def test_run_cycle_does_not_use_latest_unpublished_cycle(self):
        self.assertEqual(choose_run(self.as_of), self.run)
        self.assertEqual(choose_run("2026-02-01T01:00:00Z"), datetime(2026, 1, 31, 18, tzinfo=UTC))

    def test_steps_include_both_hour_boundaries(self):
        self.assertEqual(required_steps(self.as_of, self.run, 24), list(range(6, 34, 3)))
        self.assertEqual(required_steps(self.as_of, self.run, 48)[-1], 57)
        for horizon in (23, 49, True, 24.0):
            with self.subTest(horizon=horizon), self.assertRaises(ValueError):
                required_steps(self.as_of, self.run, horizon)

    def test_available_object_and_cutoff_equality(self):
        headers = {"Last-Modified": "Sat, 31 Jan 2026 19:00:00 GMT", "ETag": '"abc"'}
        self.assertEqual(check_available(headers, self.run, self.as_of)["last_modified"], "2026-01-31T19:00:00Z")

    def test_future_pre_run_or_missing_publication_rejected(self):
        for stamp in ("Sat, 31 Jan 2026 19:00:01 GMT", "Sat, 31 Jan 2026 11:59:59 GMT", "invalid", None):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                check_available({"Last-Modified": stamp, "ETag": '"abc"'}, self.run, self.as_of)
        with self.assertRaises(ValueError):
            check_available({"Last-Modified": "Sat, 31 Jan 2026 18:00:00 GMT"}, self.run, self.as_of)

    def index(self):
        return ("1:0:d=2026013112:TMP:2 m above ground:9 hour fcst:\n"
                "2:100:d=2026013112:UGRD:100 m above ground:9 hour fcst:\n"
                "3:220:d=2026013112:VGRD:100 m above ground:9 hour fcst:\n"
                "4:350:d=2026013112:RH:2 m above ground:9 hour fcst:\n")

    def test_index_byte_boundaries(self):
        self.assertEqual(field_ranges(self.index(), self.run, 9),
                         {"u": (100, 219), "v": (220, 349), "t": (0, 99)})

    def test_wrong_index_run_step_missing_duplicate_and_height(self):
        variants = [self.index().replace("2026013112", "2026020112"),
                    self.index().replace("9 hour", "12 hour"),
                    self.index().replace("100 m", "10 m"),
                    self.index().replace("3:220:", "3:100:"),
                    self.index().replace("RH:2 m", "TMP:2 m")]
        for index in variants:
            with self.subTest(index=index), self.assertRaises(ValueError):
                field_ranges(index, self.run, 9)

    def samples(self):
        # U=step, V=0 and T=-step: exact expected interpolation/averaging.
        return [{"step": step, "values": {
            field: {t: {"value": sign * step} for t in COORDINATES}
            for field, sign in (("u", 1), ("v", 0), ("t", -1))}}
            for step in required_steps(self.as_of, self.run, 24)]

    def test_hourly_means_grid_and_turbines(self):
        points = hourly_points(self.samples(), self.run, self.as_of, 24)
        self.assertEqual(len(points), 48)
        self.assertEqual(points[0], {"turbine_id": "turbine_1", "valid_at": "2026-01-31T20:00:00Z",
                                     "wind_speed_ms": 7.5, "temperature_c": -7.5})
        self.assertEqual(points[-1]["wind_speed_ms"], 30.5)
        self.assertEqual(points[-1]["valid_at"], "2026-02-01T19:00:00Z")
        self.assertEqual(points[-1]["turbine_id"], "turbine_2")

    def test_magnitude_of_vector_and_separate_turbines(self):
        samples = self.samples()
        for sample in samples:
            sample["values"]["u"]["turbine_1"]["value"] = 3
            sample["values"]["v"]["turbine_1"]["value"] = 4
        points = hourly_points(samples, self.run, self.as_of, 24)
        self.assertEqual(points[0]["wind_speed_ms"], 5)
        self.assertEqual(points[24]["wind_speed_ms"], 7.5)

    def test_no_extrapolation_or_missing_forecast_fill(self):
        samples = self.samples()
        with self.assertRaises(KeyError):
            hourly_points(samples[:-1], self.run, self.as_of, 24)
        with self.assertRaises(ValueError):
            hourly_points(samples + samples[:1], self.run, self.as_of, 24)
        samples[0]["values"]["u"]["turbine_1"]["value"] = float("nan")
        with self.assertRaises(ValueError):
            hourly_points(samples, self.run, self.as_of, 24)

    def test_grib_rejects_bad_envelope(self):
        with self.assertRaises(ValueError):
            decode_field(b"not a grib", "u", self.run, 9)

    def test_grib_checks_parameter_run_and_height(self):
        from eccodes import (codes_grib_new_from_samples, codes_set, codes_set_values,
                             codes_get_message, codes_release)
        handle = codes_grib_new_from_samples("regular_ll_sfc_grib2")
        try:
            for key, value in {"gridType": "regular_ll", "Ni": 720, "Nj": 361,
                               "latitudeOfFirstGridPointInDegrees": 90,
                               "latitudeOfLastGridPointInDegrees": -90,
                               "longitudeOfFirstGridPointInDegrees": 0,
                               "longitudeOfLastGridPointInDegrees": 359.5,
                               "iDirectionIncrementInDegrees": 0.5, "jDirectionIncrementInDegrees": 0.5,
                               "dataDate": 20260131, "dataTime": 1200, "step": 9,
                               "typeOfLevel": "heightAboveGround", "level": 100,
                               "parameterCategory": 2, "parameterNumber": 2}.items():
                codes_set(handle, key, value)
            codes_set_values(handle, [3.0] * (720 * 361))
            payload = codes_get_message(handle)
        finally:
            codes_release(handle)
        decoded = decode_field(payload, "u", self.run, 9)
        self.assertEqual(decoded["turbine_1"]["value"], 3)
        self.assertLess(decoded["turbine_1"]["distance_km"], 40)
        for field, run, step in (("v", self.run, 9), ("t", self.run, 9),
                                 ("u", self.run + timedelta(hours=6), 9), ("u", self.run, 12)):
            with self.subTest(field=field, run=run, step=step), self.assertRaises(ValueError):
                decode_field(payload, field, run, step)


class PublishedArchiveTests(unittest.TestCase):
    def test_daily_batches_cover_every_february_hour_for_both_turbines(self):
        folder = Path(__file__).resolve().parents[1] / "weather"
        start = datetime(2026, 1, 31, 19, tzinfo=UTC)
        actual = set()
        for day in range(28):
            as_of = start + timedelta(days=day)
            path = folder / as_of.strftime("%Y%m%dT%H%M%SZ.json")
            weather = json.loads(path.read_text())
            self.assertEqual(len(weather["points"]), 96)
            for p in weather["points"]:
                stamp = datetime.fromisoformat(p["valid_at"].replace("Z", "+00:00"))
                if as_of < stamp <= as_of + timedelta(hours=24):
                    key = (p["turbine_id"], stamp)
                    self.assertNotIn(key, actual)
                    actual.add(key)
        expected = {(t, start + timedelta(hours=h)) for t in COORDINATES for h in range(1, 28 * 24 + 1)}
        self.assertEqual(actual, expected)

    def test_all_real_archives_recompute_and_verify(self):
        folder = Path(__file__).resolve().parents[1] / "weather"
        proofs = sorted(folder.glob("*.provenance.json"))
        self.assertTrue(proofs, "Real weather archives must be bundled")
        for proof in proofs:
            with self.subTest(path=proof.name):
                weather = json.loads(proof.with_name(proof.name.replace(".provenance", "")).read_text())
                self.assertTrue(verify_archive(weather, json.loads(proof.read_text())))

    def test_evidence_or_value_tampering_fails_and_outputs_preserved(self):
        folder = Path(__file__).resolve().parents[1] / "weather"
        proofs = sorted(folder.glob("*.provenance.json"))
        self.assertTrue(proofs)
        provenance = json.loads(proofs[0].read_text())
        weather = json.loads(proofs[0].with_name(proofs[0].name.replace(".provenance", "")).read_text())
        changed = copy.deepcopy(weather)
        changed["points"][0]["wind_speed_ms"] += 1
        with self.assertRaises(ValueError):
            verify_archive(changed, provenance)
        changed = copy.deepcopy(provenance)
        changed["samples"][0]["objects"][0]["last_modified"] = "2026-03-01T00:00:00Z"
        with self.assertRaises(ValueError):
            verify_archive(weather, changed)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_archive(tmp, weather, provenance)
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_archive(tmp, weather, provenance)
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
