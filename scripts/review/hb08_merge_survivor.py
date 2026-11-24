"""Round-4 merge-survivor measurement: the M1/M2/M3 candidates vs the gates.

The third adversarial review showed a merge deletes larger, healthy qualified
events (``heat_wave-20260917-321``: 3.62M km2, qualified at its seed day,
absorbed on 09-20 by a 0.48M km2 event that itself ended the next day; the
dome re-seeded as a fresh object and its history was never published). This
script replays the production lane window (lat >= -60, the freeze ceiling -
all through ``hb01_box_growth.load_window``) once per candidate rule and
measures the round-4 gates per hazard (heat_wave, freeze, cold_snap):

  current  Params() - the shipped oldest-qualified survivor (reference)
  legacy   the production LEGACY fingerprint (whole-region claim, box union,
           start clock) - for the real-data G6 identity check
  M1       merge_survivor="largest"         survivor = largest claim today
  M2       merge_survivor="largest_close"   M1 + aged qualified members are
                                            closed and PUBLISHED, not absorbed
  M3       merge_survivor="separate"        qualified events never merge;
                                            contested cells go to the larger

Gates (brief ``fix_round4.md``; G3 is the synthetic unit test and the
flood/legacy fuzz goldens in tests/, but the real-data part of G4/G5 and the
whole of G1/G2 are measured here):

  G1  the central-Africa dome (region lat -12..22, lon 15..42, the reviewer's
      ``rev3_trace.py`` region) is ONE heat event starting no later than
      09-17 and running to about 10-06;
  G2  no qualified event of >= min_days days disappears unpublished unless a
      same-object successor (same hazard, same start day, overlapping
      tracker box) carries its start date;
  G4  0 sub-floor events (qualifying-day footprint and peak >= the qualify
      floor) and start <= qualified < end on every published event;
  G5  published-fill median >= 0.75 and published-box median <= 1.5M km2.

Ranking statistics per rule: unique qualified members absorbed without
publication, absorbed members whose cells re-seeded within 3 days, and the
event-count change versus the ``current`` rule.

Running it with the HEAD code (a ``git archive`` snapshot of the branch tip
next to this scratch checkout) for ``--rules current,legacy`` and diffing the
digests is the real-data G6 check: the shipped default and the LEGACY replay
must be event-for-event identical to HEAD. HEAD has no ``merge_survivor``
field, so the M-rules are skipped there automatically.

LXC-only (reads the lane + the ERA5 truth mirror read-only). Run detached,
one hazard per job:
  cd /opt/ewb-heatbox && EWB_LIVE_CACHE=/opt/ewb-heatbox/data/cache \
    nohup /opt/ewb-live/.venv/bin/python scripts/review/hb08_merge_survivor.py \
      --lane /opt/ewb-live/data/results/raw_exceedance \
      --start 2026-08-21 --end 2026-11-13 --hazards heat_wave \
      --out /opt/ewb-heatbox/out/hb08_heat.json \
      > /opt/ewb-heatbox/out/hb08_heat.log 2>&1 &
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))     # the repo root
sys.path.insert(0, str(_HERE.parent))         # scripts/review, for hb01

import hb01_box_growth as h1  # noqa: E402  (path bootstrap above)
from ewb_live.events import event_tracker as ET  # noqa: E402
from ewb_live.events.event_tracker import (  # noqa: E402
    Box,
    Params,
    TrackedEvent,
    Tracker,
    persistence_k_days,
)

RULES = ("current", "legacy", "M1", "M2", "M3")

# the round-4 case study: the central-Africa dome (rev3_trace.py's region)
AFRICA = (-12.0, 22.0, 15.0, 42.0)
G1_START = dt.date(2026, 9, 17)
G1_END = dt.date(2026, 10, 6)


def make_params(rule: str) -> Params | None:
    """The Params of one candidate rule; None when HEAD has no such knob."""
    if rule == "legacy":  # the production legacy fingerprint (as tests/)
        return dataclasses.replace(
            Params(), claim_valid_only=False, seed_valid_only=False,
            merge_keep_box=False, core_clock="start")
    if rule == "current":
        return Params()
    knob = {"M1": "largest", "M2": "largest_close", "M3": "separate"}[rule]
    if "merge_survivor" not in {f.name for f in dataclasses.fields(Params)}:
        return None
    return dataclasses.replace(Params(), merge_survivor=knob)


class ProbeTracker(Tracker):
    """Records the merge absorptions and the seeds of each day.

    ``absorbed``: one row per member a merge group did NOT carry on, with the
    state it had BEFORE the day's advance (age/claim are pre-merge, which is
    what G2 asks about). ``seeded``: the box of every candidate seeded each
    day (a seed is always open right after the step that seeded it), used to
    count how soon an absorbed member's cells re-seeded.
    """

    def __init__(self, *args, **kw) -> None:
        super().__init__(*args, **kw)
        self.day = ""
        self.absorbed: list[dict] = []
        self.seeded: list[dict] = []

    def step(self, day: dt.date, valid: np.ndarray) -> list[TrackedEvent]:
        self.day = str(day)
        ended = super().step(day, valid)
        for ev in self.active:
            if ev.start == day:
                self.seeded.append({"day": self.day, "id": ev.id,
                                    "box": [ev.box.i0, ev.box.i1,
                                            ev.box.j0, ev.box.j1]})
        return ended

    def _group_units(self, group, hits, labels, valid, weighted):
        units = super()._group_units(group, hits, labels, valid, weighted)
        for sv, absorbed in units:
            ev = self.active[sv]
            sv_claim = float(weighted[self.claim(
                ev, hits[sv], labels, valid)].sum())
            for k in absorbed:
                o = self.active[k]
                self.absorbed.append({
                    "day": self.day, "hazard": self.hazard,
                    "id": o.id, "start": str(o.start),
                    "qualified": str(o.qualified) if o.qualified else None,
                    "age_days": o.duration_days,
                    "claim_km2": round(float(weighted[self.claim(
                        o, hits[k], labels, valid)].sum()), 1),
                    "box": [o.box.i0, o.box.i1, o.box.j0, o.box.j1],
                    "survivor": ev.id, "survivor_start": str(ev.start),
                    "survivor_claim_km2": round(sv_claim, 1),
                })
        return units


def measure(w: h1.Window, valid: list[TrackedEvent],
            p: Params) -> list[dict]:
    """Fill/box metrics of every valid event, published as production would."""
    idx = {d: k for k, d in enumerate(w.dates)}
    rows = []
    for ev in valid:
        i0 = idx[ev.start]
        i1 = (idx[ev.end - dt.timedelta(days=1)] if ev.end
              else len(w.dates) - 1)
        k_days = persistence_k_days(i1 - i0 + 1, p.publish_min_fraction)
        pub = ET.persistence_box(w.mask[i0:i1 + 1], ev.box, w.cell_area,
                                 k_days, p.publish_trim) or ev.box
        spell = h1.Spell(w.hazard, ev.id, ev.start, ev.end, ev.qualified,
                         i0, i1, ev.box, ev.peak_area_km2, ev.end is None)
        row = h1.score_one(w, h1.Published(spell=spell, box=pub))
        row["tracker_box"] = [ev.box.i0, ev.box.i1, ev.box.j0, ev.box.j1]
        # score_one's "end" is the last RECORDED day (inclusive); G4 and the
        # digest need the tracker's exclusive end (None = ongoing)
        row["end_exclusive"] = str(ev.end) if ev.end else None
        q_idx = (ev.qualified - ev.start).days if ev.qualified else None
        row["qual_day_footprint_km2"] = (
            ev.footprint_area_km2[q_idx]
            if q_idx is not None and 0 <= q_idx < len(ev.footprint_area_km2)
            # no in-series qualifying day: the LEGACY merge could inherit a
            # qualification dated BEFORE the survivor's own start (the
            # round-3 finding) - a violation to report, not a crash
            else None)
        rows.append(row)
    return rows


def box_overlap(a: list[int], b: list[int], nx: int) -> bool:
    """Tracker-box overlap with longitude wrap (grid-index boxes)."""
    if a[1] < b[0] or b[1] < a[0]:
        return False
    return bool((Box(*a).columns(nx) & Box(*b).columns(nx)).any())


def g2_violations(rows_pub: list[dict], absorbed: list[dict], p: Params,
                  nx: int) -> list[dict]:
    """Qualified events of >= min_days days that vanished without publication.

    Carried by a "same-object successor" = a published event of the same
    hazard with the SAME start day whose tracker box overlaps the absorbed
    member's box - the merge may fold the object into a survivor, but if the
    start date and the cells live on in a published event, no history was
    deleted. Only when no such event exists is the spell lost.
    """
    out = []
    seen = set()
    for ab in absorbed:
        if (ab["qualified"] is None or ab["age_days"] < p.min_days
                or ab["id"] in seen):
            continue
        carried = any(r["start"] == ab["start"]
                      and box_overlap(r["tracker_box"], ab["box"], nx)
                      for r in rows_pub)
        if not carried:
            out.append(ab)
            seen.add(ab["id"])
    return out


def g4_violations(rows_pub: list[dict], p: Params) -> list[dict]:
    out = []
    for r in rows_pub:
        fp = r["qual_day_footprint_km2"]
        ok = (fp is not None and fp >= p.qualify_area_km2
              and r["peak_km2"] >= p.qualify_area_km2
              and r["qualified"] is not None and r["qualified"] >= r["start"]
              and (r["end_exclusive"] is None
                   or r["qualified"] < r["end_exclusive"]))
        if not ok:
            out.append({k: r[k] for k in
                        ("id", "start", "qualified", "end_exclusive",
                         "qual_day_footprint_km2", "peak_km2")})
    return out


def g1_dome(w: h1.Window, rows_pub: list[dict]) -> dict:
    """The central-Africa dome: exactly one event, 09-17 .. about 10-06."""
    la0, la1, lo0, lo1 = AFRICA
    hits = []
    for r in rows_pub:
        b = r["bbox"]  # lat0, lat1, lon0, lon1 (cell centres, wrapped box: lon0 > lon1)
        lat_ok = b[1] >= la0 and b[0] <= la1
        lon_ok = not (b[3] < lo0 or b[2] > lo1) if b[2] <= b[3] else True
        if not (lat_ok and lon_ok):
            continue
        s = dt.date.fromisoformat(r["start"])
        e = (dt.date.fromisoformat(r["end"]) if r["end"]
             else w.dates[-1] + dt.timedelta(days=1))
        if (min(e - dt.timedelta(days=1), G1_END)
                - max(s, G1_START)).days >= 3:
            hits.append(r)
    if len(hits) != 1:
        ok, why = False, f"{len(hits)} events overlap the dome window"
    else:
        r = hits[0]
        end_ok = r["end"] is None or dt.date.fromisoformat(r["end"]) >= G1_END
        ok = dt.date.fromisoformat(r["start"]) <= G1_START and end_ok
        why = f"start {r['start']} end {r['end']}"
    return {"pass": ok, "detail": why,
            "events": [{"id": r["id"], "start": r["start"], "end": r["end"],
                        "bbox": [round(x, 1) for x in r["bbox"]],
                        "peak_km2": r["peak_km2"]} for r in hits]}


def reseed_count(absorbed: list[dict], seeded: list[dict], nx: int,
                 dates: list[dt.date]) -> tuple[int, int]:
    """Seeds within 0-3 days of a merge whose box hits an absorbed box.

    Returns (all absorbed members re-seeded, qualified ones re-seeded).
    """
    di = {str(d): k for k, d in enumerate(dates)}
    res_all = res_q = 0
    for ab in absorbed:
        hit = any(0 <= di[s["day"]] - di[ab["day"]] <= 3
                  and box_overlap(ab["box"], s["box"], nx) for s in seeded)
        if hit:
            res_all += 1
            if ab["qualified"] is not None:
                res_q += 1
    return res_all, res_q


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", type=Path, required=True)
    ap.add_argument("--start", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--end", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--hazards", default="heat_wave,freeze,cold_snap")
    ap.add_argument("--rules", default=",".join(RULES))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    out: dict = {}
    for hazard in [h.strip() for h in a.hazards.split(",") if h.strip()]:
        t0 = time.monotonic()
        w = h1.load_window(a.lane, a.start, a.end, hazard)
        nx = w.mask.shape[2]
        out[hazard] = {}
        base = 0
        for rule in [r.strip() for r in a.rules.split(",") if r.strip()]:
            p = make_params(rule)
            if p is None:
                print(f"{hazard} {rule}: no merge_survivor on this code",
                      flush=True)
                continue
            tr = ProbeTracker(w.cell_area, p, hazard)
            ended: list[TrackedEvent] = []
            for day, frame in zip(w.dates, w.mask, strict=True):
                ended += tr.step(day, frame)
            valid = [ev for ev in ended + tr.active if ev.is_valid(p)]
            rows_pub = measure(w, valid, p)
            g2 = g2_violations(rows_pub, tr.absorbed, p, nx)
            g4 = g4_violations(rows_pub, p)
            fills = [r["fill"] for r in rows_pub]
            boxes = [r["box_km2"] for r in rows_pub]
            res_all, res_q = reseed_count(tr.absorbed, tr.seeded, nx, w.dates)
            if rule == "current":
                base = len(rows_pub)
            row = {
                "events": len(rows_pub),
                "events_delta_vs_current": len(rows_pub) - base,
                "fill_median": round(float(np.median(fills)), 3) if fills else 0,
                "box_median_km2": round(float(np.median(boxes))) if boxes else 0,
                "g4_pass": not g4,
                "g4_violations": g4,
                "g2_violations": g2,
                "g2_count": len(g2),
                "absorbed_qualified_unique": len({ab["id"] for ab in tr.absorbed
                                                  if ab["qualified"]}),
                "absorbed_rows": tr.absorbed,
                "reseed_all": res_all,
                "reseed_qualified": res_q,
                "g5_pass": (bool(fills) and float(np.median(fills)) >= 0.75
                            and float(np.median(boxes)) <= 1.5e6),
                "digest": hashlib.sha256(json.dumps(
                    [[r["id"], r["start"], r["end"], r["bbox"],
                      r["tracker_box"], r["peak_km2"],
                      r["qual_day_footprint_km2"]] for r in
                    sorted(rows_pub, key=lambda r: (r["start"], r["id"]))
                ]).encode()).hexdigest(),
            }
            if hazard == "heat_wave":
                row["g1"] = g1_dome(w, rows_pub)
            out[hazard][rule] = row
            print(f"{hazard:10s} {rule:8s} evts={row['events']:4d} "
                  f"fill={row['fill_median']:.2f} "
                  f"boxmed={row['box_median_km2'] / 1e6:.2f}M "
                  f"g2={row['g2_count']} "
                  f"lostQ={row['absorbed_qualified_unique']} "
                  f"reseed(q)={res_q} g4={row['g4_pass']} "
                  f"g5={row['g5_pass']}"
                  + (f" g1={row['g1']['pass']}" if hazard == "heat_wave" else "")
                  + f" [{time.monotonic() - t0:.0f}s]", flush=True)
        print(f"{hazard}: {w.mask.shape[1]}x{nx} grid, {len(w.dates)} days, "
              f"{time.monotonic() - t0:.0f}s total", flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1))
    print(f"wrote {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
