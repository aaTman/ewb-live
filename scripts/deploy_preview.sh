#!/bin/bash
# Redeploy the chips preview on :8052 of the ewb LXC. Syncs BOTH dashboard/ and
# ewb_live/ (the server code itself changed — the ?v= cache-buster lives in
# ewb_live/serve.py, so the running :8052 process must be restarted to pick it
# up), then restarts the supervised server unit.
# :8051 (dev site) and /opt/ewb-live are never touched.
#
# :8052 is the `ewb-live-chips-web` systemd unit (created 2026-11-18, matching
# the ewb-live-tcmap-web / ewb-live-dev-web pattern its siblings use). Before
# that it was a bare `setsid nohup` child of my ssh session — it survived only
# because of setsid, and a reboot or an OOM-kill (this box runs a memory guard)
# would have left :8052 dead with nothing to bring it back.
set -euo pipefail
cd /home/orca/orca/workspaces/ewb-live/ne-chips
H=root@192.168.0.121
T=/opt/ewb-preview-chips
rsync -a --delete dashboard/  $H:$T/dashboard/
rsync -a --delete --exclude '__pycache__' ewb_live/ $H:$T/ewb_live/
# restart ONLY the :8052 unit — never the ewb-live-web / ewb-live-dev-web units
ssh $H "systemctl restart ewb-live-chips-web"
ssh $H "ss -ltnp | grep -q :8052 && echo ':8052 listening ('\$(systemctl is-active ewb-live-chips-web)')' \
  || { echo ':8052 DOWN'; journalctl -u ewb-live-chips-web -n 30 --no-pager; exit 1; }"
echo "preview: http://192.168.0.121:8052/"
