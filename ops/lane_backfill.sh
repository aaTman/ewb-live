#!/usr/bin/env bash
# ops/lane_backfill.sh -- the R1 guard + launcher for track-lane backfills
# (notes/scoring_design.md section 8.0 R1, 8.3).
#
#   ops/lane_backfill.sh <lane> [--cap-hours H] [--check-only] [-- <lane_pool args>]
#   ops/lane_backfill.sh tc                       # full TC backfill (resumable)
#   ops/lane_backfill.sh tc -- --models graphcast,ifs_hres --max-events 3
#
# Runs ON the ewb LXC, in the tree that holds this script (so a private preview tree
# backfills its own data/, never /opt/ewb-live).
#
# R1 -- a backfill never overlaps the weekly run (ewb-live-weekly.service, Mondays
# 07:00 UTC, Persistent=true, TimeoutStartSec=24h, last run ~9 h, 184 GB peak). The guard
# reads STATE, not the clock:
#   * NEVER START while `systemctl is-active ewb-live-weekly.service` is active /
#     activating (or reloading / deactivating, or cannot be read: "cannot prove it
#     is idle" means no), or while the weekly/deploy flock (/opt/ewb-live/.deploy.lock)
#     is held (`flock -n`);
#   * REFUSE from Monday 06:30 UTC until Monday 14:00 UTC while the weekly service is not
#     yet active (it may be about to start) UNLESS it already ran this week, i.e.
#     `systemctl show ewb-live-weekly.service -p ActiveExitTimestamp` is at or after this
#     Monday 07:00 UTC. No other clock window.
#   * then run the lane under `timeout` (hard cap, default 6 h) and POLL the same checks
#     every 30 s. On a positive poll: SIGTERM to the pool's process group, wait for it to
#     drain (the pool finishes its running jobs and keeps every completed (event, model)
#     part file, which are keyed (event, model, fingerprint, truth) so the next run resumes),
#     SIGKILL only if it has not exited after the drain wait, and exit 0.
#
# LIVE TREE: refused (exit 3). The backfill writes part files, sidecars and scores under the tree it
# runs in, so it must run from a private tree (e.g. /opt/ewb-preview-*), never /opt/ewb-live: it
# refuses when this script's tree, or any --results / --parts (also after `--`, and the defaults
# data/results and data/cache/score_parts), resolves under /opt/ewb-live, unless
# LANE_BACKFILL_ALLOW_LIVE=1 (a deliberate, owner-approved live backfill).
#
# Exit: 0 job finished OR stopped because the weekly service came up (resumable);
#       75 PAUSED FOR DEPLOY: the deploy lock was taken mid-run (the pool drained, rerun to resume;
#          deliberately not 0 so a stopped backfill is never mistaken for a finished one);
#       3 refused to start (weekly live / lock held / Monday window / live tree);
#       124 hard cap reached (the pool drained, resumable); else the job's own status.
#
# Test hooks (tests/test_lane_guard.py) are honoured ONLY when LANE_GUARD_TEST=1 and ignored
# otherwise, so a stray environment variable can never fake the guard on the box:
# LANE_GUARD_SYSTEMCTL, LANE_GUARD_UNIT, LANE_GUARD_LOCK, LANE_GUARD_NOW (a `date -d` string = the
# fake clock), LANE_GUARD_POLL (s), LANE_GUARD_DRAIN (s), LANE_GUARD_LIVE_ROOT, LANE_JOB_CMD
# (replaces the lane_pool command).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "${LANE_GUARD_TEST:-0}" = 1 ]; then
    SYSTEMCTL="${LANE_GUARD_SYSTEMCTL:-systemctl}"
    UNIT="${LANE_GUARD_UNIT:-ewb-live-weekly.service}"
    LOCK="${LANE_GUARD_LOCK:-/opt/ewb-live/.deploy.lock}"
    POLL="${LANE_GUARD_POLL:-30}"
    DRAIN="${LANE_GUARD_DRAIN:-600}"
    LIVE_ROOT="${LANE_GUARD_LIVE_ROOT:-/opt/ewb-live}"
    JOB_OVERRIDE="${LANE_JOB_CMD:-}"
    FAKE_NOW="${LANE_GUARD_NOW:-}"
