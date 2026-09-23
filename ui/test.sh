#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Includes loading, SVG references, scoped animation and reduced-motion checks.
exec "${NODE_BINARY:-node}" --test tests/ui.test.mjs tests/draft.test.mjs tests/design.test.mjs
