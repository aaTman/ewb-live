#!/usr/bin/env bash
# /dev/ review site for EWB Live — a SECOND, isolated copy of the dashboard and
# its results on the ewb LXC, for the owner's visual review of unreleased work.
#
# Pattern reused from the two precedents on the box (see
# notes/channel_new_events.md [integrator] 2026-11-17): /opt/ewb-events-dev
# (dev/live pair, frontfinder era) and /opt/ewb-preview-{colors,nav,flood}
# (throwaway tree + own serve port). Code is isolated; heavy READ-ONLY inputs
# (ERA5 truth mirror, climatology, IMERG store, MLWP virtual store) are
# symlinked from the live tree so nothing is re-downloaded. Anything the run
# WRITES (results, per-window caches, IBTrACS archive, viz) is a real directory
# inside the dev tree — the live tree is never written.
#
#   ops/dev_site.sh sync     rsync THIS repo's code -> $DEV_DIR (run locally)
#   ops/dev_site.sh init     symlinks + data layout + seed results from live
#   ops/dev_site.sh lanes    detection -> per-event viz -> publish (background)
#   ops/dev_site.sh web      install + start ewb-live-dev-web.service ($DEV_PORT)
#   ops/dev_site.sh status   tree/unit/port/log tail
#   ops/dev_site.sh stop     stop the dev server (leaves the tree in place)
#   ops/dev_site.sh url      the URL the owner opens
#
# Env: EWB_DEV_DIR (default /opt/ewb-live-dev), EWB_DEV_PORT (default 8051),
#      EWB_DEV_HOST (default the box's LAN address; the Cloudflare tunnel has no
#      credentials on this box, so the public entry is dark — a LAN port is the
#      honest equivalent of the dev hostname until the owner authorises it).
#
# HARD RULES enforced here: every remote write path is under $DEV_DIR; the live
# tree is only ever read (rsync without --delete FROM live, symlink targets).
set -uo pipefail

BOX="${EWB_DEV_BOX:-root@192.168.0.121}"
DEV="${EWB_DEV_DIR:-/opt/ewb-live-dev}"
LIVE=/opt/ewb-live
PORT="${EWB_DEV_PORT:-8051}"
HOST="${EWB_DEV_HOST:-192.168.0.121}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=ewb-live-dev-web
r() { ssh -o ConnectTimeout=10 "$BOX" "$@"; }
case "${1:-}" in
  sync)
    r "mkdir -p $DEV"
    rsync -az --delete --delete-excluded \
      --exclude .git --exclude .venv --exclude data --exclude logs \
      --exclude third_party --exclude __pycache__ --exclude .pytest_cache \
      --exclude '*.pyc' --exclude tests/e2e/__pycache__ \
      "$REPO/" "$BOX:$DEV/" && echo "synced code -> $DEV"
    ;;
  init)
    r "set -e
       mkdir -p $DEV/data/cache $DEV/logs
       [ -e $DEV/.venv ] || ln -s $LIVE/.venv $DEV/.venv
       [ -e $DEV/third_party ] || ln -s $LIVE/third_party $DEV/third_party
       # read-only inputs: symlink, never copy (26 G clim + 45 G truth + stores)
       for d in clim imerg icechunk; do
         [ -e $DEV/data/\$d ] || ln -s $LIVE/data/\$d $DEV/data/\$d
       done
       for d in truth fc_parts mlwp; do
         [ -e $DEV/data/cache/\$d ] || ln -s $LIVE/data/cache/\$d $DEV/data/cache/\$d
       done
       # small cache metadata: COPY (the lanes write into data/cache/*)
       for f in truth_cutoff.json aiwp_inits.json; do
         [ -e $DEV/data/cache/\$f ] || cp -n $LIVE/data/cache/\$f $DEV/data/cache/ 2>/dev/null || true
       done
       # everything the run writes is a REAL dir here, seeded from live so the
       # dev site shows the existing scorecard/history instead of empty tabs
       mkdir -p $DEV/data/cache/{events,ibtracs,viz,ewb_mid,score_parts}
       if [ ! -f $DEV/data/results/meta.json ]; then
         mkdir -p $DEV/data/results
         rsync -a $LIVE/data/results/ $DEV/data/results/
         echo seeded results from live (copy; live untouched)
       fi
       df -h / | tail -1
       du -sh $DEV/data/results"
    ;;
  lanes)
    # detection (all lanes) -> per-event viz + timeline + raw frames ->
    # publish scorecard/meta from the copied score rows (no new scoring)
    r "cd $DEV && nohup sh -c '
        set -x
        . .venv/bin/activate
        export EWB_DATA_PREP_DIR=$DEV/third_party/ExtremeWeatherBench/data_prep
        echo \"=== \$(date -u +%FT%TZ) detect\"
        python -m ewb_live.pipeline --events-only --days 90 --top-n 15
        echo \"=== \$(date -u +%FT%TZ) viz\"
        python -m ewb_live.viz --all --force --types heat_wave,freeze,cold_snap,atmospheric_river
        echo \"=== \$(date -u +%FT%TZ) publish\"
        python -m ewb_live.pipeline --publish-only --days 90
        echo \"=== \$(date -u +%FT%TZ) done\"
       ' > logs/lanes.log 2>&1 &
       echo started; sleep 2; tail -3 $DEV/logs/lanes.log"
    ;;
  web)
    r "cat > /etc/systemd/system/$UNIT.service <<EOF
[Unit]
Description=EWB Live DEV dashboard (review site, $DEV)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DEV
ExecStart=$LIVE/.venv/bin/python -m ewb_live.serve --port $PORT --host 0.0.0.0
Restart=always
RestartSec=3
StandardOutput=append:$DEV/logs/web.log
StandardError=append:$DEV/logs/web.log

[Install]
WantedBy=multi-user.target
EOF
       systemctl daemon-reload && systemctl enable --now $UNIT && sleep 1 &&
       systemctl is-active $UNIT && curl -s -o /dev/null -w 'http %{http_code} on :$PORT\n' http://127.0.0.1:$PORT/"
    ;;
  status)
    r "systemctl is-active $UNIT 2>/dev/null; ss -ltnp | grep :$PORT || echo 'nothing on :$PORT';
       tail -5 $DEV/logs/lanes.log 2>/dev/null;
       cd $DEV && .venv/bin/python ops/dev_site_report.py 2>&1 | tail -8; df -h / | tail -1"
    ;;
  stop) r "systemctl disable --now $UNIT 2>/dev/null; echo stopped ($DEV left in place)" ;;
  url)  echo "http://$HOST:$PORT/" ;;
  *)    grep '^#   ops/dev_site.sh' "$0" | sed 's/^#   //'; exit 1 ;;
esac
