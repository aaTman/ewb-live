#!/bin/sh
# Combined rebuild after the 2026-11-11 merge (AR seam fix + footprint/core
# heat/freeze tracker): events + scoring + viz in ONE weekly run. The raw layer
# (v9, .ivt schema 2) was rebuilt by seamfix_rebuild.sh step 1 and is not redone.
# From the ISOLATED tree /opt/ewb-arbuild (data/logs/.venv/third_party symlinked
# to /opt/ewb-live) so a lane's whole-tree sync cannot swap code under the run;
# md5 guard before and after every step, fail-fast. The caller stops the two
# freshness timers; they are restored on success.
cd /opt/ewb-arbuild || exit 1
. .venv/bin/activate
export EWB_LIVE_INIT_PARALLEL="${EWB_LIVE_INIT_PARALLEL:-2}"
export EWB_LIVE_AR_JOBS="${EWB_LIVE_AR_JOBS:-3}"
WANT="efb9c755 a2077a5a 2db5edd2 167d392f fd59a911 5287ee30 f97aec34 "
guard() {
  have=$(md5sum ewb_live/events/seam.py ewb_live/events/ar_filter.py ewb_live/viz/exceedance.py \
              ewb_live/events/ar_mask.py ewb_live/events/ar.py ewb_live/events/temperature.py \
              ewb_live/events/event_tracker.py | cut -c1-8 | tr '\n' ' ')
  echo "=== $(date -u +%FT%TZ) GUARD $have"
  [ "$have" = "$WANT" ] && return 0
  echo "=== ABORT: code is not what was deployed (got $have)"; exit 3
}
run() { guard || exit 3; echo "=== $(date -u +%FT%TZ) START $* ==="; "$@" || { echo "=== $(date -u +%FT%TZ) FAILED: $* ==="; exit 1; }; echo "=== $(date -u +%FT%TZ) OK : $* ==="; guard || exit 3; }
run python -m ewb_live.weekly --days 90
run python -m ewb_live.viz --all --force --types atmospheric_river
run python scripts/build_region_scores.py data/results
echo "=== $(date -u +%FT%TZ) ALL DONE ==="
systemctl start ewb-live-freshness.timer ewb-refresh.timer && echo "timers restored"