else
    SYSTEMCTL=systemctl UNIT=ewb-live-weekly.service LOCK=/opt/ewb-live/.deploy.lock
    POLL=30 DRAIN=600 LIVE_ROOT=/opt/ewb-live JOB_OVERRIDE="" FAKE_NOW=""
fi
CAP_HOURS="${LANE_CAP_HOURS:-6}"

log() { echo "$(date -u +%FT%TZ) lane_backfill: $*" >&2; }

now_epoch() {
    if [ -n "$FAKE_NOW" ]; then date -u -d "$FAKE_NOW" +%s; else date -u +%s; fi
}

# guard_block: prints the reason and returns 0 when a backfill must NOT run now.
guard_block() {
    local st
    st="$("$SYSTEMCTL" is-active "$UNIT" 2>/dev/null | head -n1)"
    case "$st" in
        active|activating|reloading|deactivating) echo "$UNIT is $st"; return 0 ;;
        inactive|failed) ;;
        *) echo "cannot read $UNIT state (got '${st:-nothing}'): cannot prove the weekly run is idle"; return 0 ;;
    esac
    if [ -e "$LOCK" ] && ! flock -n -s "$LOCK" true 2>/dev/null; then
        echo "weekly/deploy lock $LOCK is held"; return 0
    fi
    local now dow hm
    now="$(now_epoch)" || { echo "cannot read the clock"; return 0; }
    dow="$(date -u -d "@$now" +%u)"
    hm="$((10#$(date -u -d "@$now" +%H%M)))"
    if [ "$dow" = 1 ] && [ "$hm" -ge 630 ] && [ "$hm" -lt 1400 ]; then
        local out val ts mon7
        out="$("$SYSTEMCTL" show "$UNIT" -p ActiveExitTimestamp --timestamp=utc 2>/dev/null | head -n1)"
        val="${out#*=}"
        if [ -z "$val" ] || [ "$val" = "n/a" ]; then
            echo "Monday 06:30-14:00 UTC and the weekly service has not run this week (ActiveExitTimestamp empty)"; return 0
        fi
        ts="$(date -u -d "$val" +%s 2>/dev/null)" || { echo "cannot parse ActiveExitTimestamp '$val'"; return 0; }
        mon7="$(date -u -d "$(date -u -d "@$now" +%F) 07:00:00" +%s)"
        if [ "$ts" -lt "$mon7" ]; then
            echo "Monday 06:30-14:00 UTC and the weekly service last finished at '$val' (before this Monday 07:00 UTC)"; return 0
        fi
    fi
    return 1
}

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

[ $# -ge 1 ] || usage
LANE="$1"; shift
CHECK_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --cap-hours) CAP_HOURS="$2"; shift 2 ;;
        --check-only) CHECK_ONLY=1; shift ;;
        --) shift; break ;;
        *) echo "unknown option $1" >&2; usage ;;
    esac
done

