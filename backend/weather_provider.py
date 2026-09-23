"""Verified NOAA delivery and immutable local versions for the agent workflow."""

import os
from pathlib import Path
from threading import Lock

from backend.config import ROOT
from backend.service import strict_json
from backend.validation import ForecastValidationError
from data.weather_gfs import build_archive, digest, parse_as_of, canonical_bytes, verify_archive


class WeatherProvider:
    def __init__(self, archive_dir=None, cache_dir=None):
        self.archive_dir = Path(archive_dir or os.getenv("WEATHER_ARCHIVE_DIR") or ROOT / "data" / "weather")
        self.cache_dir = Path(cache_dir or os.getenv("WEATHER_CACHE_DIR") or ROOT / "backend" / "runtime" / "weather")
        self._lock = Lock()

    def _verify(self, weather, proof, request):
        try:
            verify_archive(weather, proof)
            if parse_as_of(proof["as_of"]) != request.as_of or proof["horizon_hours"] < request.horizon_hours:
                raise ValueError("Archive does not cover this request")
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            raise ForecastValidationError("INVALID_WEATHER", "weather.provenance", "Archive provenance or requested coverage is invalid.") from exc

    def _read(self, path, request, mode):
        proof_path = path.with_name(path.stem + ".provenance.json")
        try:
            weather = strict_json(path.read_text(encoding="utf-8"))
            proof = strict_json(proof_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, RecursionError) as exc:
            raise ForecastValidationError("INVALID_WEATHER", "weather.provenance", "Weather package or provenance is unreadable.") from exc
        self._verify(weather, proof, request)
        return weather, {"mode": mode, "provenance_sha256": digest(proof)}

    def resolve(self, request, refresh=False):
        stem = request.as_of.strftime("%Y%m%dT%H%M%SZ")
        folder = self.cache_dir / stem
        with self._lock:
            if not refresh:
                # New fetched versions take precedence over the original bundle.
                versions = sorted((p for p in folder.glob("*.json") if not p.name.endswith(".provenance.json")),
                                  key=lambda p: p.stat().st_mtime_ns, reverse=True)
                if versions:
                    return self._read(versions[0], request, "cache")
                bundled = self.archive_dir / (stem + ".json")
                if bundled.exists() or bundled.with_name(stem + ".provenance.json").exists():
                    return self._read(bundled, request, "bundled")
            try:
                # The bundled Windows ecCodes decoder can terminate the process
                # on concurrent initialization. Serialize GRIB decoding here.
                weather, proof = build_archive(request.as_of, 48, workers=1)
                self._verify(weather, proof, request)
                identity = digest(proof)
                folder.mkdir(parents=True, exist_ok=True)
                for name, value in ((identity + ".provenance.json", proof), (identity + ".json", weather)):
                    path = folder / name
                    raw = canonical_bytes(value)
                    if path.exists():
                        if path.read_bytes() != raw:
                            raise ValueError("Existing cache version differs")
                    else:
                        with path.open("xb") as stream:
                            stream.write(raw)
            except ForecastValidationError:
                raise
            except Exception as exc:
                # This boundary includes Data's network/GRIB/native-decoder failures.
                # Failure is explicit; no cached result is passed off as refreshed data.
                raise ForecastValidationError("WEATHER_UNAVAILABLE", "weather", "NOAA download, decoding or cache publication failed.") from exc
            return weather, {"mode": "downloaded", "provenance_sha256": identity}


def select_weather(weather, request):
    """Select a requested grid without hiding duplicate selected points."""
    from datetime import timedelta
    from backend.validation import _timestamp
    points = []
    for point in weather["points"]:
        stamp = _timestamp(point["valid_at"], "weather.points.valid_at", "INVALID_WEATHER", hourly=True)
        if point["turbine_id"] in request.turbine_ids and request.as_of < stamp <= request.as_of + timedelta(hours=request.horizon_hours):
            points.append(point)
    return {**weather, "points": points}
