"""Real NOAA GFS archives, with per-object evidence against look-ahead bias."""
import hashlib
import json
import math
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

UTC = timezone.utc
BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
COORDINATES = {"turbine_1": (43.645150, 78.535604), "turbine_2": (43.643198, 78.538828)}
FIELDS = {"u": ("UGRD", "100 m above ground", 2, 2, 100, "m s**-1"),
          "v": ("VGRD", "100 m above ground", 2, 3, 100, "m s**-1"),
          "t": ("TMP", "2 m above ground", 0, 0, 2, "K")}


def utc_text(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_as_of(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    value = value.astimezone(UTC)
    if value.minute or value.second or value.microsecond:
        raise ValueError("as_of must be a whole UTC hour")
    return value


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def choose_run(as_of):
    candidate = parse_as_of(as_of) - timedelta(hours=6)
    return candidate.replace(hour=candidate.hour // 6 * 6)


def required_steps(as_of, run, horizon_hours):
    if type(horizon_hours) is not int or not 24 <= horizon_hours <= 48:
        raise ValueError("horizon_hours must be an integer from 24 to 48")
    lead = int((as_of - run).total_seconds() // 3600)
    first, last = lead // 3 * 3, (lead + horizon_hours + 2) // 3 * 3
    if first < 0 or last > 120:
        raise ValueError("Requested forecast is outside supported GFS lead times")
    return list(range(first, last + 1, 3))


def check_available(headers, run, as_of):
    try:
        stamp = parsedate_to_datetime(headers["Last-Modified"]).astimezone(UTC)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Missing or invalid S3 Last-Modified") from exc
    if not run <= stamp <= as_of:
        raise ValueError(f"Archive object unavailable at cutoff: {utc_text(stamp)}")
    if not headers.get("ETag"):
        raise ValueError("Archive response has no ETag")
    return {"last_modified": utc_text(stamp), "etag": headers["ETag"]}


def tls_context():
    # Some python.org macOS installations have no default certificate bundle.
    # Use the OS trust bundle in that case; certificate checking remains enabled.
    if ssl.get_default_verify_paths().cafile is None and Path("/etc/ssl/cert.pem").is_file():
        return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ssl.create_default_context()


def get_bytes(url, byte_range=None):
    headers = {"User-Agent": "HackAlem-Wind-Data/1.0"}
    if byte_range:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
    limit = byte_range[1] - byte_range[0] + 1 if byte_range else 1024 * 1024
    if limit > 5 * 1024 * 1024:
        raise ValueError("Unexpectedly large GRIB field")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                        timeout=30, context=tls_context()) as response:
                if byte_range:
                    expected = f"bytes {byte_range[0]}-{byte_range[1]}/"
                    if response.status != 206 or not response.headers.get("Content-Range", "").startswith(expected):
                        raise ValueError("Server did not honour the requested byte range")
                payload = response.read(limit + 1)
                if len(payload) > limit or (byte_range and len(payload) != limit):
                    raise ValueError("Archive response length mismatch")
                return payload, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)
    raise RuntimeError("Archive download retries exhausted")


def field_ranges(index, run, step):
    records = [line.split(":") for line in index.splitlines() if line]
    offsets = [int(row[1]) for row in records]
    if offsets != sorted(set(offsets)):
        raise ValueError("Non-increasing or duplicate GRIB index offsets")
    found = {}
    for key, (name, level, *_rest) in FIELDS.items():
        matches = [i for i, row in enumerate(records)
                   if len(row) >= 7 and row[3] == name and row[4] == level]
        if len(matches) != 1 or matches[0] == len(records) - 1:
            raise ValueError("Missing or ambiguous GRIB field: " + key)
        i = matches[0]
        row = records[i]
        if row[2] != run.strftime("d=%Y%m%d%H") or row[5] != f"{step} hour fcst":
            raise ValueError("GRIB index has wrong run or forecast step")
        found[key] = (offsets[i], offsets[i + 1] - 1)
    return found


def decode_field(payload, field, run, step):
    from eccodes import codes_new_from_message, codes_get, codes_grib_find_nearest, codes_release

    if payload[:4] != b"GRIB" or payload[-4:] != b"7777" or len(payload) < 20:
        raise ValueError("Invalid GRIB envelope")
    if payload[7] != 2 or int.from_bytes(payload[8:16], "big") != len(payload):
        raise ValueError("GRIB edition or encoded length mismatch")
    handle = codes_new_from_message(payload)
    try:
        _, _, category, number, height, units = FIELDS[field]
        valid = run + timedelta(hours=step)
        expected = {"discipline": 0, "parameterCategory": category, "parameterNumber": number,
                    "typeOfLevel": "heightAboveGround", "level": height, "units": units,
                    "dataDate": int(run.strftime("%Y%m%d")), "dataTime": run.hour * 100,
                    "validityDate": int(valid.strftime("%Y%m%d")), "validityTime": valid.hour * 100,
                    "stepType": "instant", "stepUnits": 1, "endStep": step,
                    "gridType": "regular_ll", "iDirectionIncrementInDegrees": 0.5,
                    "jDirectionIncrementInDegrees": 0.5}
        for key, value in expected.items():
            if codes_get(handle, key) != value:
                raise ValueError(f"Unexpected GRIB {key}: {codes_get(handle, key)!r}; expected {value!r}")
        result = {}
        missing = codes_get(handle, "missingValue")
        for turbine, (lat, lon) in COORDINATES.items():
            point = codes_grib_find_nearest(handle, lat, lon, npoints=1)[0]
            value = float(point["value"])
            if not math.isfinite(value) or value == missing:
                raise ValueError("Missing/non-finite grid value")
            if field == "t":
                value -= 273.15
            result[turbine] = {"value": value, "latitude": float(point["lat"]),
                               "longitude": float(point["lon"]), "distance_km": float(point["distance"])}
        return result
    finally:
        codes_release(handle)


def fetch_step(run, step, as_of):
    url = f"{BUCKET}/gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p50.f{step:03d}"
    index, headers = get_bytes(url + ".idx")
    evidence = [{"url": url + ".idx", **check_available(headers, run, as_of),
                 "sha256": hashlib.sha256(index).hexdigest()}]
    ranges = field_ranges(index.decode("ascii"), run, step)
    values, versions = {}, set()
    for field, span in ranges.items():
        payload, headers = get_bytes(url, span)
        metadata = check_available(headers, run, as_of)
        versions.add((metadata["etag"], metadata["last_modified"]))
        values[field] = decode_field(payload, field, run, step)
        evidence.append({"url": url, "field": field, "byte_range": list(span), **metadata,
                         "sha256": hashlib.sha256(payload).hexdigest()})
    if len(versions) != 1:
        raise ValueError("GFS object changed between field downloads")
    for turbine in COORDINATES:
        nodes = {(v[turbine]["latitude"], v[turbine]["longitude"]) for v in values.values()}
        if len(nodes) != 1:
            raise ValueError("GRIB fields use different spatial nodes")
    return {"step": step, "values": values, "objects": evidence}


def hourly_points(samples, run, as_of, horizon_hours):
    by_step = {s["step"]: s["values"] for s in samples}
    if len(by_step) != len(samples):
        raise ValueError("Duplicate forecast step")

    def at(turbine, field, hour):
        low = hour // 3 * 3
        high = low if hour == low else low + 3
        a = by_step[low][field][turbine]["value"]
        b = by_step[high][field][turbine]["value"]
        if any(type(v) not in (float, int) or not math.isfinite(v) for v in (a, b)):
            raise ValueError("Invalid forecast number")
        return a if low == high else a + (b - a) * (hour - low) / 3

    lead = int((as_of - run).total_seconds() // 3600)
    points = []
    for turbine in COORDINATES:
        for offset in range(1, horizon_hours + 1):
            a, b = lead + offset - 1, lead + offset
            speeds = [math.hypot(at(turbine, "u", h), at(turbine, "v", h)) for h in (a, b)]
            points.append({"turbine_id": turbine, "valid_at": utc_text(as_of + timedelta(hours=offset)),
                           "wind_speed_ms": sum(speeds) / 2,
                           "temperature_c": (at(turbine, "t", a) + at(turbine, "t", b)) / 2})
    return points


def build_archive(as_of, horizon_hours=48, workers=4):
    as_of = parse_as_of(as_of)
    run = choose_run(as_of)
    steps = required_steps(as_of, run, horizon_hours)
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError("workers must be from 1 to 8")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        samples = list(pool.map(lambda step: fetch_step(run, step, as_of), steps))
    available = max(obj["last_modified"] for sample in samples for obj in sample["objects"])
    provenance = {"schema": "noaa-gfs-provenance-v1", "as_of": utc_text(as_of),
                  "issued_at": utc_text(run), "available_at": available, "horizon_hours": horizon_hours,
                  "retrieved_at": utc_text(datetime.now(UTC)), "grid_degrees": 0.5,
                  "wind_height_m": 100, "temperature_height_m": 2,
                  "coordinates": COORDINATES, "spatial_method": "nearest grid node",
                  "temporal_method": "linear U/V/T at hour boundaries; mean of boundary speed/temperature",
                  "samples": samples}
    weather = {"source": "NOAA GFS 0.5 degree operational archive (AWS)",
               "run_id": f"gfs.{run:%Y%m%d}/{run:%H}", "issued_at": utc_text(run),
               "available_at": available, "wind_height_m": 100,
               "availability_evidence": (
                   "All NOAA public S3 GRIB/index Last-Modified values satisfy issued_at <= timestamp <= as_of; "
                   "available_at is their maximum. Evidence records current object version existence, not historical access logs. "
                   "Provenance SHA-256=" + digest(provenance) + "; https://registry.opendata.aws/noaa-gfs-bdp-pds/"),
               "points": hourly_points(samples, run, as_of, horizon_hours)}
    return weather, provenance


def verify_archive(weather, provenance):
    """Recompute the batch from its extracted source values and audit metadata."""
    as_of = parse_as_of(provenance["as_of"])
    run = parse_as_of(provenance["issued_at"])
    if provenance["schema"] != "noaa-gfs-provenance-v1" or run != choose_run(as_of):
        raise ValueError("Archive source schema/run mismatch")
    expected_steps = required_steps(as_of, run, provenance["horizon_hours"])
    if [s["step"] for s in provenance["samples"]] != expected_steps:
        raise ValueError("Incomplete or unordered archive steps")
    available = []
    for sample in provenance["samples"]:
        if len(sample["objects"]) != 4:
            raise ValueError("Incomplete source evidence")
        for obj in sample["objects"]:
            stamp = datetime.fromisoformat(obj["last_modified"].replace("Z", "+00:00"))
            if not run <= stamp <= as_of or not obj["url"].startswith(BUCKET + "/") or not obj["etag"]:
                raise ValueError("Invalid archive availability evidence")
            if len(obj["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in obj["sha256"]):
                raise ValueError("Invalid source checksum")
            available.append(obj["last_modified"])
    if weather["available_at"] != max(available) or provenance["available_at"] != max(available):
        raise ValueError("Availability does not match evidence")
    if weather["issued_at"] != utc_text(run) or weather["run_id"] != f"gfs.{run:%Y%m%d}/{run:%H}" or weather["wind_height_m"] != 100:
        raise ValueError("Weather metadata mismatch")
    if "SHA-256=" + digest(provenance) + ";" not in weather["availability_evidence"]:
        raise ValueError("Provenance checksum mismatch")
    if weather["points"] != hourly_points(provenance["samples"], run, as_of, provenance["horizon_hours"]):
        raise ValueError("Weather points do not match source extraction")
    return True
