"""Purge score rows for events dropped from the curated selection.

SPEC "AR tropical exclusion" (owner HIGH 2026-11-08): when detection rules
drop an event, its cached score rows must not linger in scores.parquet —
they would keep polluting the scorecard/publish while no event links to
them. Rows are deleted; the pair_status entries are replaced with an
`excluded` record carrying the reason (G-30 rule: raw reason text, never
silent absence). Sidecar part files for the event are removed (regenerable).

Run on the box between detection and weekly publish:
    python scripts/review/purge_dropped_events.py --reason "..." [--apply]
"""
import argparse
import json
import sys
from pathlib import Path

RESULTS = Path("data/results")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    import pandas as pd

    df = pd.read_parquet(RESULTS / "scores.parquet")
    sel = json.loads((RESULTS / "events_selected.json").read_text())
    sel = sel["events"] if isinstance(sel, dict) else sel
    keep = {e["id"] for e in sel}
    gone = sorted(set(df["event_id"]) - keep)
    print(f"events in selection: {len(keep)}; "
          f"score-row events not in selection: {gone or 'none'}")
    if not gone:
        return 0
    if not args.apply:
        print("(dry run; --apply to purge)")
        return 0

    n0 = len(df)
    df[~df["event_id"].isin(gone)].to_parquet(
        RESULTS / "scores.parquet", index=False)

    ps = json.loads((RESULTS / "pair_status.json").read_text())
    for eid in gone:
        for k in [k for k in ps if k.split("|", 1)[0] == eid]:
            ps[k] = {"status": "excluded", "reason": args.reason, "rows": 0}
    tmp = RESULTS / "pair_status.json.tmp"
    tmp.write_text(json.dumps(ps, indent=1))
    tmp.replace(RESULTS / "pair_status.json")

    for eid in gone:
        (RESULTS / "pair_sidecar" / f"{eid}.status.json").unlink(missing_ok=True)

    print(f"purged {n0 - len(df)} rows for {len(gone)} events; "
          f"pair_status marked excluded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
