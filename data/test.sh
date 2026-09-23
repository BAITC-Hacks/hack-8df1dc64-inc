#!/usr/bin/env bash
# History, NOAA decoding/availability rules and all bundled real archive checks.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s data/tests -p 'test_*.py' -v