under_live() {   # under_live <path>: 0 when the resolved path (relative ones against $ROOT) is the live tree or below
    local p="$1" r
    [[ "$p" == /* ]] || p="$ROOT/$p"
    r="$(realpath -m -- "$p" 2>/dev/null)" || return 1
    [ "$r" = "$LIVE_ROOT" ] || [[ "$r" == "$LIVE_ROOT"/* ]]
}
if [ "${LANE_BACKFILL_ALLOW_LIVE:-0}" != 1 ]; then
    live_hit=""
    under_live "$ROOT" && live_hit="tree $ROOT"
    argv=("$@")
    for ((i = 0; i < ${#argv[@]}; i++)); do
        a="${argv[$i]}"
        case "$a" in
            --results|--parts) under_live "${argv[$((i + 1))]:-.}" && live_hit="$a ${argv[$((i + 1))]}" ;;
            --results=*|--parts=*) under_live "${a#*=}" && live_hit="$a" ;;
        esac
    done
    # the defaults the lane writes when no --results / --parts is given (a symlinked data/ counts)
    under_live "data/results" && live_hit="${live_hit:-default data/results}"
    under_live "data/cache/score_parts" && live_hit="${live_hit:-default data/cache/score_parts}"
    if [ -n "$live_hit" ]; then
        log "REFUSED to start ($LANE): would write under the live tree $LIVE_ROOT ($live_hit); run from a private tree or set LANE_BACKFILL_ALLOW_LIVE=1"
        exit 3
    fi
fi

if reason="$(guard_block)"; then
    log "REFUSED to start ($LANE): $reason"
    exit 3
fi
log "guard ok for lane $LANE"
[ "$CHECK_ONLY" = 1 ] && exit 0

cd "$ROOT" || exit 1
ulimit -n 65536 2>/dev/null || ulimit -n "$(ulimit -Hn)" 2>/dev/null || true   # icechunk: "Too many open files"
export EWB_DATA_PREP_DIR="${EWB_DATA_PREP_DIR:-$ROOT/third_party/ExtremeWeatherBench/data_prep}"
if [ -n "$JOB_OVERRIDE" ]; then
    JOB=(bash -c "$JOB_OVERRIDE")
else
    JOB=("${LANE_PYTHON:-.venv/bin/python}" -m ewb_live.scoring.lane_pool "$LANE" "$@")
fi

# the job gets its own process group (job control), so one signal reaches the pool parent,
# its spawn workers and timeout itself
set -m
timeout -k 120 "${CAP_HOURS}h" "${JOB[@]}" &
JOB_PID=$!
log "started $LANE (pid/pgid $JOB_PID, cap ${CAP_HOURS} h, poll ${POLL} s)"

STOPPED=0
STOP_WHY=""
stop_job() {   # stop_job <why>: SIGTERM the group, wait for the drain, SIGKILL as a last resort
    log "STOPPING ($1): SIGTERM to process group $JOB_PID, waiting up to ${DRAIN} s for the pool to drain"
    STOPPED=1
    STOP_WHY="$1"
    kill -TERM -- "-$JOB_PID" 2>/dev/null || true
    local waited=0
    while kill -0 "$JOB_PID" 2>/dev/null && [ "$waited" -lt "$DRAIN" ]; do sleep 1; waited=$((waited + 1)); done
    if kill -0 "$JOB_PID" 2>/dev/null; then
        log "pool did not drain in ${DRAIN} s: SIGKILL (unfinished pairs are rescored next run)"
        kill -KILL -- "-$JOB_PID" 2>/dev/null || true
    fi
}
trap 'stop_job "signal to lane_backfill"' TERM INT

while kill -0 "$JOB_PID" 2>/dev/null; do
    for ((i = 0; i < POLL; i++)); do
        kill -0 "$JOB_PID" 2>/dev/null || break
        sleep 1
    done
    kill -0 "$JOB_PID" 2>/dev/null || break
    if reason="$(guard_block)"; then
        stop_job "$reason"
        break
    fi
done

wait "$JOB_PID" 2>/dev/null
RC=$?
if [ "$STOPPED" = 1 ]; then
    case "$STOP_WHY" in
        "weekly/deploy lock"*)
            log "PAUSED FOR DEPLOY (exit 75, not finished): $STOP_WHY; completed part files are kept, rerun to resume"
            exit 75 ;;
    esac
    log "stopped by the guard ($STOP_WHY); completed part files are kept, rerun to resume"
    exit 0
fi
if [ "$RC" = 124 ]; then log "hard cap (${CAP_HOURS} h) reached; the pool drained, rerun to resume"; fi
log "job exited rc=$RC"
exit "$RC"
