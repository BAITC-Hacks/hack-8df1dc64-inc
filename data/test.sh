#!/usr/bin/env bash
# Real Data-module checks; temporary synthetic test inputs only.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -p 'test_*.py' -v
