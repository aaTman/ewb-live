#!/bin/bash
# Durable trigger: launch the two guardians once frontend+design KT docs exist and the raw map is ready.
# Runs from ewb-live-guardian-launcher.timer every 5 min; idempotent via a state file.
cd "$(dirname "$0")/.." || exit 1
STATE=notes/.guardians_launched
[ -f "$STATE" ] && exit 0
CH=notes/channel_design_frontend.md
[ -f notes/kt_frontend.md ] && [ -f notes/kt_design.md ] && [ -f notes/handoff_frontend_final.md ] && [ -f notes/handoff_design_final.md ] || exit 0
[ "$(grep -c 'KT READY' "$CH")" -ge 2 ] && grep -q 'RAW MAP READY' "$CH" || exit 0
out=notes/guardians_launched.md; echo "# Guardians launched $(date -u +%FT%TZ)" > "$out"
for role in frontend design; do
  h=$(orca agent launch --agent claude --model claude-sonnet-5-5 --effort high --surface terminal \
      --prompt "$(cat notes/briefs/guardian_${role}.md)" --json | python3 -c "import sys,json;print(json.load(sys.stdin)['result']['outcome']['handle'])")
  echo "- guardian-${role}: ${h}" >> "$out"
done
touch "$STATE"
