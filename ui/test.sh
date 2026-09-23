#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Includes loading, design restrictions and resolution of the station SVG references.
exec "${NODE_BINARY:-node}" --test tests/ui.test.mjs tests/draft.test.mjs tests/design.test.mjs
