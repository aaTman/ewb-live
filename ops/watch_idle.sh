#!/bin/bash
# Exit when any listed Hermes agent terminal has been idle for 2 polls:
# no "Ctrl+C to interrupt", no running processes, nothing queued.
# Usage: ops/watch_idle.sh name:term_handle [name:term_handle ...]
declare -A idle
while true; do
  for pair in "$@"; do n=${pair%%:*}; h=${pair#*:}
    busy=$(orca terminal read --terminal "$h" --json 2>/dev/null | python3 -c "
import sys,json,re
t=json.load(sys.stdin)['result']['terminal']
print(int(any('Ctrl+C to interrupt' in l or re.search(r'Processes · [1-9]\d* running',l) or 'queued (' in l for l in t['tail'])))" 2>/dev/null || echo 0)
    if [ "$busy" = 0 ]; then idle[$n]=$(( ${idle[$n]:-0}+1 )); else idle[$n]=0; fi
    if [ "${idle[$n]}" -ge 2 ]; then echo "IDLE: $n"; exit 0; fi
  done; sleep 90
done
