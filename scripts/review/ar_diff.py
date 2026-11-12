"""Before/after AR list for the tracking rollout (owner 2026-11-09).

Compares two events_selected.json snapshots and the raw-layer track ledger
(data/results/raw_exceedance/ar_tracks.json), attributing each removed AR
event to the track-level exclusion that removed it (window overlap with a
dropped track; 'tc' reason carries storm name/SID/distance).

usage: python scripts/review/ar_diff.py BEFORE.json AFTER.json LEDGER.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _ars(path: str) -> dict[str, dict]:
    ev = json.loads(Path(path).read_text())
    return {e["id"]: e for e in ev if e["event_type"] == "atmospheric_river"}


def _overlap(a0: str, a1: str, b0: str, b1: str) -> bool:
    return a0 < b1 and b0 < a1


def main() -> int:
    before, after, ledger_p = sys.argv[1:4]
    b, a = _ars(before), _ars(after)
    ledger = json.loads(Path(ledger_p).read_text()) if Path(ledger_p).exists() else {}
    dropped = ledger.get("dropped", [])

    print(f"BEFORE: {len(b)} AR events   AFTER: {len(a)} AR events")
    print("\n-- kept (event id | window | track_id) --")
    for i, e in sorted(a.items(), key=lambda kv: kv[1]["start"]):
        tid = (e.get("details") or {}).get("track_id", "-")
        print(f"  KEEP {i} {e['start'][:10]}..{e['end'][:10]}  {tid}")

    print("\n-- removed (event id | window | reason) --")
    for i, e in sorted(b.items(), key=lambda kv: kv[1]["start"]):
        if i in a:
            continue
        why = "window no longer selected (curation/below threshold)"
        for d in dropped:
            if _overlap(e["start"], e["end"], d["start"], d["end"]):
                if d["reason"] == "tc":
                    why = (f"TC co-located: {d.get('tc_name')} "
                           f"({d.get('ibtracs_sid')}) {d.get('distance_km')} km")
                else:
                    why = "touches 15S-15N (track-level tropical exclusion)"
                why += f"  [track {d['id']}]"
                break
        print(f"  DROP {i} {e['start'][:10]}..{e['end'][:10]}  {why}")

    kept_dropped = sum(1 for d in dropped)
    print(f"\nraw-layer tracks dropped (all objects, not just events): "
          f"{kept_dropped} "
          f"(tropical {sum(1 for d in dropped if d['reason'] == 'tropical')}, "
          f"tc {sum(1 for d in dropped if d['reason'] == 'tc')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
