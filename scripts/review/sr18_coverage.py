"""sr18: scores.parquet coverage vs shipped events; baseline diff for rerun compare.
Run on box (cwd = repo root). Expected cell set per event_type is data-driven:
the union of (model, metric) observed for that type across events, with the
max lead count per cell — so structural exclusions (e.g. aigfs has no heat/
freeze rows at all) are not holes, while one-event gaps are.
usage:
  python scripts/review/sr18_coverage.py --write   # print snapshot JSON, exit 0
  python scripts/review/sr18_coverage.py           # diff vs baseline, exit 1 on drift
Baseline lives in the worktree (notes/coverage_baseline.json) and reaches the
box via remote.sh sync; regenerate with --write, save stdout over the file,
only after adjudicating new holes (e.g. documented D1-class exclusions).
shortcut: baseline is pinned to one events_sha; after a legitimate curated-set
change the whole diff must be re-adjudicated, upgrade when holes get reason
metadata rather than one flat baseline.
"""
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

R = Path("data/results")
ap = argparse.ArgumentParser()
ap.add_argument("--baseline", default="notes/coverage_baseline.json")
ap.add_argument("--write", action="store_true")
a = ap.parse_args()

ev = json.load(open(R / "events_selected.json"))
ev = ev["events"] if isinstance(ev, dict) else ev
shipped = {e["id"] for e in ev}
sha = hashlib.sha256((R / "events_selected.json").read_bytes()).hexdigest()[:16]

df = pd.read_parquet(R / "scores.parquet")
need = {"event_id", "event_type", "model", "metric", "lead_time_hours"}
if not need <= set(df.columns):
    raise SystemExit(f"sr18: unexpected schema {sorted(df.columns)}")

cells = df.groupby(["event_type", "event_id", "model", "metric"])[
    "lead_time_hours"].nunique()
type_union = defaultdict(dict)  # type -> (model,metric) -> max leads
for r in cells.reset_index().to_dict("records"):
    u = type_union[r["event_type"]].setdefault((r["model"], r["metric"]), 0)
    type_union[r["event_type"]][(r["model"], r["metric"])] = max(
        u, int(r["lead_time_hours"]))

holes = []
seen = {e for e in df.event_id.unique()}
for e in sorted(shipped - seen):
    holes.append(f"missing_event {e}")
for e in sorted(seen - shipped):
    holes.append(f"STALE_PART event {e} scored but not shipped")
obs = cells.reset_index().set_index(["event_id", "model", "metric"])[
    "lead_time_hours"].to_dict()
for e in sorted(seen & shipped):
    t = df.loc[df.event_id == e, "event_type"].iloc[0]
    for (m, k), exp in sorted(type_union[t].items()):
        got = obs.get((e, m, k))
        if got is None:
            holes.append(f"missing_cell {e} {m} {k}")
        elif got < exp:
            holes.append(f"short_leads {e} {m} {k} {got}/{exp}")

snap = {"events_sha": sha, "n_rows": int(len(df)),
        "n_shipped": len(shipped), "holes": sorted(holes)}
if a.write:
    print(json.dumps(snap, indent=1))
    raise SystemExit(0)

try:
    base = json.load(open(a.baseline))
except FileNotFoundError:
    raise SystemExit(f"sr18: no baseline at {a.baseline}; seed with --write")
new = sorted(set(snap["holes"]) - set(base["holes"]))
gone = sorted(set(base["holes"]) - set(snap["holes"]))
print(f"sr18: events_sha {sha} rows {snap['n_rows']} shipped {snap['n_shipped']} "
      f"holes {len(snap['holes'])} (baseline {len(base['holes'])} @ {base['events_sha']})")
for h in new:
    print("  NEW-HOLE", h)
for h in gone:
    print("  resolved", h)
if new or gone or sha != base["events_sha"]:
    print("DRIFT: adjudicate holes, then re-pin baseline")
    raise SystemExit(1)
print("coverage OK vs baseline")
