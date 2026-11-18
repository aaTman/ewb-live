#!/usr/bin/env bash
# ops/tcmap_preview.sh — preview tree for the tc-map lane (owner review of the
# tropical-cyclone WIND-FIELD map layer, 2026-11-18).
#
# A THIRD tree on the ewb LXC, isolated like ops/dev_site.sh's /dev/ site, so the
# lane can be built and seen without touching either /opt/ewb-live (live) or
# /opt/ewb-live-dev (the integrator's review site — read-only input here).
#
#   ops/tcmap_preview.sh sync     rsync THIS repo's code -> $PREV
#   ops/tcmap_preview.sh init     venv/third-party symlinks + data layout, seeded
#                                 from the dev tree (small JSONs copied, the big
#                                 read-only inputs symlinked)
#   ops/tcmap_preview.sh timeline rebuild global_timeline.json in $PREV (adds the
#                                 cyclone TRACK entries; dev's copy predates them)
#   ops/tcmap_preview.sh wind     build tc_wind.json (ERA5 10 m wind windows)
#   ops/tcmap_preview.sh web      serve $PREV on :$PORT (systemd unit)
#   ops/tcmap_preview.sh status   unit / url / asset sizes / storm counts
#   ops/tcmap_preview.sh stop     stop the server (tree stays)
#
# HARD RULES: every write path is under $PREV. The dev tree is only ever read
# (rsync FROM dev without --delete, symlinks INTO it). Live is not touched.
set -uo pipefail

BOX="${EWB_TC_BOX:-root@192.168.0.121}"
PREV="${EWB_TC_DIR:-/opt/ewb-live-tcmap}"
DEV="${EWB_TC_DEV_DIR:-/opt/ewb-live-dev}"
LIVE=/opt/ewb-live
PORT="${EWB_TC_PORT:-8053}"
UNIT=ewb-live-tcmap-web
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
r() { ssh -o ConnectTimeout=10 "$BOX" "PREV='$PREV' DEV='$DEV' LIVE='$LIVE' PORT='$PORT' UNIT='$UNIT' bash -s"; }

case "${1:-}" in
  sync)
    ssh "$BOX" "mkdir -p '$PREV'"
    # NOT --delete-excluded (dev_site.sh's trap): that flag DELETES the excluded
    # paths on the receiver — i.e. it wipes data/, .venv and third_party, so every
    # sync would destroy the tree init just built. --delete alone keeps them.
    rsync -az --delete \
      --exclude .git --exclude .venv --exclude data --exclude logs \
      --exclude third_party --exclude __pycache__ --exclude .pytest_cache \
      --exclude '*.pyc' "$REPO/" "$BOX:$PREV/" && echo "synced code -> $PREV"
    ;;
  init)
    r <<'REMOTE'
set -e
mkdir -p "$PREV/data/results" "$PREV/logs"
[ -e "$PREV/.venv" ]       || ln -s "$LIVE/.venv" "$PREV/.venv"
[ -e "$PREV/third_party" ] || ln -s "$LIVE/third_party" "$PREV/third_party"
# big read-only inputs from the DEV review tree (never re-downloaded, never written)
for f in raw_exceedance events; do
  [ -e "$PREV/data/results/$f" ] || ln -s "$DEV/data/results/$f" "$PREV/data/results/$f"
done
# small JSONs: copy, because this tree rebuilds some of them
for f in events_selected.json events.json meta.json scorecard.json \
         scorecard_by_region.json scores.json pair_status.json curation.json; do
  [ -f "$DEV/data/results/$f" ] && cp -n "$DEV/data/results/$f" "$PREV/data/results/" || true
done
echo "init done; results:"; ls -la "$PREV/data/results" | head -20
REMOTE
    ;;
  timeline)
    r <<'REMOTE'
cd "$PREV" && .venv/bin/python - <<'PY'
from pathlib import Path
from ewb_live.viz.timeline import build
p = build(Path("data/results"))
print("wrote", p, p.stat().st_size, "B")
PY
REMOTE
    ;;
  wind)
    r <<'REMOTE'
cd "$PREV" && EWB_LIVE_CACHE="$LIVE/data/cache" \
  setsid nohup timeout 1800 .venv/bin/python -m ewb_live.viz.tc_wind --results data/results \
  > logs/tc_wind.log 2>&1 < /dev/null &
echo "tc_wind building in $PREV (logs/tc_wind.log, capped at 30 min)"
REMOTE
    echo "poll:  ssh $BOX 'tail -3 $PREV/logs/tc_wind.log'   — never wait on it inside a foreground command"
    ;;
  web)
    r <<'REMOTE'
cat > /etc/systemd/system/$UNIT.service <<UNIT
[Unit]
Description=EWB Live tc-map preview (tc lane, port $PORT)
After=network.target

[Service]
Type=simple
WorkingDirectory=$PREV
ExecStart=$PREV/.venv/bin/python -m ewb_live.serve --port $PORT --host 0.0.0.0
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now $UNIT
sleep 1
systemctl is-active $UNIT
REMOTE
    ;;
  status)
    r <<'REMOTE'
systemctl is-active "$UNIT" 2>/dev/null || true
ss -lntp 2>/dev/null | grep ":$PORT" || echo "port $PORT not listening"
cd "$PREV" || exit 1        # the asset check below reads data/results relatively
du -sh "$PREV/data/results" 2>/dev/null
ls -la "$PREV/data/results"/*.json 2>/dev/null | awk '{print $5, $9}'
[ -x "$PREV/.venv/bin/python" ] && "$PREV/.venv/bin/python" - <<'PY'
import json, pathlib
r = pathlib.Path("data/results")
for name, key in (("global_timeline.json", "events"), ("tc_wind.json", "storms")):
    p = r / name
    if not p.exists():
        print(f"{name}: absent"); continue
    d = json.loads(p.read_text())
    items = d.get(key, [])
    extra = ""
    if name == "tc_wind.json":
        extra = (f" bias_median={d['bias'].get('ratio_median')} n={d['bias'].get('n')}"
                 f" census={d.get('census')}")
    print(f"{name}: {len(items)} {key}{extra}")
PY
tail -3 "$PREV/logs/tc_wind.log" 2>/dev/null || true
REMOTE
    ;;
  stop)
    r 'systemctl stop "$UNIT" && echo stopped'
    ;;
  url)
    echo "http://${BOX#*@}:$PORT/"
    ;;
  *)
    sed -n '2,20p' "$0"; exit 2
    ;;
esac
