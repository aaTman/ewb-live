#!/bin/sh
# AR rule rebuild (owner 2026-11-10, notes/ar_outage_20261110.md).
# Code deployed 21:51Z from worktree bug-catcher (backup: backups/pre-arfix-20261110T215123Z):
#   LAYER_REV v9, .ivt from the IVT FIELD (schema 2), step-level TC screen,
#   link caps (900 km / 40 steps), centroid tropical rule, seam-periodic mask
#   (events/ar_mask), wide AR compute window (viz/build), per-day AR footprints.
# Order: the map first (the owner's report is about the map), then events +
# scoring + AR assets + region scores. Mirror frozen: ewb-refresh / freshness
# timers stay stopped until the end.
cd /opt/ewb-live || exit 1
. .venv/bin/activate
export EWB_LIVE_INIT_PARALLEL="${EWB_LIVE_INIT_PARALLEL:-2}"
export EWB_LIVE_AR_JOBS="${EWB_LIVE_AR_JOBS:-3}"
run() { echo "=== $(date -u +%FT%TZ) START $* ==="; "$@"; echo "=== $(date -u +%FT%TZ) EXIT $? : $* ==="; }
run python -m ewb_live.viz.exceedance --force --days 90
run python -m ewb_live.weekly --days 90
run python -m ewb_live.viz --all --force --types atmospheric_river
run python scripts/build_region_scores.py data/results
echo "=== $(date -u +%FT%TZ) ALL DONE ==="
systemctl start ewb-live-freshness.timer ewb-refresh.timer && echo "timers restored"
