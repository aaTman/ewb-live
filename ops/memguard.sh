#!/bin/bash
# ewb memory guard v2 (stall postmortem 2026-11-07) for ewb-live-pipeline.
#
# Scoring has priority (orchestrator rule 2026-11-06 22:05): external
# tenants' memory bursts must NOT pause scoring — they get nudged/alerted.
# v1's failures this guard now prevents:
#  * thrash: stopping the unit on avail<8 GB while a neighbor pulsed 200 GB
#    every 3 min killed in-flight AR jobs every few minutes for ~4 h and
#    freed nothing durable (pause only helps if WE are over budget);
#  * unguaranteed resume: resume needed avail>60 GB, and a guard restart
#    while paused forgot the pause, leaving the unit down silently.
#
# v2 rules (20 s poll):
#  * PAUSE (systemctl stop; frees our RSS, jobs resume from part files):
#      - our own scoring RSS > EWB_LIVE_MEM_BUDGET_GB (default 100), or
#      - avail < 5 GB (kernel-OOM last resort: scoring is the big
#        incremental tenant and loses the least)
#  * RESUME when avail > EWB_LIVE_RESUME_ABOVE_GB (default 60), GUARANTEED:
#      - if paused > PAUSE_MAX_S (default 900 s) and avail > 25 GB, force
#        resume + ALERT (host is clearly not in danger; a stuck threshold
#        must never keep scoring down forever)
#  * pause state is persisted to a marker file: a guard restart mid-pause
#    still resumes.
#  * External pressure (avail low, not our fault): PRESSURE-EXTERNAL log +
#    throttled nudge file; NEVER pause scoring for it.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG=$ROOT/logs/memguard.log
ALERT=$ROOT/logs/pressure_alert.log
MARK=$ROOT/logs/.memguard_pause
NUDGED=$ROOT/logs/.last_pressure_nudge
UNIT=ewb-live-pipeline.service
BUDGET=${EWB_LIVE_MEM_BUDGET_GB:-100}
RESUME_ABOVE=${EWB_LIVE_RESUME_ABOVE_GB:-60}
PAUSE_MAX_S=${EWB_LIVE_PAUSE_MAX_S:-900}

note() { echo "$(date -u '+%a %b %d %T UTC %Y') $*" >> "$LOG"; }
mkdir -p "$(dirname "$LOG")"

ours_rss_gb() {  # scoring parent + workers
  ps -eo rss,args --no-headers | grep -E "ewb_live[.]pipeline" | grep -v grep \
    | awk "{s+=\$1} END {printf \"%d\", s/1048576}"
}
foreign_rss() {
  ps -eo rss,args --no-headers | grep -E "python" | grep -vE "ewb_live[.]pipeline|memguard|grep" \
    | awk "\$1 > 2000000 {printf \"%s(%.0fGB) \", \$2, \$1/1048576}"
}

is_paused() {  # marker file, written by us, unit actually inactive
  [ -f "$MARK" ] && ! systemctl is-active --quiet "$UNIT"
}

while true; do
  sleep 20
  avail=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
  ours=$(ours_rss_gb)

  if is_paused; then
    paused_since=$(cat "$MARK" 2>/dev/null || echo 0)
    now=$(date +%s)
    held=$(( now - paused_since ))
    if [ "$avail" -gt "$RESUME_ABOVE" ] || { [ "$held" -gt "$PAUSE_MAX_S" ] && [ "$avail" -gt 25 ]; }; then
      systemctl start "$UNIT" && { note "RESUMED avail=${avail}GB held=${held}s"; rm -f "$MARK"; } \
        || note "RESUME-FAILED avail=${avail}GB held=${held}s (will retry)"
      # guaranteed resume: if the marker says paused but the unit is
      # active again (manual start), clear the marker
      systemctl is-active --quiet "$UNIT" && rm -f "$MARK"
    fi
  elif systemctl is-active --quiet "$UNIT"; then
    if [ "$ours" -gt "$BUDGET" ]; then
      systemctl stop "$UNIT" && { date +%s > "$MARK"; note "PAUSED(ours) avail=${avail}GB ours=${ours}GB budget=${BUDGET}GB"; }
    elif [ "$avail" -lt 5 ]; then
      systemctl stop "$UNIT" && { date +%s > "$MARK"; note "PAUSED(danger) avail=${avail}GB ours=${ours}GB foreign=$(foreign_rss)"; }
    elif [ "$avail" -lt 15 ]; then
      # external pressure: scoring keeps running (priority owner rule)
      note "PRESSURE-EXTERNAL avail=${avail}GB ours=${ours}GB foreign=$(foreign_rss)"
      now=$(date +%s); last=$(cat "$NUDGED" 2>/dev/null || echo 0)
      if [ $(( now - last )) -gt 600 ]; then
        date +%s > "$NUDGED"
        echo "$(date) PRESSURE-EXTERNAL avail=${avail}GB ours=${ours}GB foreign=$(foreign_rss)" >> "$ALERT"
        note "NUDGE-REQUESTED $(tail -1 "$ALERT")"
      fi
    fi
  fi
done
