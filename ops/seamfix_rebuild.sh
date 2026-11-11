#!/bin/sh
# AR rebuild after the seam.label_periodic fix (lab[r2, -1], not lab[r, -1]):
# every AR touching the prime meridian diagonally lost ALL its cells in
# ar_filter's track labels (Europe 2026-09-07/08 blank for 18 h), so the raw
# layer AND the events/viz assets both need to be rebuilt. Same isolated-tree
# rule as ops_arfix3/4: runs from /opt/ewb-arbuild (data/logs/.venv/third_party
# symlinked to /opt/ewb-live), md5 guard before and after every step, fail-fast.
# The two freshness timers are stopped by the caller and restored on success.
cd /opt/ewb-arbuild || exit 1
. .venv/bin/activate
export EWB_LIVE_INIT_PARALLEL="${EWB_LIVE_INIT_PARALLEL:-2}"
export EWB_LIVE_AR_JOBS="${EWB_LIVE_AR_JOBS:-3}"
WANT="efb9c755 a2077a5a 2db5edd2 167d392f fd59a911 "
guard() {
  have=$(md5sum ewb_live/events/seam.py ewb_live/events/ar_filter.py ewb_live/viz/exceedance.py \
              ewb_live/events/ar_mask.py ewb_live/events/ar.py | cut -c1-8 | tr '\n' ' ')
  echo "=== $(date -u +%FT%TZ) GUARD $have"
  [ "$have" = "$WANT" ] && return 0
  echo "=== ABORT: AR code is not what was deployed (got $have)"; exit 3
}
run() { guard || exit 3; echo "=== $(date -u +%FT%TZ) START $* ==="; "$@" || { echo "=== $(date -u +%FT%TZ) FAILED: $* ==="; exit 1; }; echo "=== $(date -u +%FT%TZ) OK : $* ==="; guard || exit 3; }
run python -m ewb_live.viz.exceedance --force --days 90
run python -m ewb_live.weekly --days 90
run python -m ewb_live.viz --all --force --types atmospheric_river
run python scripts/build_region_scores.py data/results
echo "=== $(date -u +%FT%TZ) ALL DONE ==="
systemctl start ewb-live-freshness.timer ewb-refresh.timer && echo "timers restored"
