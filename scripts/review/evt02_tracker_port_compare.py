"""Review: the ported footprint/core tracker vs its reference and vs production.

Runs the REAL production scan path (published raw-exceedance lane ->
``ewb_live.events.event_tracker`` -> Events) over the live window and compares
three catalogues of heat/freeze events:

  port       this branch's ``scan_temperature_events`` (SPEC §2)
  reference  aaTman/ewb-events' own CLI output for the same window/lane
             (``/opt/ewb-events/output/events_streak1.json``) — the port must
             agree with this
  published  ``data/results/events.json`` — what the deployed site shows today
             (upstream EWB's blob tracker)

Then it rebuilds the funnel (merge -> rank -> publish gate) with the new
heat/freeze events and the still-live AR events, so the curation impact is
visible before anything is deployed.

Run on the ewb LXC from the scratch checkout:

    cd /opt/ewb-events-live && EWB_LIVE_CACHE=/opt/ewb-events-live/data/cache \
      /opt/ewb-live/.venv/bin/python scripts/review/evt02_tracker_port_compare.py \
        --start 2026-08-11 --end 2026-11-02 \
        --lane /opt/ewb-live/data/results/raw_exceedance \
        --reference /opt/ewb-events/output/events_streak1.json \
        --published /opt/ewb-live/data/results/events.json \
        --out /tmp/evt02.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402

from ewb_live.events.select import _bbox_iou, rank_and_cap  # noqa: E402
from ewb_live.events.temperature import scan_temperature_events  # noqa: E402
from ewb_live.events.types import Event  # noqa: E402
from ewb_live.events.validate import validate_event  # noqa: E402

TYPES = ("heat_wave", "freeze")


def _days(a0: dt.date, a1: dt.date, b0: dt.date, b1: dt.date) -> int:
    """Overlap in days of two [start, end) windows."""
    return max(0, (min(a1, b1) - max(a0, b0)).days)


def _match(port: list[dict], other: list[dict], min_days=3, min_iou=0.15):
    """Greedy best-overlap matching (longest port events claim first)."""
    pairs, used = [], set()
    for i, a in sorted(enumerate(port), key=lambda kv: -kv[1]["days"]):
        best, best_key = None, None
        for j, b in enumerate(other):
            if j in used or a["hazard"] != b["hazard"]:
                continue
            od = _days(a["start"], a["end"], b["start"], b["end"])
            iou = _bbox_iou(a["bbox"], b["bbox"])
            if od < min_days or iou < min_iou:
                continue
            key = (od, iou)
            if best_key is None or key > best_key:
                best, best_key = j, key
        if best is not None:
            used.add(best)
            pairs.append((i, best))
    return pairs, {i for i in range(len(port))} - {i for i, _ in pairs}, \
        {j for j in range(len(other))} - used


def _summarise(tag: str, evs: list[dict]) -> None:
    for t in TYPES:
        sub = [e for e in evs if e["hazard"] == t]
        if not sub:
            print(f"{tag:>10} {t:<10} n=0")
            continue
        dur = np.array([e["days"] for e in sub])
        pk = np.array([e["peak"] for e in sub])
        print(f"{tag:>10} {t:<10} n={len(sub):<4} days med={np.median(dur):.0f} "
              f"p90={np.percentile(dur, 90):.0f} max={dur.max()}  "
              f"peak med={np.median(pk) / 1e3:.1f}M km2 max={pk.max() / 1e3:.1f}M")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--lane", default="data/results/raw_exceedance")
    ap.add_argument("--reference", default="")
    ap.add_argument("--published", default="")
    ap.add_argument("--out", default="/tmp/evt02.json")
    ap.add_argument("--top-n", type=int, default=15)
    a = ap.parse_args()
    start = dt.date.fromisoformat(a.start)
    end = dt.date.fromisoformat(a.end)

    # ---------------------------------------------------------------- port ---
    port: list[dict] = []
    events_new: list[Event] = []
    for et in TYPES:
        evs = scan_temperature_events(start, end, et, frames_dir=Path(a.lane))
        events_new += evs
        for e in evs:
            port.append({
                "id": e.id, "hazard": e.event_type,
                "start": e.start.date(), "end": e.end.date(),
                "days": (e.end - e.start).days, "bbox": tuple(e.bbox),
                "peak": float(e.details.get("peak_area_km2", 0.0)),
                "qualified": e.details.get("qualified_date"),
                "ongoing": bool(e.details.get("ongoing")),
                "truncated": bool(e.details.get("window_start_truncated")),
            })
    print(f"\n== port (SPEC \u00a72 tracker) {start}..{end}: {len(port)} events ==")

    # ----------------------------------------------------------- reference ---
    ref: list[dict] = []
    if a.reference and Path(a.reference).exists():
        raw = json.loads(Path(a.reference).read_text())
        rows = raw["events"] if isinstance(raw, dict) else raw
        w = raw["window"] if isinstance(raw, dict) else {"start": a.start, "end": a.end}
        if isinstance(raw, dict):
            print(f"  reference window {w['start']}..{w['end']}")
            print(f"  reference params {raw['params']}")
        for r in rows:
            s = dt.date.fromisoformat(r["start"])
            # open events: the reference reports end=null, we report the day
            # after the lane's last day (both "still active at the window end")
            e = (dt.date.fromisoformat(r["end"]) if r.get("end")
                 else dt.date.fromisoformat(w["end"]) + dt.timedelta(days=1))
            b = r["bbox"]
            ref.append({
                "hazard": r["hazard"], "start": s, "end": e,
                "days": (e - s).days,
                "bbox": (b["lat_min"], b["lat_max"], b["lon_min"], b["lon_max"]),
                "peak": float(r.get("peak_area_km2", 0.0)),
            })

    # ----------------------------------------------------------- published ---
    pub: list[dict] = []
    ars: list[Event] = []
    if a.published and Path(a.published).exists():
        for d in json.loads(Path(a.published).read_text()):
            if d["event_type"] not in TYPES:
                if a.start <= d["start"][:10] <= a.end:
                    ars.append(Event.from_dict(d))
                continue
            e = Event.from_dict(d)
            pub.append({
                "id": e.id, "hazard": e.event_type,
                "start": e.start.date(), "end": e.end.date(),
                "days": (e.end - e.start).days, "bbox": tuple(e.bbox),
                "peak": float(e.details.get("peak_area_km2", 0.0)),
            })

    _summarise("port", port)
    if ref:
        _summarise("reference", ref)
    if pub:
        _summarise("published", pub)

    # the port must reproduce the reference EXACTLY on (hazard, start, end);
    # boxes differ by half a cell (the reference reports cell edges, we report
    # cell centres), so boxes are only used to pair leftovers
    if ref:
        print("\n-- port vs reference: (hazard, start, end) triples --")
        pkey = {(e["hazard"], e["start"], e["end"]) for e in port}
        rkey = {(e["hazard"], e["start"], e["end"]) for e in ref}
        for t in TYPES:
            both = {k for k in pkey & rkey if k[0] == t}
            print(f"  {t:<10} identical {len(both):>3} | port-only "
                  f"{len({k for k in pkey - rkey if k[0] == t}):>3} | "
                  f"reference-only {len({k for k in rkey - pkey if k[0] == t}):>3}")
        for tag, keys, pool in (("port-only", pkey - rkey, port),
                                ("reference-only", rkey - pkey, ref)):
            for k in sorted(keys)[:12]:
                row = next(e for e in pool
                           if (e["hazard"], e["start"], e["end"]) == k)
                near = max((e for e in (ref if tag == "port-only" else port)
                            if e["hazard"] == k[0]),
                           key=lambda e: (_days(k[1], k[2], e["start"], e["end"]),
                                          _bbox_iou(row["bbox"], e["bbox"])),
                           default=None)
                note = (f"nearest {near['start']}->{near['end']} "
                        f"iou={_bbox_iou(row['bbox'], near['bbox']):.2f}"
                        if near else "no partner")
                print(f"    {tag:<15} {k[0]} {k[1]} -> {k[2]} "
                      f"({(k[2] - k[1]).days}d) {note}")

    for other, tag in ((pub, "published"),):
        if not other:
            continue
        print(f"\n-- port vs {tag} --")
        for t in TYPES:
            pi, oi, mi = [e for e in port if e["hazard"] == t], \
                [e for e in other if e["hazard"] == t], None
            pairs, only_p, only_o = _match(pi, oi)
            print(f"  {t:<10} matched {len(pairs):>3} | port-only {len(only_p):>3} "
                  f"| {tag}-only {len(only_o):>3}")
            for i in sorted(only_p)[:8]:
                print(f"    + port: {pi[i]['start']} -> {pi[i]['end']} "
                      f"{pi[i]['days']:>3}d peak {pi[i]['peak'] / 1e3:.1f}M "
                      f"box [{pi[i]['bbox'][0]:.1f},{pi[i]['bbox'][1]:.1f},"
                      f"{pi[i]['bbox'][2]:.1f},{pi[i]['bbox'][3]:.1f}]")
            for j in sorted(only_o)[:8]:
                print(f"    - {tag}: {oi[j]['start']} -> {oi[j]['end']} "
                      f"{oi[j]['days']:>3}d peak {oi[j]['peak'] / 1e3:.1f}M "
                      f"box [{oi[j]['bbox'][0]:.1f},{oi[j]['bbox'][1]:.1f},"
                      f"{oi[j]['bbox'][2]:.1f},{oi[j]['bbox'][3]:.1f}]")

    # ------------------------------------------------------ publish gate -----
    print("\n-- publish gate on the new heat/freeze catalogue --")
    rejects = [(e.id, e.event_type, p) for e in events_new for p in validate_event(e)]
    for r in rejects[:10]:
        print("  REJECT", r)
    print(f"  rejects: {len(rejects)} of {len(events_new)} events")

    # ------------------------------------------------------------- funnel ----
    if ars:
        curated = rank_and_cap(events_new + ars, a.top_n)
        print(f"\n-- curation (merge -> rank -> cap {a.top_n}) --")
        print("  curated:", dict(Counter(e.event_type for e in curated)))
        by_type_new = {e.id: e for e in curated}
        prod_sel = [Event.from_dict(d) for d in
                    json.loads(Path(a.published).read_text())] if a.published else []
        print(f"  {'type':<12} {'start':<11} {'days':>4} {'peak':>9}  "
              f"{'production partner':<20}")
        for e in sorted(curated, key=lambda e: (e.event_type, e.start)):
            best, biou = None, 0.0
            for p in prod_sel:
                if p.event_type != e.event_type:
                    continue
                od = _days(e.start.date(), e.end.date(),
                           p.start.date(), p.end.date())
                iou = _bbox_iou(e.bbox, p.bbox)
                if od >= 3 and iou > biou:
                    best, biou = p, iou
            print(f"  {e.event_type:<12} {e.start:%Y-%m-%d}  "
                  f"{(e.end - e.start).days:>3} "
                  f"{(e.peak_value or 0):>8.1f}C  "
                  + (f"{best.start:%Y-%m-%d}..{best.end:%Y-%m-%d} iou={biou:.2f}"
                     if best else "NEW (no production partner)"))
        Path(a.out).write_text(json.dumps({
            "window": [a.start, a.end],
            "port": [{**p, "start": str(p["start"]), "end": str(p["end"]),
                      "bbox": list(p["bbox"])} for p in port],
            "curated": [e.to_dict() for e in curated],
        }, indent=1, default=str))
        print(f"  wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
