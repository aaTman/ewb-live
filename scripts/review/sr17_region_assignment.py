#!/usr/bin/env python3
"""§S referee check: bbox-centroid region assignment vs footprint-overlap>25%.
Read-only. Run on box: cd /opt/ewb-live && .venv/bin/python scripts/review/sr17_region_assignment.py
For each curated AR event: footprint = plane-2 mask pixels in the event's bbox
over the exact-hour window. Question: which regions hold >=25% of footprint
pixels, and does centroid assignment (regions.assign_regions) disagree?"""
import json, sys
import numpy as np
sys.path.insert(0, ".")
from ewb_live.viz.exceedance import frame_layers, LAT_N, LON_N
from ewb_live.regions import load_regions, assign_regions, bbox_centroid, GLOBAL_KEY

D = "data/results/raw_exceedance"
idx = json.load(open(f"{D}/index.json")); FR = idx["frames"]
ev = json.load(open("data/results/events_selected.json")); ev = ev["events"] if isinstance(ev, dict) else ev
ars = [e for e in ev if e["event_type"] == "atmospheric_river"]
regions = [r for r in load_regions() if r["key"] != GLOBAL_KEY]  # hemispheres stay as candidates on BOTH sides (consistent); global excluded

lat = -90 + 0.25 * np.arange(LAT_N)
lon = -180 + 0.25 * np.arange(LON_N)
ri = {r["key"]: r for r in regions}
# per-region pixel masks (lon wraps eastward like assign_regions)
rmask = {}
for k, r in ri.items():
    la = (lat >= r["lat"][0]) & (lat <= r["lat"][1])
    dlon = (lon - r["lon"][0]) % 360.0
    lo = dlon <= (r["lon"][1] - r["lon"][0])
    rmask[k] = la[:, None] & lo[None, :]

def frame_layers_memo(cache, t):
    if t not in cache:
        cache[t] = frame_layers(open(f"{D}/{t}.bin", "rb").read(), (LAT_N, LON_N))
    return cache[t]

cache = {}
disagree = []
for e in ars:
    b = tuple(e["bbox"])  # [latmin, latmax, lonmin, lonmax]
    t0 = e["start"][:13].replace("-", "").replace("T", "")
    t1 = e["end"][:13].replace("-", "").replace("T", "")
    win = [t for t in FR if t0 <= t <= t1]
    if not win: continue
    la = (lat >= min(b[0], b[1])) & (lat <= max(b[0], b[1]))
    lo = ((lon - b[2]) % 360.0) <= ((b[3] - b[2]) % 360.0)  # eastward arc, AM-safe
    tot, frac, perframe = 0.0, {k: 0.0 for k in ri}, set()
    for t in win:
        m = frame_layers_memo(cache, t)[2] & (la[:, None] & lo[None, :])
        n = int(m.sum())
        if not n: continue
        tot += n
        for k in ri:
            c = int((m & rmask[k]).sum())
            frac[k] += c
            if c / n >= 0.25: perframe.add(k)  # §S.1 per-frame union
    if tot == 0:
        print(e["id"], "ZERO footprint in window (dead-at-t0 class?)"); continue
    top = {k: f / tot for k, f in frac.items() if f / tot >= 0.25}
    cen = set(assign_regions((b[0], b[1], b[2], b[3]), [ri[k] for k in ri])) - {GLOBAL_KEY}
    miss = set(top) - cen; extra = cen - set(top)
    pf_miss = perframe - cen  # regions a frame put >=25% in, centroid misses
    latc, lonc = bbox_centroid((b[0], b[1], b[2], b[3]))
    # stated rule: EXTRA is non-fatal — centroid-padded bboxes (±5°) drift into
    # neighbouring regions by design; only mask-side miss/pf_miss trip G4.
    flag = bool(miss or pf_miss)
    print(f"{e['id']} centroid{latc:.1f},{lonc:.1f}->{sorted(cen)} overlap>=25%:{sorted(top)} "
          f"perframe_union:{sorted(perframe)} MISS:{sorted(miss)} PF_MISS:{sorted(pf_miss)} "
          f"EXTRA:{sorted(extra)} {'DISAGREE' if flag else 'ok'}")
    if flag: disagree.append({"event": e["id"], "centroid_regions": sorted(cen),
                              "overlap_regions": sorted(top), "perframe_miss": sorted(pf_miss)})
print("note: EXTRA (centroid claims a region holding <25% of footprint) is report-only — "
      "the shipped rule IS the centroid; the tripwire fires on overlap regions it MISSES (§S)")
print("GATE_JSON:" + json.dumps({"events": len(ars), "disagree": disagree}))
