"""Watch Hermes agent terminals in this worktree and enforce the 90% context handoff rule.

Polls every terminal in this worktree, reads the `used/total` context counter from the Hermes
status bar, and at >= 90% sends the agent the handoff instruction (once per terminal). Exits with
a line "HANDOFF <handle> <pct>" after each trigger, so the orchestrator can check that the
handoff happened, then restart this script.

Usage: python3 ops/context_watchdog.py [--threshold 0.9] [--interval 120]
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "notes" / ".watchdog_sent.json"
CTX_RE = re.compile(r"│ ([\d.]+)k(?:/([\d.]+)k)?(?: |$)")
DEFAULT_TOTAL_K = 200.0  # Hermes hides the total in narrow status bars
MSG = (
    "[Orchestrator watchdog] Your context is at {pct:.0%}. Do the mandatory context handoff from "
    "SPEC.md now: write notes/handoff_<your-agent>_<n>.md (goal, state, done, in progress incl. "
    "running PIDs/logs, ordered next steps, gotchas), launch your successor (it must keep YOUR exact role name) with orca agent launch "
    "--agent pi --surface terminal --prompt \"You are agent '<your-agent>' (successor). Read "
    "SPEC.md, then notes/handoff_<your-agent>_<n>.md, and continue from there.\" --json, put its "
    "handle at the top of the handoff file, then stop."
)


def orca(*args):
    out = subprocess.run(["orca", *args, "--json"], capture_output=True, text=True, timeout=60)
    try:
        return json.loads(out.stdout)["result"]
    except (ValueError, KeyError):
        return {}  # stale handle or orca unavailable; skip this poll


def context_pct(handle):
    tail = orca("terminal", "read", "--terminal", handle).get("terminal", {}).get("tail", [])
    for line in reversed(tail):
        m = CTX_RE.search(line)
        if m:
            return float(m.group(1)) / float(m.group(2) or DEFAULT_TOTAL_K)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.9)
    ap.add_argument("--interval", type=int, default=120)
    a = ap.parse_args()
    sent = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    while True:
        terms = orca("terminal", "list")
        terms = terms.get("terminals", []) if isinstance(terms, dict) else terms
        for t in terms:
            if t.get("worktreePath") != str(ROOT) or t.get("orphaned"):
                continue
            h = t["handle"]
            try:
                pct = context_pct(h)
            except Exception:
                continue
            if pct is None or pct < a.threshold or h in sent:
                continue
            subprocess.run(["orca", "terminal", "send", "--terminal", h,
                            "--text", MSG.format(pct=pct), "--enter", "--json"],
                           capture_output=True, timeout=60)
            sent.add(h)
            STATE.write_text(json.dumps(sorted(sent)))
            print(f"HANDOFF {h} {pct:.0%}", flush=True)
            sys.exit(0)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
