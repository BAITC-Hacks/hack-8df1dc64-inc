import csv
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.history import COLUMNS, RAW, TURBINES, hourly_history, iter_observations, source_report, verify_sources


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "input.csv"

    def rows(self, start="2026-01-31 10:00:00", count=6):
        dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        source = [[str(i+1), (dt+timedelta(minutes=10*i)).strftime("%Y-%m-%d %H:%M:%S"), str(i+1), "0.5", "-10"] for i in range(count)]
        self.write(source)
        return list(iter_observations(self.path, "turbine_1"))

    def write(self, rows, columns=COLUMNS):
        with self.path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f); writer.writerow(columns); writer.writerows(rows)

    def test_fixed_timezone_and_values(self):
        row = self.rows()[0]
        self.assertEqual(row["timestamp"].isoformat(), "2026-01-31T05:00:00+00:00")
        self.assertEqual(row["temperature_c"], -10)
        # User confirmed fixed UTC+5, including history before the Kazakhstan zone change.
        self.assertEqual(self.rows("2023-03-11 00:00:00")[0]["timestamp"].hour, 19)

    def test_start_mean(self):
        result = hourly_history(self.rows(), datetime(2026, 1, 31, 6, tzinfo=timezone.utc), "start")
        point = result["points"][0]
        self.assertEqual(point["valid_at"], "2026-01-31T06:00:00Z")
        self.assertEqual(point["wind_speed_ms"], 3.5)
        self.assertEqual(point["normalized_power"], 0.5)
        self.assertEqual(point["sample_count"], 6)

    def test_end_mean(self):
        result = hourly_history(self.rows("2026-01-31 10:10:00"), datetime(2026, 1, 31, 6, tzinfo=timezone.utc), "end")
        self.assertEqual(result["points"][0]["valid_at"], "2026-01-31T06:00:00Z")
        self.assertEqual(result["points"][0]["wind_speed_ms"], 3.5)

    def test_gap_is_not_zero_filled(self):
        rows = self.rows(count=12)
        del rows[2]
        result = hourly_history(rows, datetime(2026,1,31,7,tzinfo=timezone.utc), "start")
        self.assertEqual(len(result["points"]), 1)
        self.assertEqual(result["excluded_incomplete_hours"], 1)
        self.assertEqual(result["points"][0]["wind_speed_ms"], 9.5)

    def test_future_rows_excluded(self):
        result = hourly_history(self.rows(count=12), datetime(2026,1,31,6,tzinfo=timezone.utc), "start")
        self.assertEqual(len(result["points"]), 1)
        self.assertEqual(result["excluded_after_cutoff"], 6)

    def test_test_month_never_used(self):
        result = hourly_history(self.rows("2026-01-31 23:00:00", 12), datetime(2026,2,2,tzinfo=timezone.utc), "start")
        self.assertEqual(len(result["points"]), 1)
        self.assertEqual(result["excluded_test_rows"], 6)

    def test_missing_role_and_naive_cutoff_fail(self):
        rows = self.rows()
        for as_of, role in [(datetime(2026,2,1), "start"), (datetime(2026,2,1,tzinfo=timezone.utc), None), (datetime(2026,2,1,0,1,tzinfo=timezone.utc), "end")]:
            with self.subTest(as_of=as_of, role=role), self.assertRaises(ValueError):
                hourly_history(rows, as_of, role)

    def test_no_complete_hour_fails(self):
        with self.assertRaises(ValueError):
            hourly_history(self.rows(count=5), datetime(2026,2,1,tzinfo=timezone.utc), "start")

    def test_duplicate_observation_fails(self):
        rows = self.rows()
        with self.assertRaises(ValueError):
            hourly_history(rows + rows[:1], datetime(2026,2,1,tzinfo=timezone.utc), "start")

    def test_invalid_raw_inputs(self):
        good = ["1", "2026-01-31 10:00:00", "1", "0.5", "-10"]
        variants = [good[:2]+["NaN"]+good[3:], good[:2]+["-1"]+good[3:],
                    good[:3]+[""]+good[4:], ["1","bad",*good[2:]],
                    ["1","2026-01-31 10:05:00",*good[2:]], good[:-1]]
        for bad in variants:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.write([bad]);list(iter_observations(self.path, "turbine_1"))
        for source in [[good,good], [good,["2",*good[1:]]], []]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.write(source);list(iter_observations(self.path, "turbine_1"))

    def test_wrong_columns_and_turbine(self):
        self.rows()
        with self.assertRaises(ValueError):
            list(iter_observations(self.path, "unknown"))
        self.write([], ["wrong"])
        with self.assertRaises(ValueError):
            list(iter_observations(self.path, "turbine_1"))

    def test_two_turbines_not_combined(self):
        one = self.rows()
        two = [dict(row, turbine_id="turbine_2", wind_speed_ms=12) for row in one]
        points = hourly_history(one+two, datetime(2026,2,1,tzinfo=timezone.utc), "start")["points"]
        self.assertEqual([p["wind_speed_ms"] for p in points], [3.5,12])

    def test_report_gaps(self):
        self.rows(count=12)
        with self.path.open(newline="") as stream:
            rows = list(csv.reader(stream))
        del rows[3]
        self.write(rows[1:])
        report = source_report(self.path, "turbine_1")
        self.assertEqual(report["missing_slots"], 1)
        self.assertEqual(report["expected_slots_between_endpoints"], 12)

    def test_cli_requires_role(self):
        run = subprocess.run([sys.executable,"-m","data.prepare_history","hourly","--as-of","2026-02-01T00:00:00Z"], capture_output=True,text=True)
        self.assertEqual(run.returncode, 2)


class BundledSourceTests(unittest.TestCase):
    def test_real_sources_hashes_and_coverage(self):
        manifest = verify_sources()
        self.assertEqual(len(manifest["files"]), 2)
        for turbine, count in zip(TURBINES, [142360,149499]):
            report = source_report(RAW / (turbine+".csv.gz"), turbine)
            self.assertEqual(report["rows"], count)
            self.assertEqual(report["first_source_time"], "2023-03-11 0:00:00")
            self.assertEqual(report["last_source_time"], "2026-01-31 23:50:00")
            self.assertEqual(report["test_period_rows"], 0)
            self.assertGreater(report["missing_slots"], 0)
            self.assertEqual(count + report["missing_slots"], report["expected_slots_between_endpoints"])


if __name__ == "__main__":
    unittest.main()
