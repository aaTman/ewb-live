"""Section I audit: owner AR tropical exclusion + TC co-location, EVERYWHERE.

Rule (SPEC 'AR tropical exclusion', revised 2026-11-08): any AR object with
ANY mask cell between 15S and 15N at any time step is DROPPED, and AR objects
co-located with IBTrACS TC tracks are dropped. Applies to events AND the raw
AR layer on the global map. READ-ONLY on the box; run via ops/remote.sh.

A: raw frames — zero AR pixels with |lat|<15 (and count at exactly ±15, a
   boundary judgment call), and an independent pixel->TC-track minimum
   distance per frame (must exceed TC_SCREEN_KM within TC_SCREEN_HOURS).
B: events — no selected AR bbox intersects the tropics; screen provenance.
C: curation — refill/selection constraints after the rule change.
D: scores — sr11 regression counters + scorecard/parquet reconciliation.
"""
import datetime as dt
import glob
import json
import sys

sys.path.insert(0, ".")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import zlib  # noqa: E402
from ewb_live.events.tc_screen import (  # noqa: E402
    TC_SCREEN_KM, TC_SCREEN_HOURS, load_ibtracs_tracks,
)

R = "data/results"
out = {"utc": dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")}

# ---- A: raw exceedance frames ------------------------------------------
idx = json.load(open(f"{R}/raw_exceedance/index.json"))
ny, nx = idx["grid"]["ny"], idx["grid"]["nx"]
lat0, step = idx["grid"]["lat0"], idx["grid"]["step_lat"]
lats = lat0 + step * np.arange(ny)  # display: row 0 = south
band = np.abs(lats) < 15.0          # strict <15 per contract
exact = np.isclose(np.abs(lats), 15.0)
frames = idx["frames"]
if not frames:  # blank index (all-AR-fail) would report a vacuous clean audit
    raise SystemExit("sr12: index has no frames — refusing to report clean on an empty window")
out["raw"] = {"n_frames": len(frames),
              "cutoff": idx.get("cutoff"),
              "index_generated": idx.get("generated")}
# frame bin: zlib(packbits x3); decode via the canonical publisher's decoder
from ewb_live.viz.exceedance import frame_layers
trk_cache = {}


def _haversine_min_km(la, lo, ta, to):
    la1, lo1 = np.radians(la), np.radians(lo)
    la2, lo2 = np.radians(ta), np.radians(to)
    m = np.inf
    for s in range(0, len(la1), 50_000):  # block to stay ~40 MB/frame
        dla = la2[:, None] - la1[None, s:s + 50_000]
        dlo = lo2[:, None] - lo1[None, s:s + 50_000]
        a = np.sin(dla / 2) ** 2 + np.cos(la2[:, None]) * np.cos(la1[None, s:s + 50_000]) * \
            np.sin(dlo / 2) ** 2
        m = min(m, float((2 * 6371.0 * np.arcsin(np.sqrt(a))).min()))
    return m


worst = None
tropical_px = exact_px = 0
checked = 0
for f in frames:
    t = pd.to_datetime(f, format="%Y%m%d%H")
    ar = frame_layers(open(f"{R}/raw_exceedance/{f}.bin", "rb").read(),
                      (ny, nx))[2]
    tropical_px += int(ar[band].sum())
    exact_px += int(ar[exact].sum())
    ys, xs = np.nonzero(ar)
    if len(ys) == 0:
        continue
    yr = int(t.year)
    if yr not in trk_cache:
        trk_cache[yr] = load_ibtracs_tracks({yr - 1, yr, yr + 1})
    tracks = trk_cache[yr]
    if tracks is None:
        raise SystemExit("sr12: IBTrACS unavailable — refusing to report the TC "
                         "check as clean without the screen's input")
    win = tracks[(pd.to_datetime(tracks["valid_time"]) - t).abs().dt.total_seconds()
                 <= (TC_SCREEN_HOURS + 6) * 3600]  # detector's step-grid tolerance
    if win.empty:
        continue
    checked += 1
    la = lats[ys]
    lo = -180.0 + step * xs
    m = _haversine_min_km(la, lo, win["latitude"].to_numpy(float),
                          win["longitude"].to_numpy(float))
    if worst is None or m < worst["min_km"]:
        worst = {"frame": f, "min_km": round(float(m), 1), "ar_px": len(ys)}
out["raw"]["tropical_ar_pixels_strict_lt15"] = tropical_px
out["raw"]["ar_pixels_at_exactly_pm15"] = exact_px
out["raw"]["tc_closest_frame_any_retained_px"] = worst
out["raw"]["frames_with_ar_and_nearby_tc_checked"] = checked

# ---- B: events ------------------------------------------------------------
sel = json.load(open(f"{R}/events_selected.json"))
evs = sel["events"] if isinstance(sel, dict) else sel
ars = [e for e in evs if e["event_type"] == "atmospheric_river"]


def _bb(e):
    b = e["bbox"]
    return float(b[0]), float(b[1]), float(b[2]), float(b[3])


out["events"] = {"n_ar": len(ars),
                 "ar_bbox_touches_tropics": [
                     e["id"] for e in ars
                     if not (_bb(e)[0] >= 15.0 or _bb(e)[1] <= -15.0)],
                 "ar_with_tc_screen_provenance": sum(
                     1 for e in ars if "tc_screen" in (e.get("details") or {})),
                 "ar_with_tropical_drop_field": sum(
                     1 for e in ars if "tropical_dropped" in (e.get("details") or {})),
                 # seam checks (minimal-arc bbox contract):
                 "bbox_arc_ge_180": [
                     e["id"] for e in ars
                     if ((lambda b: (b[3] - b[2]) % 360.0)(_bb(e)) >= 180.0)],
                 "bbox_crosses_0E": [e["id"] for e in ars
                                     if _bb(e)[2] < 0.0 < _bb(e)[3]],
                 "bbox_wrap_arc_gt180": [
                     e["id"] for e in ars
                     if _bb(e)[2] > _bb(e)[3]
                     and (360.0 - _bb(e)[2] + _bb(e)[3]) > 180.0]}

# ---- C: curation constraints over the AR selection ------------------------
# actual rule (select.py PRODUCT RULE): no two events share >=1 day AND
# bbox area (authoritative select._overlaps); replicate the day+bbox test
def _ov(a, b):
    # timestamps, not calendar dates (matches select._overlaps)
    if min(pd.Timestamp(a["end"]), pd.Timestamp(b["end"])) <= \
            max(pd.Timestamp(a["start"]), pd.Timestamp(b["start"])):
        return False
    la0, la1, lo0, lo1 = _bb(a)
    lb0, lb1, lo0b, lo1b = _bb(b)
    if la1 <= lb0 or lb1 <= la0:
        return False
    def _arc(x0, x1, y0, y1):  # eastward arcs intersect?
        def pieces(x0, x1):  # wrap arc splits at +-180 (seam-fix bbox encoding)
            return [(x0, x1)] if x0 <= x1 else [(x0, 180.0), (-180.0, x1)]
        return any(max(p[0], q[0]) < min(p[1], q[1])
                   for p in pieces(x0, x1) for q in pieces(y0, y1))
    return _arc(lo0, lo1, lo0b, lo1b)


bad = []
for i in range(len(ars)):
    for j in range(i + 1, len(ars)):
        if _ov(ars[i], ars[j]):
            bad.append((ars[i]["id"], ars[j]["id"], "shared day + bbox arc"))
out["curation"] = {"ar_pair_conflicts": bad,
                   "ar_missing_qualified_at": [
                       e["id"] for e in ars
                       if not ((e.get("details") or {}).get("qualified_at")
                               or e.get("qualified_at"))],
                   "counts_by_type": {k: sum(1 for e in evs if e["event_type"] == k)
                                      for k in {e["event_type"] for e in evs}}}

# ---- D: score consistency --------------------------------------------------
d = pd.read_parquet(f"{R}/scores.parquet")
csi = d[(d.event_type == "atmospheric_river") & (d.metric == "CriticalSuccessIndex")]
sl = csi[csi.lead_time_hours <= 48]
scored_ids = set(csi.event_id)
out["scores"] = {
    "rows": len(d), "ar_events_with_csi": len(scored_ids),
    "short_lead_zero_csi_events": sorted(scored_ids - set(sl[sl.value > 0].event_id)),
    "ar_csi@48_mean": round(float(csi[csi.lead_time_hours == 48].value.mean()), 4),
    "ar_ids_not_selected_but_scored": sorted(
        scored_ids - {e["id"] for e in ars}),
    "selected_ar_not_scored": sorted(
        {e["id"] for e in ars} - scored_ids),
}
sc = json.load(open(f"{R}/scorecard.json"))
rows = sc["rows"] if isinstance(sc, dict) and "rows" in sc else sc
cell = [r for r in rows if r.get("event_type") == "atmospheric_river"
        and r.get("metric") == "CriticalSuccessIndex"
        and r.get("lead_time_hours") == 48 and r.get("model") == "ifs_hres"]
out["scores"]["scorecard_ar_csi@48_ifs"] = (
    [round(r["mean"], 4) for r in cell], [r.get("n_events") for r in cell])
print(json.dumps(out, indent=1, default=str))
