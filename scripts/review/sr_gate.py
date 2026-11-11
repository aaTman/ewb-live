#!/usr/bin/env python3
"""Referee acceptance gate for the flicker/dead-tail fix (pre-registered 2026-11-10, §O).

Run on the EWB box after any lane rebuild or curation change:
    cd /opt/ewb-live && .venv/bin/python scripts/review/sr_gate.py
Gates (goalposts fixed BEFORE the fix, notes/science_review.md §O):
  G1 curated-blink: sr13_flicker reports 0 blinks in a curated AR window+bbox.
  G2 lifecycle:     sr15_event_lifecycle reports 0 interior DEAD runs longer than
                    the shipped gap-bridge on each event's own plane (§R
                    amendment: a K-frame bridge legitimately leaves interior
                    DEAD runs <= K; --bridge N, default 0 = raw semantics).
                    NEARBY frames stay REPORT-ONLY (objects may out-move a static bbox).
  G3 start-dead:    no curated window starts with its object absent (§Q).
  G4 region-check:  sr17_region_assignment reports 0 centroid-vs-overlap
                    disagreements (§S regression tripwire). Needs
                    ewb_live/regions.py + dashboard/regions.json; if absent the
                    leg reports ERROR and the run cannot reach exit 0 — a
                    broken leg, not a fix FAIL.
Baseline on the shipped lane (2026-11-10): G1=4 FAIL, G2=12 runs FAIL,
G3=[8002c1ab3ba0] FAIL, G4=0 PASS. Post-fix acceptance: --bridge N must match
the shipped bridge (bridge 3 -> G1,G2 clear; G3 needs the lifetime-gate clamp).
--bridge N = shipped gap-bridge size (0 = today's raw lane, §R amendment).
Each leg prints as it completes; a leg script that crashes yields ERROR and the
gate continues (ponytail 2026-11-10 MUST-1: the old version sys.exit(2)'d from
a leg failure before printing legs already computed).
Exit 0 = all legs pass (--fast skips G1's slow 336-frame scan — exit 0 there
is NOT full acceptance). Pins lane generation AND events_selected.json sha256
in output for audit trail (§T).
"""
import hashlib, json, subprocess, sys
FAST = "--fast" in sys.argv  # skips G1; G2/G3/G4 still run
BRIDGE = int(sys.argv[sys.argv.index("--bridge") + 1]) if "--bridge" in sys.argv else 0

def run(script):
    try:
        p = subprocess.run([sys.executable, f"scripts/review/{script}"],
                           capture_output=True, text=True, timeout=5400)
    except subprocess.TimeoutExpired:
        print(f"{script} TIMEOUT (5400s)")
        return None
    if p.returncode != 0:
        print(f"{script} FAILED rc={p.returncode}\n{p.stderr[-800:]}")
        return None
    return p.stdout

def gate_json(out):
    if out is None:
        return None
    line = [l for l in out.splitlines() if l.startswith("GATE_JSON:")]
    if not line:
        print("no GATE_JSON in output:\n", out[-500:])
        return None
    return json.loads(line[0][len("GATE_JSON:"):])

def verdict(label, fails):
    if fails is None:
        print(f"{label}: ERROR (leg produced no verdict)")
        return False
    print(f"{label}: {len(fails)} -> {'PASS' if not fails else 'FAIL ' + json.dumps(fails)[:300]}")
    return not fails

idx = json.load(open("data/results/raw_exceedance/index.json"))
ev_sha = hashlib.sha256(open("data/results/events_selected.json", "rb").read()).hexdigest()[:16]
print("lane:", idx["generated"], "frames:", len(idx["frames"]), "events_sha:", ev_sha)

ok = True
if FAST:
    print("G1 curated-window blinks: SKIPPED (--fast — full acceptance must run WITHOUT --fast)")
else:
    rep13 = gate_json(run("sr13_flicker.py"))
    g1 = None if rep13 is None else [b for b in rep13["in_curated_ar_window_and_bbox"]
                                     if b.get("gap", 1) > BRIDGE]
    ok &= verdict(f"G1 curated-window blinks (gap>{BRIDGE})", g1)
rep15 = gate_json(run("sr15_event_lifecycle.py"))
# §R: runs longer than the SHIPPED gap-bridge; blinks are 1-step gaps (gap absent -> 1)
dead = None if rep15 is None else [(r["event"], s, e2, n) for r in rep15
                                   for s, e2, n in r.get("dead_runs", []) if n > BRIDGE]
ok &= verdict(f"G2 interior DEAD runs >{BRIDGE} frames", dead)
if rep15 is not None:
    nearby = sum(1 for r in rep15 for _, c in r["classes"] if c.startswith("NEARBY"))
    print(f"   (NEARBY, report-only: {nearby})")
g3 = None if rep15 is None else [r["event"] for r in rep15 if r.get("start_dead")]
ok &= verdict("G3 windows dead at start", g3)
rep17 = gate_json(run("sr17_region_assignment.py"))
ok &= verdict("G4 region centroid-vs-overlap disagreements",
              None if rep17 is None else rep17["disagree"])
sys.exit(0 if ok else 1)
