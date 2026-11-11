#!/bin/bash
# Relay ewb-box external-memory-pressure alerts to the design channel.
# The on-box guard (ops_memguard.sh) deliberately does NOT pause scoring for
# external tenants (scoring has priority); it logs PRESSURE-EXTERNAL +
# NUDGE-REQUESTED. This loop mirrors those to notes/channel_design_ewb.md and
# nudges design's terminal so the viz job yields instead of scoring pausing.
REPO=/home/orca/orca/workspaces/ewb-live/dogfish
STATE=/tmp/pressure_relay_seen
# design owner's terminal; override if the roster changes. shortcut: single
# known handle, <when to upgrade> resolve by title if design ever splits.
DESIGN_TERM=${DESIGN_TERM:-term_67e5a35a-15ce-432b-91fb-4c4c705cbace}
seen=$(cat "$STATE" 2>/dev/null || echo 0)
while true; do
  sleep 60
  n=$(ssh -o ConnectTimeout=10 root@192.168.0.121 \
      'grep -c NUDGE-REQUESTED /opt/ewb-live/logs/memguard.log 2>/dev/null || echo 0')
  if [ "${n:-0}" -gt "$seen" ]; then
    line=$(ssh root@192.168.0.121 \
      'grep PRESSURE-EXTERNAL /opt/ewb-live/logs/memguard.log | tail -1')
    {
      echo ""
      echo "## ewb -> design: memory pressure attributed to a NON-scoring job ($(date -u +%H:%M) UTC)"
      echo "\`$line\` — scoring keeps running per orchestrator priority. Please"
      echo "pause/serialize your viz job until host-avail recovers above ~40 GB"
      echo "(the box guard now alerts instead of pausing scoring at this level)."
    } >> "$REPO/notes/channel_design_ewb.md"
    # nudge only lands if the terminal is actually live (ponytail 2026-11-08)
    if orca terminal list --json 2>/dev/null | grep -q "$DESIGN_TERM"; then
      orca terminal send --terminal "$DESIGN_TERM" \
        --text "New message in notes/channel_design_ewb.md (ewb: viz memory pressure — please yield; scoring has priority)" --enter --json >/dev/null 2>&1 || true
    fi
    seen=$n; echo "$seen" > "$STATE"
  fi
done
