"""Small artificial histories with analytically known curves, never demo data."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from backend.model import build_curves, interpolate, utc_text
from backend.validation import ForecastValidationError

AS_OF = datetime(2026, 1, 31, 19, tzinfo=timezone.utc)


def history():
    return [{"turbine_id": turbine, "valid_at": utc_text(AS_OF - timedelta(hours=i)),
             "wind_speed_ms": 2.0 if i % 2 else 4.0, "temperature_c": -5.0,
             "normalized_power": (0.2 if i % 2 else 0.6) * factor, "sample_count": 6}
            for turbine, factor in (("turbine_1", 1), ("turbine_2", 0.5)) for i in range(24)]


class ModelTests(unittest.TestCase):
    def test_separate_curves_interpolation_and_bounds(self):
        rows = history()
        before = deepcopy(rows)
        curves = build_curves(rows, AS_OF, ("turbine_2", "turbine_1"))
        self.assertEqual(rows, before)
        self.assertEqual([c["turbine_id"] for c in curves], ["turbine_2", "turbine_1"])
        self.assertAlmostEqual(interpolate(curves[1]["curve_knots"], 3)[0], 0.4)
        self.assertAlmostEqual(interpolate(curves[0]["curve_knots"], 3)[0], 0.2)
        knots = curves[1]["curve_knots"]
        for speed, expected, outside in ((0, 0.2, True), (2, 0.2, False), (4, 0.6, False), (30, 0.6, True)):
            with self.subTest(speed=speed):
                actual, flag = interpolate(knots, speed)
                self.assertAlmostEqual(actual, expected)
                self.assertEqual(flag, outside)
        self.assertEqual(sum(k["sample_count"] for k in knots), 24)

    def test_invalid_history(self):
        for field, value in (("normalized_power", 1.1), ("normalized_power", -0.1),
                             ("wind_speed_ms", -1), ("wind_speed_ms", float("nan")),
                             ("temperature_c", float("inf")), ("sample_count", 5),
                             ("sample_count", 6.0), ("turbine_id", []),
                             ("valid_at", "2026-01-31T20:00:00Z"),
                             ("valid_at", "2026-01-31T18:30:00Z")):
            with self.subTest(field=field, value=value):
                rows = history()
                rows[0][field] = value
                with self.assertRaises(ForecastValidationError) as error:
                    build_curves(rows, AS_OF, ("turbine_1", "turbine_2"))
                self.assertEqual(error.exception.code, "INVALID_HISTORY")

    def test_future_and_february_rejected_even_with_later_cutoff(self):
        for cutoff, stamp in ((AS_OF - timedelta(hours=1), utc_text(AS_OF)),
                              (AS_OF + timedelta(days=20), "2026-02-01T00:00:00Z")):
            rows = history()
            rows[0]["valid_at"] = stamp
            with self.assertRaises(ForecastValidationError):
                build_curves(rows, cutoff, ("turbine_1", "turbine_2"))

    def test_duplicate_hour(self):
        rows = history()
        rows.append(dict(rows[0]))
        with self.assertRaises(ForecastValidationError) as error:
            build_curves(rows, AS_OF, ("turbine_1", "turbine_2"))
        self.assertEqual(error.exception.code, "INVALID_HISTORY")

    def test_insufficient_hours_or_wind_variation(self):
        for rows in (history()[:23], [{**row, "wind_speed_ms": 2.0} for row in history()[:24]], []):
            with self.assertRaises(ForecastValidationError) as error:
                build_curves(rows, AS_OF, ("turbine_1",))
            self.assertEqual(error.exception.code, "INSUFFICIENT_HISTORY")
