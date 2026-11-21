"""Real-data check of the round-3 merge-survivor fix: 0 sub-floor events.

Replays the production lane window at the production population (lat >= -60,
the freeze ceiling - all through ``hb01_box_growth.load_window``) with the
CURRENT production rule (``Params()`` = C3 + core-from-qualification + the
merge-survivor fix) and audits every valid event of heat_wave, freeze and
cold_snap:

* ``footprint_area_km2[(qualified - start).days] >= qualify_area_km2`` - the
  qualifying day's footprint reached the floor on the event's OWN footprint
  (the pre-fix rule published heat events at 63k-195k km2 this way);
* ``peak_area_km2 >= qualify_area_km2``;
* ``start <= qualified < end`` (open events: ``start <= qualified``).

Exit code 1 on any violation, so the LXC run doubles as the gate the review
asked for ("Real replay at lat >= -60 must show 0 sub-floor events in heat,
freeze and cold_snap").

LXC-only. Run on the ewb LXC (scratch tree):
  cd /opt/ewb-heatbox && EWB_LIVE_CACHE=/opt/ewb-heatbox/data/cache \
    timeout 600 /opt/ewb-live/.venv/bin/python \
      scripts/review/hb07_subfloor.py \
        --lane /opt/ewb-live/data/results/raw_exceedance \
        --start 2026-08-21 --end 2026-11-13 \
        --out /opt/ewb-heatbox/out/hb07_subfloor.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))
sys.path.insert(0, str(_HERE.parent))

import hb01_box_growth as h1  # noqa: E402  (load_window: the replay window, production population)

from ewb_live.events.event_tracker import Params, track_events  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", type=Path, required=True)
    ap.add_argument("--start", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--end", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--hazards", default="heat_wave,freeze,cold_snap")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    p = Params()
    report: dict[str, dict] = {}
    bad_total = 0
    land = None
    for hazard in [h.strip() for h in a.hazards.split(",") if h.strip()]:
        w = h1.load_window(a.lane, a.start, a.end, hazard, land)
        land = w.land
        events = track_events(w.mask, w.dates, w.cell_area, p, hazard)
        viol: list[dict] = []
        for ev in events:
            i = (ev.qualified - ev.start).days
            fp_q = ev.footprint_area_km2[i]
            ok = (fp_q >= p.qualify_area_km2
                  and ev.peak_area_km2 >= p.qualify_area_km2
                  and ev.start <= ev.qualified
                  and (ev.end is None or ev.qualified < ev.end))
            if not ok:
                viol.append({"id": ev.id, "start": str(ev.start),
                             "qualified": str(ev.qualified),
                             "end": str(ev.end),
                             "footprint_at_qualified_km2": round(fp_q, 1),
                             "peak_km2": round(ev.peak_area_km2, 1)})
        report[hazard] = {"n_valid_events": len(events),
                          "n_sub_floor": len(viol), "violations": viol,
                          "peak_km2_min": round(min((ev.peak_area_km2
                                                     for ev in events),
                                                    default=0.0), 1)}
        bad_total += len(viol)
        print(f"{hazard}: {len(events)} valid events, {len(viol)} sub-floor "
              f"(min peak {report[hazard]['peak_km2_min'] / 1e3:.0f}k km2)",
              flush=True)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, indent=1))
    print(f"sub-floor total: {bad_total}", flush=True)
    return 1 if bad_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
