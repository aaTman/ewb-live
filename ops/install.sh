#!/usr/bin/env bash
# Install EWB Live ops units ON THE ewb LXC (root, system-level systemd).
#
# HARD RULE (product owner, 2026-11-06): EWB services run only on the ewb
# box. This script refuses to install EWB units on any other host — run on
# the box copy (repo at /opt/ewb-live, as root) it installs
#   /etc/systemd/system/ewb-live-web.service      (dashboard, 0.0.0.0:8050)
#   /etc/systemd/system/ewb-live-weekly.service   (oneshot)
#   /etc/systemd/system/ewb-live-weekly.timer     (Mondays 07:00 UTC, Persistent)
#   /etc/systemd/system/ewb-live-store.service    (oneshot: MLWP store update)
#   /etc/systemd/system/ewb-live-store.timer      (daily 05:30 UTC, Persistent)
#
#   ops/install.sh            install + start (must be root on host 'ewb')
#   ops/install.sh --status   show what is currently running
#   ops/install.sh --uninstall  stop and remove the units
#
# Deploying to the box and running this there:
#   ops/remote.sh sync && \
#   ssh root@192.168.0.121 'cd /opt/ewb-live && ./ops/install.sh'
set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$OPS_DIR")"
UNIT_DIR=/etc/systemd/system
UNITS=(ewb-live-web.service ewb-live-weekly.service ewb-live-weekly.timer
       ewb-live-store.service ewb-live-store.timer
       ewb-live-pipeline.service ewb-live-memguard.service
       ewb-live-freshness.service ewb-live-freshness.timer)

require_ewb_root() {
  if [ "$(whoami)" != root ] || [ "$(hostname)" != ewb ]; then
    cat >&2 <<EOF
EWB services install only on the ewb LXC as root (host is '$(hostname)', user '$(whoami)').
Copy the repo with ops/remote.sh sync, then run:
  ssh root@192.168.0.121 'cd /opt/ewb-live && ./ops/install.sh'
(The local ewb-live-context-watchdog.service is managed separately; it is
agent management, not EWB compute, and stays on this container.)
EOF
    exit 2
  fi
}

render_units() {
  mkdir -p "$REPO/logs"
  for u in "${UNITS[@]}"; do
    sed -e "s|__REPO__|$REPO|g" -e "s|__WANTED_BY__|multi-user.target|g" \
      "$OPS_DIR/$u" > "$UNIT_DIR/$u"
    echo "installed $UNIT_DIR/$u"
  done
  systemctl daemon-reload
}

install_units() {
  require_ewb_root
  # Public leak gate (owner rule 2026-11-08): refuse to (re)deploy a tree
  # whose served content carries private-infrastructure strings.
  "$REPO/scripts/public_scrub_check.sh" "$REPO" \
    || { echo "public_scrub_check FAILED — fix leaks above before deploying" >&2; exit 1; }
  render_units
  systemctl enable --now ewb-live-web.service
  systemctl enable ewb-live-weekly.timer ewb-live-store.timer
  systemctl start ewb-live-weekly.timer 2>/dev/null || true
  systemctl start ewb-live-store.timer 2>/dev/null || true
  # scoring lifecycle (stall postmortem 2026-11-07): the guard and the
  # freshness timer always run; the pipeline unit ENABLES (so a host
  # reboot resumes scoring) but is not force-started here — starting a
  # full run stays an explicit agent/orchestrator act.
  systemctl enable --now ewb-live-memguard.service
  systemctl enable ewb-live-pipeline.service
  systemctl enable --now ewb-live-freshness.timer
  echo
  systemctl --no-pager --full status ewb-live-web.service | head -5 || true
  systemctl --no-pager list-timers 'ewb-live-weekly*' || true
}

status() {
  systemctl --no-pager list-unit-files 'ewb-live*' 2>/dev/null || echo "(systemctl unavailable on this host)"
  systemctl --no-pager list-timers 'ewb-live*' 2>/dev/null || true
  (ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null) | grep -E ':8050 ' || echo "nothing listening on 8050"
}

uninstall() {
  require_ewb_root
  # loop the full UNITS list — install_units enables all of them (pipeline,
  # memguard, freshness included), so uninstall must too (ponytail 2026-11-08)
  systemctl disable --now "${UNITS[@]}" 2>/dev/null || true
  for u in "${UNITS[@]}"; do rm -f "${UNIT_DIR%/}/$u"; done
  systemctl daemon-reload
  echo "units removed"
}

case "${1:-install}" in
  install)
    install_units
    echo
    echo "verify: curl -s http://192.168.0.121:8050/ -o /dev/null -w '%{http_code}\n'"
    ;;
  --status|status) status ;;
  --uninstall|uninstall) uninstall ;;
  *) echo "usage: ops/install.sh [--status|--uninstall]" >&2; exit 2 ;;
esac
