"""CLI for validating the supplied sources and preparing hourly history."""
import argparse
import csv
import json
import sys
from datetime import datetime
from itertools import chain
from pathlib import Path

from data.history import RAW, hourly_history, iter_observations, source_report, verify_sources


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "hourly"))
    parser.add_argument("--raw-dir", type=Path, default=RAW)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--as-of")
    parser.add_argument("--timestamp-role", choices=("start", "end"))
    args = parser.parse_args(argv)
    if args.command == "hourly" and (not args.as_of or not args.timestamp_role):
        parser.error("hourly requires --as-of and --timestamp-role; interval semantics must be explicit")
    try:
        manifest = verify_sources(args.raw_dir)
        if args.output and (args.output.resolve() == args.raw_dir.resolve() or args.raw_dir.resolve() in args.output.resolve().parents):
            raise ValueError("Output must not overwrite raw source files")
        if args.command == "audit":
            result = {"source_manifest": manifest, "sources": [source_report(args.raw_dir / e["path"], e["turbine_id"]) for e in manifest["files"]]}
        else:
            rows = chain.from_iterable(iter_observations(args.raw_dir / e["path"], e["turbine_id"]) for e in manifest["files"])
            result = hourly_history(rows, datetime.fromisoformat(args.as_of), args.timestamp_role)
            result.update({"as_of": args.as_of, "timestamp_role": args.timestamp_role,
                           "timestamp_role_confirmed": False, "source_timezone": "UTC+05:00",
                           "source_hashes": {e["turbine_id"]: e["sha256_uncompressed"] for e in manifest["files"]}})
        payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            # Exclusive creation prevents silently replacing an existing run or source.
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(payload)
            print(json.dumps({"output": str(args.output), "points": len(result.get("points", []))}))
        else:
            print(payload, end="")
    except (OSError, ValueError, KeyError, csv.Error, EOFError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
