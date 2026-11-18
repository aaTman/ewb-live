#!/bin/bash
# Redeploy the chips preview on :8052 of the ewb LXC. Syncs BOTH dashboard/ and
# ewb_live/ (the server code itself changed — the ?v= cache-buster lives in
# ewb_live/serve.py, so the running :8052 process must be restarted to pick it
# up), then relaunches the detached server exactly as it was first started
# (system `python3 -m ewb_live.serve`, cwd = tree root, data/ symlink untouched).
# :8051 (dev site) and /opt/ewb-live are never touched.
set -euo pipefail
cd /home/orca/orca/workspaces/ewb-live/ne-chips
H=root@192.168.0.121
T=/opt/ewb-preview-chips
rsync -a --delete dashboard/  $H:$T/dashboard/
rsync -a --delete --exclude '__pycache__' ewb_live/ $H:$T/ewb_live/
# restart ONLY the :8052 server. The `[e]` bracket stops pkill -f from matching
# the very shell running pkill (whose command line contains the pattern).
ssh $H "pkill -f '[e]wb_live.serve --port 8052' || true; sleep 1"
ssh -n $H "cd $T && setsid nohup python3 -m ewb_live.serve --port 8052 --host 0.0.0.0 \
  >serve.log 2>&1 </dev/null & sleep 1; echo restarted"
ssh $H "ss -ltnp | grep -q :8052 && echo ':8052 listening' || { echo ':8052 DOWN'; cat $T/serve.log; exit 1; }"
