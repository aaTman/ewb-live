"""tc lane sanity check — run on the ewb LXC (real IBTrACS data).

    ops/remote.sh run python scripts/tc_sanity.py [--days 90]

Two things the tc lane must keep true, checked against the live source instead
of a fixture:

1. COUNTS. tropical_cyclone events in the window, by basin, with the peak-wind
   distribution — the owner's sanity check (a lane that suddenly emits 300
   events, or none, is broken).
2. CONTRACT. the pieces the pipeline uses on those real events: validate_event
   accepts them, the selection layer never merges a track and never lets the
   no-overlap rule delete one, the per-type quota keeps a whole season, and the
   event JSON the browser consumes stays NaN-free with the shared 5-slot
   details.track rows.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import statistics


def main(argv=None) -> int:
    from ewb_live.events import select as S
    from ewb_live.events.tc import scan_tc_events
    from ewb_live.events.validate import validate_event

    ap = argparse.ArgumentParser(prog="python -m scripts.tc_sanity")
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args(argv)

    end = dt.date.today()
    start = end - dt.timedelta(days=args.days)
    evs = scan_tc_events(start, end)
    print(f"window {start} .. {end}: {len(evs)} tropical_cyclone events")

    by: dict[str, list[float]] = collections.defaultdict(list)
    for e in evs:
        by[e.details["basin_name"]].append(float(e.peak_value or 0.0))
    for b, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        print(f"  {b:<24} {len(v):>3} events   peak kt: median {statistics.median(v):3.0f}"
              f", max {max(v):3.0f}")
    cats = collections.Counter(e.details["peak_category"] for e in evs)
    print("peak categories:", dict(sorted(cats.items())))
    dur = [(e.end - e.start).total_seconds() / 86400 for e in evs]
    print(f"duration days: median {statistics.median(dur):.1f}, max {max(dur):.1f}")
    print(f"tracks: {sum(1 for e in evs if e.details['track'])}/{len(evs)}, "
          f"antimeridian crossings: "
          f"{sum(1 for e in evs if e.details['track_crosses_antimeridian'])}, "
          f"named: {sum(1 for e in evs if e.details['name'] != 'Unnamed')}/{len(evs)}")

    bad = {e.id: p for e in evs if (p := validate_event(e))}
    print(f"validate_event failures: {len(bad)}")
    for k, v in list(bad.items())[:3]:
        print("  BAD", k, v)

    merged = S.merge_same_type(evs, [])
    print(f"merge_same_type: {len(merged)} in -> {len(merged)} out "
          f"({'OK: tracks never merge' if len(merged) == len(evs) else 'BROKEN'})")
    floored = [e for e in merged if S._passes_floor(e)]
    print(f"selection floor: {len(floored)}/{len(merged)} pass")
    cap = S.TOP_N_BY_TYPE["tropical_cyclone"]
    top = S.rank_and_cap(floored, {"tropical_cyclone": cap})
    print(f"quota (top_n {cap}): {len(top)} selected")

    # the no-overlap rule must not delete a storm sitting inside an area event
    import dataclasses

    from ewb_live.events.types import Event
    area = Event(id="areacase", event_type="heat_wave", title="x",
                 start=dt.datetime(2026, 9, 1), end=dt.datetime(2026, 9, 10),
                 bbox=(10.0, 20.0, 120.0, 140.0), peak_value=40.0, details={})
    inside = [e for e in evs
              if e.bbox[0] < 20 and e.bbox[1] > 10 and e.start < area.end and e.end > area.start
              and e.bbox[2] < e.bbox[3] and e.bbox[2] < 140 and e.bbox[3] > 120]
    print(f"overlap rule vs an area event: {len(inside)} storms checked, "
          f"conflicts: {sum(S._overlaps(e, area) for e in inside)} (must be 0)")

    # public JSON: NaN-free, 5-slot track rows, the keys the dashboard reads
    blob = json.dumps([e.to_dict() for e in top[:5]], allow_nan=False)
    row = json.loads(blob)[0]
    need = {"track", "intensity_units", "peak_value_units", "min_pressure_hpa",
            "peak_category", "name", "basin_name", "fix_step_h"}
    assert need <= set(row["details"]), sorted(need - set(row["details"]))
    assert all(len(r) == 5 for r in row["details"]["track"])
    print(f"public JSON OK ({len(blob) // 1024} KB / 5 events), largest track "
          f"{max(len(e.details['track']) for e in evs)} fixes")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
