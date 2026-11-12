#!/usr/bin/env bash
# Public leak gate: FAIL if anything the public site can serve carries
# private-infrastructure strings (owner rule 2026-11-08, notes/infra_domain.md
# "Public scrub"). Two modes:
#
#   scripts/public_scrub_check.sh            scan the files serve.py exposes
#                                            (dashboard/ + data/results/ trees)
#   scripts/public_scrub_check.sh https://ewb.espr.ai   crawl the live site at
#                                            the same file list, and verify
#                                            non-JSON top-level results files
#                                            are NOT served (serve.py JSON-only rule)
#
# Wired into: ops/install.sh (deploy gate), ewb_live/weekly.py (after publish),
# tests/test_public_scrub.py.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATTERNS='192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|(^|[^0-9.])10\.[0-9]+\.[0-9]+\.[0-9]+([^0-9]|$)|127\.0\.0\.1|localhost:[0-9]|:8050|:8080|:8479|--port|python -m|ewb_live\.serve|/home/orca|/opt/ewb|/root/\.venv|example-host|[Pp]roxmox|\borca\b|/opt/ewb-owb|ewb-live-web|ewb-live-weekly|ewb-live-store|remote\.sh|ssh root@|EWB_LIVE_SKIP|notes/ewb\.md|notes/channel|\bLXC\b|\bCT[0-9]+\b|16 cores|128 GB RAM|peak_total_rss_gb|"perf":|peak_rss|cpu_count|load_avg'

fail=0
scan() { # label  root-subdir
  local label=$1 hit
  # -I skips binaries (png/parquet); we only care about textual content
  if hit=$(grep -rInE "$PATTERNS" --binary-files=without-match "$2" 2>/dev/null | grep -v "^$"); then
    echo "LEAK in $label:"; echo "$hit"; fail=1  # no cap: weekly.py quarantines files parsed from this output (ponytail 2026-11-08)
  fi
}

if [[ "${1:-}" == http* ]]; then
  BASE="${1%/}"
  echo "URL mode: crawling $BASE"
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  # every file serve.py maps: dashboard/** -> /, data/results top-level json -> /data/results/
  while IFS= read -r f; do
    url="$BASE/$(realpath --relative-to="$HERE/dashboard" "$f")"
    code=$(curl -s -o "$tmp/f" -w '%{http_code}' -m 20 "$url" || echo 000)
    case $code in
      200) if grep -qE "$PATTERNS" "$tmp/f"; then grep -InE "$PATTERNS" --label="$url" "$tmp/f" | head -5; fail=1; fi ;;
      404|204|403) : ;;                       # simply not served — fine
      *) echo "FETCH FAIL($code): $url"; fail=1 ;;
    esac
  done < <(find "$HERE/dashboard" -type f)
  # nested allowed shapes: any .json under data/results (top-level, mini dirs, events/**, history/**)
  while IFS= read -r f; do
    url="$BASE/data/results/$(realpath --relative-to="$HERE/data/results" "$f")"
    code=$(curl -s -o "$tmp/f" -w '%{http_code}' -m 20 "$url" || echo 000)
    case $code in
      200) if grep -qE "$PATTERNS" "$tmp/f"; then grep -InE "$PATTERNS" --label="$url" "$tmp/f" | head -5; fail=1; fi ;;
      404|204|403) : ;;
      *) echo "FETCH FAIL($code): $url"; fail=1 ;;
    esac
  done < <(find "$HERE/data/results" -type f -name '*.json')
  # the JSON-only rule: top-level non-.json results files must NOT be served
  for f in "$HERE"/data/results/*; do
    [[ -f $f && ${f##*.} != json ]] || continue
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 20 "$BASE/data/results/${f##*/}")
    [[ $code == 200 || $code == 3* ]] && { echo "LEAK ROUTE: /data/results/${f##*/} served (HTTP $code)"; fail=1; }
  done
else
  echo "tree mode: scanning $(realpath --relative-to="$HERE" "${1:-$HERE}")"
  root="${1:-$HERE}"
  scan "dashboard/" "$root/dashboard"
  scan "data/results/" "$root/data/results"
fi

if [ "$fail" = 0 ]; then echo "public_scrub_check: CLEAN"; fi
exit "$fail"
