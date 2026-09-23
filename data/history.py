"""Organizer history adapter. Weather forecasts and power models live elsewhere."""
import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE_TZ = timezone(timedelta(hours=5))
UTC = timezone.utc
TEN_MINUTES = timedelta(minutes=10)
TEST_START = datetime(2026, 2, 1, tzinfo=SOURCE_TZ)
RAW = Path(__file__).resolve().parent / "raw"
COLUMNS = ["ID", "Статистическое время", "Средняя скорость ветра(m/s)",
           "Нормализованная активная мощность", "Средняя температура окружающей среды(°C)"]
TURBINES = ("turbine_1", "turbine_2")


def iter_observations(path, turbine_id):
    """Yield validated observations in source order, retaining source provenance."""
    if turbine_id not in TURBINES:
        raise ValueError("Unknown turbine_id")
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    ids, previous = set(), None
    with opener(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        if next(reader, None) != COLUMNS:
            raise ValueError("Source columns do not match the organizer CSV")
        for row in reader:
            prefix = f"{turbine_id}: CSV line {reader.line_num}: "
            if len(row) != len(COLUMNS) or any(not v.strip() for v in row):
                raise ValueError(prefix + "missing cells or wrong column count")
            if row[0] in ids:
                raise ValueError(prefix + "duplicate source ID")
            ids.add(row[0])
            try:
                stamp = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=SOURCE_TZ)
                values = [float(v) for v in row[2:]]
            except ValueError as exc:
                raise ValueError(prefix + "invalid date or numeric value") from exc
            if stamp.minute % 10 or stamp.second:
                raise ValueError(prefix + "timestamp is not on the ten-minute grid")
            if previous is not None and stamp <= previous:
                raise ValueError(prefix + "duplicate or out-of-order timestamp")
            if not all(math.isfinite(v) for v in values) or values[0] < 0:
                raise ValueError(prefix + "non-finite number or negative wind speed")
            previous = stamp
            yield {"turbine_id": turbine_id, "source_id": row[0], "source_time": row[1],
                   "timestamp": stamp.astimezone(UTC), "wind_speed_ms": values[0],
                   "normalized_power": values[1], "temperature_c": values[2]}
        if previous is None:
            raise ValueError("Source CSV contains no observations")


def verify_sources(raw_dir=RAW):
    """Check original byte hashes before using the bundled organizer data."""
    raw_dir = Path(raw_dir)
    manifest = json.loads((raw_dir / "manifest.json").read_text())
    if {f["turbine_id"] for f in manifest["files"]} != set(TURBINES) or len(manifest["files"]) != 2:
        raise ValueError("Manifest must list the two turbines exactly once")
    for entry in manifest["files"]:
        if Path(entry["path"]).name != entry["path"]:
            raise ValueError("Manifest source must be a basename")
        payload = gzip.decompress((raw_dir / entry["path"]).read_bytes())
        if len(payload) != entry["bytes_uncompressed"] or hashlib.sha256(payload).hexdigest() != entry["sha256_uncompressed"]:
            raise ValueError("Source checksum mismatch: " + entry["turbine_id"])
    return manifest


def source_report(path, turbine_id):
    rows = list(iter_observations(path, turbine_id))
    stamps = [r["timestamp"] for r in rows]
    gaps = []
    for a, b in zip(stamps, stamps[1:]):
        missed = int((b - a) / TEN_MINUTES) - 1
        if missed:
            gaps.append({"after": a.astimezone(SOURCE_TZ).isoformat(),
                         "before": b.astimezone(SOURCE_TZ).isoformat(), "missing_slots": missed})
    return {
        "turbine_id": turbine_id, "rows": len(rows),
        "first_source_time": rows[0]["source_time"], "last_source_time": rows[-1]["source_time"],
        "timezone": "UTC+05:00", "timestamp_role": "unconfirmed",
        "expected_slots_between_endpoints": int((stamps[-1] - stamps[0]) / TEN_MINUTES) + 1,
        "missing_slots": sum(g["missing_slots"] for g in gaps), "gaps": gaps,
        "rows_by_month": dict(sorted(Counter(t.astimezone(SOURCE_TZ).strftime("%Y-%m") for t in stamps).items())),
        "test_period_rows": sum(TEST_START <= t.astimezone(SOURCE_TZ) < datetime(2026, 3, 1, tzinfo=SOURCE_TZ) for t in stamps),
        "values": {key: {"min": min(r[key] for r in rows), "max": max(r[key] for r in rows)}
                   for key in ("wind_speed_ms", "normalized_power", "temperature_c")},
        "power_outside_0_1": sum(not 0 <= r["normalized_power"] <= 1 for r in rows),
    }


def hourly_history(observations, as_of, timestamp_role):
    """Aggregate complete hours; caller must explicitly choose interval semantics."""
    if timestamp_role not in ("start", "end"):
        raise ValueError("timestamp_role must explicitly be start or end; source semantics are unconfirmed")
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    as_of = as_of.astimezone(UTC)
    if as_of.minute or as_of.second or as_of.microsecond:
        raise ValueError("as_of must be a whole UTC hour")
    groups, seen = defaultdict(dict), set()
    after_cutoff = test_rows = 0
    for row in observations:
        stamp = row["timestamp"]
        if stamp.utcoffset() is None:
            raise ValueError("Observation timestamp must have a timezone")
        stamp = stamp.astimezone(UTC)
        if row["turbine_id"] not in TURBINES or stamp.minute % 10 or stamp.second or stamp.microsecond:
            raise ValueError("Invalid observation turbine or time grid")
        key = (row["turbine_id"], stamp)
        if key in seen:
            raise ValueError("Duplicate observation")
        seen.add(key)
        for name in ("wind_speed_ms", "normalized_power", "temperature_c"):
            value = row[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Invalid observation number")
        if row["wind_speed_ms"] < 0:
            raise ValueError("Negative wind speed")
        if stamp >= TEST_START:
            test_rows += 1
            continue
        end = stamp + TEN_MINUTES if timestamp_role == "start" else stamp
        if end > as_of:
            after_cutoff += 1
            continue
        hour = end.replace(minute=0, second=0, microsecond=0)
        if end != hour:
            hour += timedelta(hours=1)
        groups[(row["turbine_id"], hour)][end] = row
    points, incomplete = [], 0
    for (turbine, hour), samples in sorted(groups.items()):
        expected = {hour - TEN_MINUTES * i for i in range(6)}
        if set(samples) != expected:
            incomplete += 1
            continue
        point = {"turbine_id": turbine, "valid_at": hour.isoformat().replace("+00:00", "Z"), "sample_count": 6}
        for name in ("wind_speed_ms", "normalized_power", "temperature_c"):
            point[name] = math.fsum(r[name] / 6 for r in samples.values())
        points.append(point)
    if not points:
        raise ValueError("No complete historical hours are available at this cutoff")
    return {"points": points, "excluded_incomplete_hours": incomplete,
            "excluded_after_cutoff": after_cutoff, "excluded_test_rows": test_rows}
