"""Read-only CSV quality audit; does not define the shared backend schema."""

import argparse
import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def audit_csv(path, *, delimiter=",", encoding="utf-8-sig", required=(),
              numeric=(), timestamp=None, time_format=None, keys=()):
    """Report aggregate diagnostics without copying source cell values.

    No imputation, timezone assumptions, numeric limits or hourly aggregation.
    Duplicate keys normalize timezone-aware timestamps to UTC.
    """
    errors, missing, invalid = Counter(), Counter(), Counter()
    seen, awareness = set(), set()
    first = last = None
    rows = 0
    with Path(path).open(encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=delimiter, strict=True)
        header = next(reader, None)
        if not header or any(not name.strip() for name in header):
            raise ValueError("Missing or empty CSV column names")
        if len(set(header)) != len(header):
            raise ValueError("Duplicate CSV column names")
        checked = set(required) | set(numeric) | set(keys)
        if timestamp:
            checked.add(timestamp)
        if checked - set(header):
            raise ValueError("Requested columns are absent: " + ", ".join(sorted(checked - set(header))))
        for values in reader:
            rows += 1
            if len(values) != len(header):
                errors["wrong_column_count"] += 1
                continue
            row = dict(zip(header, values))
            for name, value in row.items():
                if not value.strip():
                    missing[name] += 1
            for name in numeric:
                if not row[name].strip():
                    continue
                try:
                    if not math.isfinite(float(row[name])):
                        raise ValueError("non-finite")
                except ValueError:
                    invalid[name] += 1
            parsed = None
            if timestamp and row[timestamp].strip():
                try:
                    value = row[timestamp].strip()
                    parsed = (datetime.strptime(value, time_format) if time_format
                              else datetime.fromisoformat(value))
                    aware = parsed.utcoffset() is not None
                    awareness.add(aware)
                    if aware:
                        parsed = parsed.astimezone(timezone.utc)
                    if len(awareness) == 1:
                        first = parsed if first is None else min(first, parsed)
                        last = parsed if last is None else max(last, parsed)
                except ValueError:
                    errors["invalid_timestamp"] += 1
            if keys and all(row[name].strip() for name in keys):
                key = tuple(parsed if name == timestamp and parsed is not None
                            else row[name] for name in keys)
                if key in seen:
                    errors["duplicate_key"] += 1
                seen.add(key)
    if not rows:
        errors["no_data_rows"] += 1
    if len(awareness) > 1:
        errors["mixed_timestamp_timezones"] += 1
        first = last = None
    required_missing = {name: missing[name] for name in sorted(checked) if missing[name]}
    return {
        "ok": not (errors or required_missing or invalid),
        "rows": rows, "columns": header,
        "missing_cells": dict(sorted(missing.items())),
        "missing_checked_cells": required_missing,
        "invalid_numeric_cells": dict(sorted(invalid.items())),
        "errors": dict(sorted(errors.items())),
        "time_min": first.isoformat() if first else None,
        "time_max": last.isoformat() if last else None,
        "timezone": "UTC" if awareness == {True} else "unspecified" if awareness == {False} else None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--delimiter", default=",")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--required", action="append", default=[])
    parser.add_argument("--numeric", action="append", default=[])
    parser.add_argument("--timestamp")
    parser.add_argument("--time-format", help="strptime format; default: ISO 8601")
    parser.add_argument("--key", action="append", default=[], help="Repeat for a composite key")
    args = parser.parse_args(argv)
    if len(args.delimiter) != 1 or args.delimiter in "\r\n\0":
        parser.error("--delimiter must be one non-newline character")
    if args.time_format and not args.timestamp:
        parser.error("--time-format requires --timestamp")
    try:
        result = audit_csv(args.path, delimiter=args.delimiter, encoding=args.encoding,
                           required=args.required, numeric=args.numeric, timestamp=args.timestamp,
                           time_format=args.time_format, keys=args.key)
    except (OSError, UnicodeError, LookupError, ValueError, csv.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
