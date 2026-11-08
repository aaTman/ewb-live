#!/usr/bin/env bash
# One-command browser e2e: the whole Playwright suite on Chromium, Firefox and
# WebKit against the deployed dashboard.
#
#   scripts/e2e.sh                         # all three engines, all tests
#   scripts/e2e.sh -k axe                  # extra args go to pytest
#   EWB_E2E_BROWSERS=chromium scripts/e2e.sh     # subset (comma list)
#   EWB_LIVE_URL=https://example.org scripts/e2e.sh   # another deployment
#
# First time on a fresh machine:  .venv/bin/playwright install chromium firefox webkit
# (WebKit also needs distro libs: `sudo playwright install-deps webkit`, or
# scripts/e2e_webkit_libs.sh when you have no root).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
export EWB_E2E_BROWSERS="${EWB_E2E_BROWSERS:-all}"
LIBS="$HERE/.e2e-webkit-libs/root/usr/lib/x86_64-linux-gnu"
if [ -d "$LIBS" ]; then
  export EWB_EXTRA_LD="$LIBS" PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1
fi
PY="${PYTHON:-$HERE/.venv/bin/python}"
exec "$PY" -m pytest -m e2e tests/e2e -p no:cacheprovider -q "$@"
