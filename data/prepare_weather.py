"""Download daily historical GFS weather batches for the backend archive directory."""
import argparse
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from data.weather_gfs import build_archive, parse_as_of, verify_archive


def write_archive(directory, weather, provenance):
    verify_archive(weather, provenance)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stem = parse_as_of(provenance["as_of"]).strftime("%Y%m%dT%H%M%SZ")
    target = directory / (stem + ".json")
    proof = directory / (stem + ".provenance.json")
    if target.exists() or proof.exists():
        raise FileExistsError("Archive output already exists: " + stem)
    # Publish complete files without overwriting an existing run. B1 is last.
    for path, value in ((proof, provenance), (target, weather)):
        payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".weather-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        try:
            os.link(temporary, path)
        finally:
            temporary.unlink()
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--as-of")
    group.add_argument("--start-as-of")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--horizon-hours", type=int, default=48)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.days <= 31 or (args.as_of and args.days != 1):
        parser.error("days must be 1..31 and requires --start-as-of for multiple days")
    try:
        first = parse_as_of(args.as_of or args.start_as_of)
        for day in range(args.days):
            as_of = first + timedelta(days=day)
            stem = as_of.strftime("%Y%m%dT%H%M%SZ")
            target = args.output_dir / (stem + ".json")
            proof = args.output_dir / (stem + ".provenance.json")
            if target.exists() or proof.exists():
                weather, provenance = json.loads(target.read_text()), json.loads(proof.read_text())
                verify_archive(weather, provenance)
                if provenance["horizon_hours"] != args.horizon_hours or parse_as_of(provenance["as_of"]) != as_of:
                    raise ValueError("Existing archive belongs to a different request")
                print(json.dumps({"verified_existing": str(target)}), flush=True)
                continue
            print(json.dumps({"downloading": stem}), flush=True)
            weather, provenance = build_archive(as_of, args.horizon_hours, args.workers)
            write_archive(args.output_dir, weather, provenance)
            print(json.dumps({"output": str(target), "points": len(weather["points"]),
                              "run": weather["run_id"], "available_at": weather["available_at"]}), flush=True)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
