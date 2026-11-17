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
#   ops/dev_site.sh sync     rsync THIS repo's code -> $DEV (run locally)
#   ops/dev_site.sh init     symlinks + data layout + seed results from live
#   ops/dev_site.sh lanes    detection -> per-event viz -> publish (background)
#   ops/dev_site.sh web      install + start ewb-live-dev-web.service ($PORT)
#   ops/dev_site.sh status   unit / port / log tail / per-type counts
#   ops/dev_site.sh watch    follow the lane log
#   ops/dev_site.sh stop     stop the dev server (leaves the tree in place)
#   ops/dev_site.sh url      the URL the owner opens
#
# Env: EWB_DEV_BOX (default root@192.168.0.121), EWB_DEV_DIR (default
# /opt/ewb-live-dev), EWB_DEV_PORT (default 8051), EWB_DEV_HOST (default the
# box's LAN address — the Cloudflare tunnel on this box has no credentials, so
# the public entry is dark and a LAN port is the honest stand-in for a dev
# hostname until the owner authorises the tunnel).
#
# HARD RULES: every remote write path is under $DEV. The live tree is only read
# (symlink targets, rsync FROM live without --delete).
set -uo pipefail

BOX="${EWB_DEV_BOX:-root@192.168.0.121}"
DEV="${EWB_DEV_DIR:-/opt/ewb-live-dev}"
LIVE=/opt/ewb-live
PORT="${EWB_DEV_PORT:-8051}"
HOST="${EWB_DEV_HOST:-192.168.0.121}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=ewb-live-dev-web
# remote scripts come in on stdin with these bound, so no quoting traps
r() { ssh -o ConnectTimeout=10 "$BOX" "DEV='$DEV' LIVE='$LIVE' PORT='$PORT' UNIT='$UNIT' bash -s"; }

case "${1:-}" in
  sync)
    ssh "$BOX" "mkdir -p '$DEV'"
    rsync -az --delete --delete-excluded \
      --exclude .git --exclude .venv --exclude data --exclude logs \
      --exclude third_party --exclude __pycache__ --exclude .pytest_cache \
      --exclude '*.pyc' \
      "$REPO/" "$BOX:$DEV/" && echo "synced code -> $DEV"
    ;;
  init)
    r <<'REMOTE'
set -e
mkdir -p "$DEV/data/cache" "$DEV/logs"
[ -e "$DEV/.venv" ]       || ln -s "$LIVE/.venv" "$DEV/.venv"
[ -e "$DEV/third_party" ] || ln -s "$LIVE/third_party" "$DEV/third_party"
# read-only inputs: symlink, never copy (clim 26 G, truth mirror 45 G, stores)
for d in clim imerg icechunk; do
  [ -e "$DEV/data/$d" ] || ln -s "$LIVE/data/$d" "$DEV/data/$d"
done
for d in truth fc_parts mlwp; do
  [ -e "$DEV/data/cache/$d" ] || ln -s "$LIVE/data/cache/$d" "$DEV/data/cache/$d"
done
# small cache metadata: COPY, because the lanes write inside data/cache
for f in truth_cutoff.json aiwp_inits.json; do
  [ -e "$DEV/data/cache/$f" ] || cp "$LIVE/data/cache/$f" "$DEV/data/cache/" 2>/dev/null || true
done
# everything the run writes is a REAL dir here, seeded from live so the dev site
# shows the existing scorecard/history instead of empty tabs
mkdir -p "$DEV/data/cache/events" "$DEV/data/cache/ibtracs" "$DEV/data/cache/viz" \
       "$DEV/data/cache/ewb_mid" "$DEV/data/cache/score_parts"
if [ ! -f "$DEV/data/results/meta.json" ]; then
  mkdir -p "$DEV/data/results"
  rsync -a "$LIVE/data/results/" "$DEV/data/results/"
  echo "seeded results from live (copy; live untouched)"
fi
df -h / | tail -1
du -sh "$DEV/data/results"
REMOTE
    ;;
  lanes)
    r <<'REMOTE'
cd "$DEV" || exit 1
nohup sh -c '
  set -x
  . .venv/bin/activate
  export EWB_DATA_PREP_DIR="$DEV/third_party/ExtremeWeatherBench/data_prep"
  echo "=== $(date -u +%FT%TZ) detect"
  python -m ewb_live.pipeline --events-only --days 90 --top-n 15
  echo "=== $(date -u +%FT%TZ) viz"
  python -m ewb_live.viz --all --force --types heat_wave,freeze,cold_snap,atmospheric_river
  echo "=== $(date -u +%FT%TZ) publish"
  python -m ewb_live.pipeline --publish-only --days 90
  echo "=== $(date -u +%FT%TZ) done"
' > logs/lanes.log 2>&1 < /dev/null &
echo "started (logs/lanes.log)"; sleep 3; tail -3 logs/lanes.log
REMOTE
    ;;
  web)
    r <<'REMOTE'
cat > "/etc/systemd/system/$UNIT.service" <<EOF
[Unit]
Description=EWB Live DEV dashboard (review site at $DEV, port $PORT)
Documentation=file:$DEV/notes/channel_new_events.md
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
systemctl daemon-reload && systemctl enable --now "$UNIT" && sleep 1
systemctl is-active "$UNIT"
curl -s -o /dev/null -w "dashboard http %{http_code} on :$PORT\n" "http://127.0.0.1:$PORT/"
REMOTE
    ;;
  status)
    r <<'REMOTE'
systemctl is-active "$UNIT" 2>/dev/null || true
ss -ltnp 2>/dev/null | grep ":$PORT" || echo "nothing listening on :$PORT"
[ -f "$DEV/logs/lanes.log" ] && tail -4 "$DEV/logs/lanes.log"
cd "$DEV" && .venv/bin/python ops/dev_site_report.py 2>&1 | tail -8
df -h / | tail -1
REMOTE
    ;;
  watch) ssh -t "$BOX" "tail -f $DEV/logs/lanes.log" ;;
  stop) r <<'REMOTE'
systemctl disable --now "$UNIT" 2>/dev/null || true
echo "stopped; $DEV left in place"
REMOTE
    ;;
  url) echo "http://$HOST:$PORT/" ;;
  *)   grep '^#   ops/dev_site.sh' "$0" | sed 's/^#   //'; exit 1 ;;
esac
