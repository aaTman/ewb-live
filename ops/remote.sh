#!/usr/bin/env bash
# ops/remote.sh — run EWB Live on the dedicated ewb LXC.
#
# HARD RULE (product owner, 2026-11-06): no EWB compute on this container.
# Code lives here (this worktree); execution happens on ewb:/opt/ewb-live.
#
# Subcommands:
#   sync                 rsync code to the box (never touches remote data/ or logs/)
#   run <cmd...>         sync, then run <cmd> in /opt/ewb-live with .venv active,
#                        streaming output and appending logs/run-<UTC ts>.log
#   bg <name> <cmd...>   sync, then run detached under systemd-run
#                        (unit ewb-live-<name>, fallback nohup), log logs/<name>.log
#   logs <name> [args]   tail /opt/ewb-live/logs/<name>.log  (args e.g. -n 100, -f)
#   status               units, ports, disk, latest weekly/web log lines
#   deploy-data          one-off push of data/icechunk + data/results (initial seed;
#                        never deletes remote data; after go-live the box owns data/)
#   pull-results         rsync /opt/ewb-live/data/results back into this worktree
set -euo pipefail

HOST="${EWB_LIVE_HOST:-root@192.168.0.121}"
REMOTE_DIR="${EWB_LIVE_REMOTE_DIR:-/opt/ewb-live}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Code-only excludes. data/ and logs/ are NEVER synced up once the box is live
# (the weekly cycle there owns the store, the truth mirror and data/results).
EXCLUDES=(--exclude .venv --exclude __pycache__ --exclude .git --exclude data --exclude logs --exclude node_modules --exclude .e2e-webkit-libs)

rsync_code() {
    # Serialize box deploys across agent lanes (AR-lane incident 2026-11-10: a
    # whole-tree sync reverted another lane's code mid-publish). Builders hold
    # the same lock: flock -w 900 /opt/ewb-live/.deploy.lock -c '<build>'.
    rsync -az --rsync-path="flock -w 900 $REMOTE_DIR/.deploy.lock rsync" \
        "${EXCLUDES[@]}" "$REPO/" "$HOST:$REMOTE_DIR/"
    # Make reverts visible: box md5s of the AR-lane sentinel files after every sync.
    ssh "$HOST" "md5sum $REMOTE_DIR/ewb_live/viz/exceedance.py $REMOTE_DIR/ewb_live/events/ar_filter.py $REMOTE_DIR/dashboard/mergedmap.js 2>/dev/null" >&2
}

remote_run() {  # remote_run <log-file> <cmd string>  — streamed, logged, exit code kept
    local log="$1"; shift
    local T=""
    [ -t 1 ] && T="-t"   # only allocate a tty when we have one (avoids ssh warning)
    ssh $T "$HOST" "cd $REMOTE_DIR && mkdir -p logs && set -o pipefail && \
        . .venv/bin/activate && $* 2>&1 | tee -a '$log'"
}

