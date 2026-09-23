"""Synthetic test cases, not organizer data or weather forecasts."""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_csv import audit_csv


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.csv"

    def check(self, content, **options):
        self.path.write_text(content, encoding="utf-8")
        before = self.path.read_bytes()
        result = audit_csv(self.path, **options)
        self.assertEqual(self.path.read_bytes(), before)
        return result

    def test_valid_bom_two_turbines(self):
        result = self.check("\ufeffturbine,time,wind,power,temp\n1,2026-01-31T23:00:00+05:00,4,0.2,-10\n2,2026-01-31T23:00:00+05:00,5,0.3,-9\n",
                            numeric=["wind", "power", "temp"], timestamp="time", keys=["turbine", "time"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["time_min"], "2026-01-31T18:00:00+00:00")

    def test_semicolon_date_format(self):
        result = self.check("time;wind\n31.01.2026 23:00;4.5\n01.02.2026 00:00;0\n",
                            delimiter=";", numeric=["wind"], timestamp="time", time_format="%d.%m.%Y %H:%M")
        self.assertTrue(result["ok"])
        self.assertEqual(result["timezone"], "unspecified")
        self.assertEqual(result["time_max"], "2026-02-01T00:00:00")

    def test_bad_numbers_dates_missing_duplicate(self):
        result = self.check("time,wind\n2026-01-31T00:00Z,NaN\n2026-01-31T05:00+05:00,inf\nbad,abc\n,\n",
                            numeric=["wind"], timestamp="time", keys=["time"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["invalid_numeric_cells"], {"wind": 3})
        self.assertEqual(result["missing_checked_cells"], {"time": 1, "wind": 1})
        self.assertEqual(result["errors"], {"duplicate_key": 1, "invalid_timestamp": 1})

    def test_mixed_timezone(self):
        result = self.check("t\n2026-01-31T00:00Z\n2026-01-31T01:00\n", timestamp="t")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["time_min"])

    def test_bad_row_lengths_and_blank_line(self):
        self.assertEqual(self.check("a,b\n1\n2,3,4\n\n")["errors"], {"wrong_column_count": 3})

    def test_headers_and_missing_column(self):
        for content, options in [("", {}), ("a,a\n1,2\n", {}), ("a,\n1,2\n", {}),
                                 ("a\n1\n", {"required": ["b"]})]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.check(content, **options)

    def test_header_only(self):
        self.assertFalse(self.check("a,b\n")["ok"])

    def test_optional_missing(self):
        result = self.check("a,b\n1,\n", required=["a"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["missing_cells"], {"b": 1})

    def test_unclosed_quote(self):
        with self.assertRaises(csv.Error):
            self.check('a,b\n1,"unterminated\n')

    def test_cli_exit_codes(self):
        script = str(Path(__file__).resolve().parents[1] / "audit_csv.py")
        for content, expected in [("n\n1\n", 0), ("n\nNaN\n", 1), ("other\n1\n", 2)]:
            self.path.write_text(content)
            run = subprocess.run([sys.executable, script, str(self.path), "--numeric", "n"], capture_output=True, text=True)
            self.assertEqual(run.returncode, expected, run.stderr)
            self.assertEqual(json.loads(run.stderr if expected == 2 else run.stdout)["ok"], expected == 0)

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            audit_csv(self.path)


if __name__ == "__main__":
    unittest.main()
