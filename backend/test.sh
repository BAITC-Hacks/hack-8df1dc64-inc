#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
python_command="${PYTHON_BIN:-}"
if [[ -z "$python_command" ]]; then
  for candidate in python python3; do
    if command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
      python_command="$candidate"
      break
    fi
  done
fi
if [[ -z "$python_command" ]]; then
  echo "Python 3.11 or newer is required; set PYTHON_BIN to its executable." >&2
  exit 1
fi
if ! "$python_command" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "PYTHON_BIN must point to a working Python 3.11 or newer." >&2
  exit 1
fi

# B1-B5: validation, calculation, HTTP, analysis, NOAA workflow and cache checks.
# Offline gate: real paid OpenAI checks are explicit via backend.analyze_weather.
"$python_command" -m unittest discover -s backend/tests -t . -v