case "${1:-}" in
    sync)
        rsync_code
        echo "synced $REPO -> $HOST:$REMOTE_DIR (code only; remote data/ and logs/ untouched)"
        ;;
    run)
        shift; [ $# -gt 0 ] || { echo "usage: ops/remote.sh run <cmd...}" >&2; exit 2; }
        rsync_code
        log="logs/run-$(date -u +%Y%m%dT%H%M%SZ).log"
        remote_run "$log" "$(printf '%q ' "$@")"
        echo "--- log: $HOST:$REMOTE_DIR/$log" >&2
        ;;
    bg)
        shift; NAME="${1:-}"; shift || true
        case "$NAME" in *[!A-Za-z0-9_-]*|"") echo "usage: ops/remote.sh bg <name> <cmd...>  (name: [A-Za-z0-9_-]+)" >&2; exit 2;; esac
        [ $# -gt 0 ] || { echo "usage: ops/remote.sh bg <name> <cmd...>" >&2; exit 2; }
        rsync_code
        # POSIX-safe quoting (bash %q output is not portable to /bin/sh).
        CMD=""
        for a in "$@"; do CMD="${CMD}'${a//\'/\'\\\'\'}' "; done
        # Refuse to double-run: an active transient unit or an older job
        # writing the same log means <name> is already in flight.
        # ([l] trick stops pgrep from matching its own invoking shell.)
        if ssh "$HOST" "systemctl is-active --quiet ewb-live-$NAME 2>/dev/null || pgrep -f '[l]ogs/($NAME\.log|\.runner-$NAME\.sh)' >/dev/null 2>&1"; then
            echo "bg: ewb-live-$NAME is already running on the box (check: ops/remote.sh status / logs $NAME). Not starting a duplicate." >&2
            exit 1
        fi
        # Runner script shipped over stdin — immune to nested-quoting bugs
        # in the command (e.g. `python -c 'import x; print(1)'`).
        printf '#!/bin/sh\ncd %s\n. .venv/bin/activate\n%s >> logs/%s.log 2>&1\n' \
            "$REMOTE_DIR" "$CMD" "$NAME" \
            | ssh "$HOST" "cat > $REMOTE_DIR/logs/.runner-$NAME.sh && chmod +x $REMOTE_DIR/logs/.runner-$NAME.sh"
        # systemd-run transient unit ewb-live-<name>; fall back to detached nohup.
        ssh "$HOST" "if command -v systemd-run >/dev/null && systemd-run --quiet --collect --unit=ewb-live-$NAME $REMOTE_DIR/logs/.runner-$NAME.sh 2>/dev/null; then \
                echo \"started unit ewb-live-$NAME (log logs/$NAME.log)\"; \
            else \
                setsid nohup $REMOTE_DIR/logs/.runner-$NAME.sh >/dev/null 2>&1 & \
                echo \"started detached nohup (log $REMOTE_DIR/logs/$NAME.log)\"; \
            fi"
        ;;
    logs)
        shift; NAME="${1:-}"; shift || true
        case "$NAME" in *[!A-Za-z0-9_-]*|"") echo "usage: ops/remote.sh logs <name> [tail args, e.g. -n 100 or -f]" >&2; exit 2;; esac
        ARGS="$*"
        [ -n "$ARGS" ] || ARGS="-n 50"
        TTY=""
        [ -t 1 ] && TTY="-t"
        ssh $TTY "$HOST" "tail $ARGS '$REMOTE_DIR/logs/$NAME.log'"
        ;;
    status)
        ssh "$HOST" "
            echo '== ewb-live systemd units ==';
            systemctl list-unit-files 'ewb-live*' --no-pager 2>/dev/null;
            systemctl list-timers 'ewb-live*' --no-pager 2>/dev/null | head -4;
            echo; echo '== processes ==';
            pgrep -af 'ewb_live|/opt/ewb-live' | grep -v pgrep || echo ' (none)';
            echo; echo '== listening ports ==';
            ss -ltnp | grep -E ':80|:805' || true;
            echo; echo '== disk ==';
            df -h / | tail -1; du -sh $REMOTE_DIR/{data,logs} 2>/dev/null;
            echo; echo '== web.log (last 5) ==';
            tail -n 5 $REMOTE_DIR/logs/web.log 2>/dev/null || true;
            echo '== weekly.log (last 8) ==';
            tail -n 8 $REMOTE_DIR/logs/weekly.log 2>/dev/null || true
        "
        ;;
    deploy-data)
        # One-off initial seed of repo-owned data. Never deletes remote data.
        rsync -az --ignore-existing \
            "$REPO/data/icechunk" "$REPO/data/results" "$HOST:$REMOTE_DIR/data/"
        echo "seeded data/icechunk + data/results -> $HOST:$REMOTE_DIR/data (existing remote files kept)"
        ;;
    pull-results)
        mkdir -p "$REPO/data/results"
        rsync -az "$HOST:$REMOTE_DIR/data/results/" "$REPO/data/results/"
        echo "pulled $HOST:$REMOTE_DIR/data/results -> $REPO/data/results"
        ;;
    *)
        cat >&2 <<'EOF'
usage: ops/remote.sh sync | run <cmd...> | bg <name> <cmd...> | logs <name> [tail args]
                | status | deploy-data | pull-results
EOF
        exit 2
        ;;
esac
