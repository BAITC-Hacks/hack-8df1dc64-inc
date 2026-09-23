from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import unittest

from backend.validation import ForecastValidationError, validate_forecast_inputs


def sample_inputs(hours=24, turbines=None):
    """Artificial inputs for validation only; not an actual weather forecast."""
    selected = ["turbine_1"] if turbines is None else list(turbines)
    cutoff = datetime(2026, 1, 31, 19, tzinfo=timezone.utc)
    request = {"as_of": cutoff.isoformat(), "horizon_hours": hours, "turbine_ids": selected}
    weather = {
        "source": "synthetic-unit-test",
        "run_id": "test-run-2026-01-31-12",
        "issued_at": "2026-01-31T12:00:00Z",
        "available_at": "2026-01-31T15:30:00Z",
        "availability_evidence": "Artificial timestamps for validation tests only.",
        "wind_height_m": 100,
        "points": [
            {
                "turbine_id": turbine,
                "valid_at": (cutoff + timedelta(hours=hour)).isoformat(),
                "wind_speed_ms": 4 + hour / 10,
                "temperature_c": -8 + hour / 10,
            }
            for turbine in selected
            for hour in range(1, hours + 1)
        ],
    }
    return request, weather


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.request, self.weather = sample_inputs()

    def assert_rejected(self, code, field=None, request=None, weather=None):
        with self.assertRaises(ForecastValidationError) as caught:
            validate_forecast_inputs(
                self.request if request is None else request,
                self.weather if weather is None else weather,
            )
        error = caught.exception
        self.assertEqual(error.code, code)
        if field is not None:
            self.assertEqual(error.field, field)
        self.assertTrue(error.message)
        self.assertEqual(error.as_dict(), {"code": code, "field": error.field, "message": error.message})
        return error

    def test_24_hour_forecast_preserves_input_values(self):
        result = validate_forecast_inputs(self.request, self.weather)
        self.assertEqual(len(result.weather.points), 24)
        self.assertEqual(result.request.turbine_ids, ("turbine_1",))
        first, last = result.weather.points[0], result.weather.points[-1]
        self.assertEqual(first.valid_at, datetime(2026, 1, 31, 20, tzinfo=timezone.utc))
        self.assertEqual(last.valid_at, datetime(2026, 2, 1, 19, tzinfo=timezone.utc))
        self.assertEqual(first.wind_speed_ms, 4.1)
        self.assertEqual(last.temperature_c, -5.6)
        self.assertEqual(result.weather.source, self.weather["source"])
        self.assertEqual(result.weather.run_id, self.weather["run_id"])
        self.assertEqual(result.weather.availability_evidence, self.weather["availability_evidence"])
        self.assertEqual(result.weather.wind_height_m, 100.0)

    def test_48_hours_two_turbines_order_and_utc(self):
        request, weather = sample_inputs(48, ["turbine_2", "turbine_1"])
        weather["points"].reverse()
        result = validate_forecast_inputs(request, weather)
        self.assertEqual(len(result.weather.points), 96)
        self.assertEqual([point.turbine_id for point in result.weather.points[:48]], ["turbine_2"] * 48)
        self.assertEqual(result.weather.points[48].turbine_id, "turbine_1")
        self.assertEqual(result.weather.points[-1].valid_at, datetime(2026, 2, 2, 19, tzinfo=timezone.utc))
        self.assertTrue(all(point.valid_at.tzinfo is timezone.utc for point in result.weather.points))

    def test_intermediate_horizon_is_supported(self):
        request, weather = sample_inputs(30)
        self.assertEqual(len(validate_forecast_inputs(request, weather).weather.points), 30)

    def test_equivalent_timezone_offsets(self):
        self.request["as_of"] = "2026-02-01T00:00:00+05:00"
        self.weather["issued_at"] = "2026-01-31T14:00:00+02:00"
        self.weather["available_at"] = "2026-02-01T00:00:00+05:00"
        self.weather["points"][0]["valid_at"] = "2026-02-01T01:00:00+05:00"
        result = validate_forecast_inputs(self.request, self.weather)
        self.assertEqual(result.request.as_of, datetime(2026, 1, 31, 19, tzinfo=timezone.utc))
        self.assertEqual(result.weather.available_at, result.request.as_of)
        self.assertEqual(result.weather.issued_at.hour, 12)

    def test_hour_alignment_is_checked_in_utc(self):
        self.request["as_of"] = "2026-02-01T00:30:00+05:30"
        result = validate_forecast_inputs(self.request, self.weather)
        self.assertEqual(result.request.as_of.hour, 19)
        self.request["as_of"] = "2026-02-01T00:00:00+05:30"
        self.assert_rejected("INVALID_REQUEST", "request.as_of")

    def test_metadata_may_have_fractional_seconds(self):
        self.weather["issued_at"] = "2026-01-31T12:34:56.123456Z"
        self.assertEqual(validate_forecast_inputs(self.request, self.weather).weather.issued_at.microsecond, 123456)

    def test_inputs_not_mutated_and_result_immutable(self):
        originals = deepcopy((self.request, self.weather))
        result = validate_forecast_inputs(self.request, self.weather)
        self.assertEqual((self.request, self.weather), originals)
        with self.assertRaises(FrozenInstanceError):
            result.request.horizon_hours = 48
        with self.assertRaises(FrozenInstanceError):
            result.weather.points[0].wind_speed_ms = 9
        self.request["turbine_ids"].append("turbine_2")
        self.weather["points"][0]["wind_speed_ms"] = 99
        self.assertEqual(result.request.turbine_ids, ("turbine_1",))
        self.assertEqual(result.weather.points[0].wind_speed_ms, 4.1)

    def test_late_publication_rejected_even_when_issue_is_old(self):
        self.weather["available_at"] = "2026-01-31T19:00:00.000001Z"
        original = deepcopy(self.weather)
        self.assert_rejected("WEATHER_NOT_AVAILABLE", "weather.available_at")
        self.assertEqual(self.weather, original)

    def test_publication_before_issue_rejected(self):
        self.weather["available_at"] = "2026-01-31T11:59:59Z"
        self.assert_rejected("INVALID_WEATHER", "weather.available_at")

    def test_issue_publication_and_cutoff_can_be_equal(self):
        self.weather["issued_at"] = self.request["as_of"]
        self.weather["available_at"] = self.request["as_of"]
        result = validate_forecast_inputs(self.request, self.weather)
        self.assertEqual(result.weather.issued_at, result.request.as_of)

    def test_invalid_horizons(self):
        for horizon in (23, 49, 0, -24, True, False, 24.0, "24", None, float("nan")):
            with self.subTest(horizon=horizon):
                request = dict(self.request, horizon_hours=horizon)
                self.assert_rejected("INVALID_REQUEST", "request.horizon_hours", request=request)

    def test_invalid_turbine_lists(self):
        for turbines in ([], (), None, "turbine_1", ["unknown"], ["turbine_1", "turbine_1"], [True], [{}], [[]]):
            with self.subTest(turbines=turbines):
                self.assert_rejected("INVALID_REQUEST", request=dict(self.request, turbine_ids=turbines))

    def test_nonobjects_rejected(self):
        for value in (None, [], "", 42):
            with self.subTest(value=value):
                with self.assertRaises(ForecastValidationError) as caught:
                    validate_forecast_inputs(value, self.weather)
                self.assertEqual(caught.exception.code, "INVALID_REQUEST")
                with self.assertRaises(ForecastValidationError) as caught:
                    validate_forecast_inputs(self.request, value)
                self.assertEqual(caught.exception.code, "INVALID_WEATHER")

    def test_missing_and_unknown_request_fields(self):
        for field in self.request:
            with self.subTest(field=field):
                request = dict(self.request)
                del request[field]
                self.assert_rejected("INVALID_REQUEST", f"request.{field}", request=request)
        self.assert_rejected("INVALID_REQUEST", "request", request=dict(self.request, typo=1))

    def test_missing_and_unknown_weather_fields(self):
        for field in self.weather:
            with self.subTest(field=field):
                weather = dict(self.weather)
                del weather[field]
                self.assert_rejected("INVALID_WEATHER", f"weather.{field}", weather=weather)
        self.assert_rejected("INVALID_WEATHER", "weather", weather=dict(self.weather, typo=1))

    def test_invalid_request_dates(self):
        values = (
            None, 123, "2026-01-31", "2026-01-31T19:00:00", "2026-01-31 19:00:00Z",
            "2026-02-30T19:00:00Z", "2026-01-31T19:00:00+00:99", "2026-01-31T19:00:00+24:00",
            "2026-01-31T19:00:60Z", "2026-01-31T19:01:00Z", "2026-01-31T19:00:00.000001Z",
            "2026-01-31T19:00:00.0000001Z", "0001-01-01T00:00:00+01:00",
        )
        for value in values:
            with self.subTest(value=value):
                self.assert_rejected("INVALID_REQUEST", "request.as_of", request=dict(self.request, as_of=value))

    def test_horizon_must_fit_datetime_range(self):
        self.request["as_of"] = "9999-12-31T00:00:00Z"
        self.assert_rejected("INVALID_REQUEST", "request.as_of")

    def test_metadata_dates_require_timezone_and_valid_date(self):
        for field in ("issued_at", "available_at"):
            for value in ("2026-01-31T12:00:00", "invalid", None, "2026-02-30T00:00:00Z"):
                with self.subTest(field=field, value=value):
                    weather = dict(self.weather)
                    weather[field] = value
                    self.assert_rejected("INVALID_WEATHER", f"weather.{field}", weather=weather)

    def test_metadata_text_is_required(self):
        for field in ("source", "run_id", "availability_evidence"):
            for value in (None, "", "  ", 1):
                with self.subTest(field=field, value=value):
                    weather = dict(self.weather)
                    weather[field] = value
                    self.assert_rejected("INVALID_WEATHER", f"weather.{field}", weather=weather)

    def test_height_must_be_positive_finite_number(self):
        for value in (0, -1, True, None, "100", float("inf"), float("nan"), 10**400):
            with self.subTest(value=value):
                self.assert_rejected("INVALID_WEATHER", "weather.wind_height_m", weather=dict(self.weather, wind_height_m=value))

    def test_empty_or_missing_hour_grid(self):
        for points in ([], self.weather["points"][1:]):
            with self.subTest(length=len(points)):
                self.assert_rejected("INCOMPLETE_WEATHER_GRID", "weather.points", weather=dict(self.weather, points=points))

    def test_duplicate_hour_even_with_equivalent_offset(self):
        self.weather["points"][1]["valid_at"] = "2026-02-01T01:00:00+05:00"
        self.assert_rejected("INCOMPLETE_WEATHER_GRID", "weather.points[1]")

    def test_extra_hour_and_cutoff_hour_rejected(self):
        for timestamp in ("2026-01-31T19:00:00Z", "2026-02-01T20:00:00Z"):
            with self.subTest(timestamp=timestamp):
                weather = deepcopy(self.weather)
                weather["points"][0]["valid_at"] = timestamp
                self.assert_rejected("INCOMPLETE_WEATHER_GRID", "weather.points[0]", weather=weather)
        weather = deepcopy(self.weather)
        weather["points"].append(dict(weather["points"][-1], valid_at="2026-02-01T20:00:00Z"))
        self.assert_rejected("INCOMPLETE_WEATHER_GRID", "weather.points[24]", weather=weather)

    def test_unrequested_turbine_rejected(self):
        for turbine in ("turbine_2", "unknown"):
            with self.subTest(turbine=turbine):
                weather = deepcopy(self.weather)
                weather["points"][0]["turbine_id"] = turbine
                self.assert_rejected("INCOMPLETE_WEATHER_GRID", "weather.points[0]", weather=weather)

    def test_second_turbine_requires_its_own_complete_grid(self):
        self.request["turbine_ids"].append("turbine_2")
        error = self.assert_rejected("INCOMPLETE_WEATHER_GRID", "weather.points")
        self.assertIn("Missing 24 point(s)", error.message)

    def test_points_must_be_array_of_objects(self):
        for points in (None, {}, (), "points", [None], [[]]):
            with self.subTest(points=points):
                self.assert_rejected("INVALID_WEATHER", weather=dict(self.weather, points=points))

    def test_point_fields_are_required_and_no_extra_fields(self):
        for field in self.weather["points"][0]:
            with self.subTest(field=field):
                weather = deepcopy(self.weather)
                del weather["points"][0][field]
                self.assert_rejected("INVALID_WEATHER", f"weather.points[0].{field}", weather=weather)
        self.weather["points"][0]["typo"] = 1
        self.assert_rejected("INVALID_WEATHER", "weather.points[0]")

    def test_invalid_point_dates(self):
        for value in (None, "2026-01-31T20:00:00", "2026-01-31T20:30:00Z", "not a date"):
            with self.subTest(value=value):
                weather = deepcopy(self.weather)
                weather["points"][0]["valid_at"] = value
                self.assert_rejected("INVALID_WEATHER", "weather.points[0].valid_at", weather=weather)

    def test_point_numbers_are_finite_and_strict(self):
        for field in ("wind_speed_ms", "temperature_c"):
            for value in (True, False, None, "7.5", float("nan"), float("inf"), -float("inf"), 10**400):
                with self.subTest(field=field, value=value):
                    weather = deepcopy(self.weather)
                    weather["points"][0][field] = value
                    self.assert_rejected("INVALID_WEATHER", f"weather.points[0].{field}", weather=weather)

    def test_negative_wind_rejected_zero_wind_and_negative_temperature_allowed(self):
        self.weather["points"][0]["wind_speed_ms"] = -0.1
        self.assert_rejected("INVALID_WEATHER", "weather.points[0].wind_speed_ms")
        self.weather["points"][0]["wind_speed_ms"] = 0
        self.weather["points"][0]["temperature_c"] = -30
        first = validate_forecast_inputs(self.request, self.weather).weather.points[0]
        self.assertEqual((first.wind_speed_ms, first.temperature_c), (0.0, -30.0))


if __name__ == "__main__":
    unittest.main()
