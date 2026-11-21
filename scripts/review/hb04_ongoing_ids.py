"""Ongoing-event id stability across weekly runs, shipped rules vs C3 (hb-implement).

The weekly pipeline re-detects the whole 90-day window and rebuilds
`data/results/events.json` from scratch; `make_event_id` hashes
(type, start, ID BOX) and an ongoing event's id lives exactly as long as its
id box and start do not move. This script measures that, per ruleset, on the
real lane: replay window W1 = start..E, the "next weekly run" W2 =
start..E+7 (extend at the same head), and the window-HEAD SLIDES start+k..
E+k for k in --slides (7/14/21 d - one per weekly run; the slid head is what
re-keys window-edge events), and for the events that are `ongoing` at
W1's end reports how many survive each window with the same id / start / bbox.

Two ID bases are measured, because the shipped code (temperature.py,
2026-11-20) hashes the ANCHOR bbox (the tracker box on day 3, frozen) for
heat/freeze, not the published rectangle:
  anchor   = make_event_id over the ANCHOR bbox (what production ships;
             falls back to the published box when anchor_day=None)
  published = make_event_id over the published box (the pre-2026-11-20 rule,
             kept as the comparison the reviewer asked for)

Rulesets (production code, no review-copy):
  shipped = Params(claim_valid_only=False, seed_valid_only=False,
                   merge_keep_box=False, publish_box="tracker")
  c3      = Params()  (the new defaults)

LXC-only (reads lane frames). Production /opt/ewb-live is read-only here.

Run on the ewb LXC (scratch tree):
  cd /opt/ewb-heatbox && EWB_LIVE_CACHE=/opt/ewb-heatbox/data/cache \
    /opt/ewb-live/.venv/bin/python scripts/review/hb04_ongoing_ids.py \
      --lane /opt/ewb-live/data/results/raw_exceedance \
      --start 2026-08-21 --end 2026-11-06

Rulesets are the pre-C3 shipped rule, C3 with the core counted from the start
day, and C3 + the core clock/anchor freeze starting at qualification (the
owner's 2026-11-21 decision, now production ``Params()``) - the last is the one
whose id stability matters, because the anchor box is the id basis and it now
freezes later for a growing spell.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))     # the scratch checkout's ewb_live,
                                              # not the production one

from ewb_live.events import frame_masks
from ewb_live.events.event_tracker import (
    Params,
    persistence_box,
    persistence_k_days,
    track_events,
)
from ewb_live.events.temperature import _cell_area_km2
from ewb_live.events.types import make_event_id, normalize_lon_180

RULESETS = {
    # the pre-C3 shipped rule (whole-region claim/seed, box-union merge, core
    # counted from the candidate's start day, tracker box published)
    "shipped": dataclasses.replace(Params(), claim_valid_only=False,
                                   seed_valid_only=False, merge_keep_box=False,
                                   core_clock="start", publish_box="tracker"),
    # C3 with the core counted from the start day (the 2026-11-20 rule)
    "c3_core_from_start": dataclasses.replace(Params(), core_clock="start"),
    # C3 + the core clock and anchor freeze starting at qualification
    # (owner 2026-11-21) = production Params() now
    "c3_qualify": Params(),
}


def published(dates, mask, cell_area, lats, lons, params, hazard="heat_wave"):
    """The Events a scan would publish: (id, ids, start, end, bbox, ongoing).

    Mirrors the bbox/id mapping in scan_temperature_events (cell centres, the
    wrap convention, and the ANCHOR-bbox id basis - the tracker box on day 3,
    with the published box as the anchor_day=None fallback - without the
    truth reads this measurement does not need). ``ids`` carries both bases:
    "anchor" (shipped) and "published" (pre-2026-11-20 rule).
    """
    evs = track_events(mask, dates, cell_area, params, hazard)
    ny, nx = mask.shape[1:]
    out = []
    for ev in evs:
        d0 = dates.index(ev.start)
        d1 = len(dates) - 1 if ev.end is None else dates.index(ev.end - dt.timedelta(days=1))
        pub = ev.box
        if params.publish_box == "persistence":
            pub = persistence_box(mask[d0:d1 + 1], ev.box, cell_area,
                                  persistence_k_days(d1 - d0 + 1,
                                                     params.publish_min_fraction),
                                  params.publish_trim) or ev.box
        bbox = (float(lats[pub.i0]), float(lats[pub.i1]),
                normalize_lon_180(float(lons[pub.j0])),
                normalize_lon_180(float(lons[pub.j1])))
        id_box = ev.anchor if ev.anchor is not None else pub
        anchor_bbox = (float(lats[id_box.i0]), float(lats[id_box.i1]),
                       normalize_lon_180(float(lons[id_box.j0])),
                       normalize_lon_180(float(lons[id_box.j1])))
        start_dt = dt.datetime.combine(ev.start, dt.time())
        ids = {"anchor": make_event_id(hazard, start_dt, anchor_bbox),
               "published": make_event_id(hazard, start_dt, bbox)}
        out.append({"id": ids["anchor"], "ids": ids,
                    "start": ev.start, "end": ev.end, "bbox": bbox,
                    "ongoing": ev.end is None,
                    "box_km2": float(cell_area[pub.mask(ny, nx)].sum())})
    return out


def lon_arc(b) -> float:
    return (b[3] - b[2]) % 360.0 or 360.0


def overlaps(a, b) -> bool:
    """Same spell? start within 1 d of each other and boxes intersect."""
    if abs((a["start"] - b["start"]).days) > 1:
        return False
    lat = min(a["bbox"][1], b["bbox"][1]) - max(a["bbox"][0], b["bbox"][0])
    if lat <= 0:
        return False
    for l0, l1 in ([(a["bbox"][2], a["bbox"][3])] if a["bbox"][2] <= a["bbox"][3]
                   else [(a["bbox"][2], 180.0), (-180.0, a["bbox"][3])]):
        for m0, m1 in ([(b["bbox"][2], b["bbox"][3])] if b["bbox"][2] <= b["bbox"][3]
                       else [(b["bbox"][2], 180.0), (-180.0, b["bbox"][3])]):
            if min(l1, m1) - max(l0, m0) > 0:
                return True
    return False


def bbox_intersects(a, b) -> bool:
    """Plain bbox intersection (wrap-aware), no start-tolerance test."""
    lat = min(a["bbox"][1], b["bbox"][1]) - max(a["bbox"][0], b["bbox"][0])
    if lat <= 0:
        return False
    for l0, l1 in ([(a["bbox"][2], a["bbox"][3])] if a["bbox"][2] <= a["bbox"][3]
                   else [(a["bbox"][2], 180.0), (-180.0, a["bbox"][3])]):
        for m0, m1 in ([(b["bbox"][2], b["bbox"][3])] if b["bbox"][2] <= b["bbox"][3]
                       else [(b["bbox"][2], 180.0), (-180.0, b["bbox"][3])]):
            if min(l1, m1) - max(l0, m0) > 0:
                return True
    return False


def report(name: str, w1: list[dict], w2: list[dict]) -> None:
    ongoing = [e for e in w1 if e["ongoing"]]
    same_id = same_pub = same_start = same_bbox = matched = lost = 0
    for e in ongoing:
        succ = [f for f in w2 if overlaps(e, f)]
        if not succ:
            lost += 1
            continue
        matched += 1
        same_id += any(f["ids"]["anchor"] == e["ids"]["anchor"] for f in succ)
        same_pub += any(f["ids"]["published"] == e["ids"]["published"]
                        for f in succ)
        same_start += any(f["start"] == e["start"] for f in succ)
        same_bbox += any(f["bbox"] == e["bbox"] for f in succ)
    print(f"{name}: ongoing@W1end={len(ongoing)} matched-in-W2={matched} "
          f"lost={lost} same-id(ANCHOR,shipped)={same_id} "
          f"same-id(published,old)={same_pub} same-start={same_start} "
          f"same-bbox={same_bbox}", flush=True)
    for e in ongoing:
        succ = [f for f in w2 if overlaps(e, f)]
        tag = "KEPT" if any(f["ids"]["anchor"] == e["ids"]["anchor"]
                            for f in succ) else (
            "REKEYED" if succ else "LOST")
        print(f"   {tag:8s} {e['id']} {e['start']} box {e['box_km2'] / 1e6:6.2f}M "
              + (f"-> {sorted({f['ids']['anchor'] for f in succ})} "
                 f"box {sorted(round(f['box_km2'] / 1e6, 2) for f in succ)}M"
                 if succ else ""), flush=True)
        if not succ:
            # why lost: a pairable-in-space object that starts further than
            # the +-1 d pairing tolerance is the spell living on under another
            # start (absorbed by a merge / re-seeded), not an id re-key
            near = [f for f in w2 if bbox_intersects(e, f)
                    and (e["end"] is None or f["start"] <= e["end"])
                    and (f["end"] is None or f["end"] >= e["start"])]
            for f in sorted(near, key=lambda f: abs((f["start"] - e["start"]).days))[:3]:
                print(f"      near: {f['ids']['anchor']} start {f['start']} "
                      f"({(f['start'] - e['start']).days:+d} d) "
                      f"end {f['end'] or 'open'} box "
                      f"{f['box_km2'] / 1e6:6.2f}M", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", type=Path, required=True)
    ap.add_argument("--start", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--end", type=dt.date.fromisoformat, required=True,
                    help="W1 end; W2 = start..end+7d and each slide k in "
                         "--slides: start+k..end+k (the real weekly move)")
    ap.add_argument("--slides", default="7,14,21",
                    help="window-head slide offsets (days) to measure the "
                         "re-key rate at - one per weekly run")
    a = ap.parse_args()
    slides = [int(k) for k in a.slides.split(",") if k.strip()]
    dates, lats, lons, masks = frame_masks.daily_masks(a.lane)
    mask_all = masks["heat_wave"]
    keep = (lats >= -60.0) & (lats <= 90.0)      # production lat clamp
    lats, mask_all = lats[keep], mask_all[:, keep]
    cell_area = _cell_area_km2(lats, float(abs(lons[1] - lons[0])), lons.size)
    e2 = a.end + dt.timedelta(days=7)
    win1 = [i for i, d in enumerate(dates) if a.start <= d <= a.end]
    win2 = [i for i, d in enumerate(dates) if a.start <= d <= e2]
    winsl: dict[int, list[int]] = {
        k: [i for i, d in enumerate(dates)
            if a.start + dt.timedelta(days=k) <= d <= a.end + dt.timedelta(days=k)]
        for k in slides}
    for win in [win1, win2, *winsl.values()]:
        assert len(win) >= 14, "lane does not cover a window"
    for name, params in RULESETS.items():
        def run(sel, prm=params):
            sel = list(sel)
            return published([dates[i] for i in sel], mask_all[sel],
                             cell_area, lats, lons, prm)
        w1 = run(win1)
        print(f"== {name} | W1 {a.start}..{a.end} ({len(w1)} events) | "
              f"W2 {a.start}..{e2} | "
              + " | ".join(
                  f"slide+{k} {a.start + dt.timedelta(days=k)}.."
                  f"{a.end + dt.timedelta(days=k)}" for k in slides),
              flush=True)
        report(" same-start W2 ", w1, run(win2))
        for k, win in winsl.items():
            report(f" slid-start +{k}d", w1, run(win))
    return 0


if __name__ == "__main__":
    sys.exit(main())
