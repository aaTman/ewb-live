# Science-review sr11: targeted re-review of the G-34 truth-longitude fix.
# (1) western vs eastern heat RMSE distributions, Greenland freeze RMSE,
#     formerly all-zero-CSI ARs; (2) 95aeafb7fd7f max-RMSE anatomy;
#     (3) cyclone screen + refill curation integrity. READ-ONLY on the box.
# RUN ON THE ewb BOX after full_run_complete=true.
import json
import math
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path("/opt/ewb-live/data/results")
sel = json.load(open(ROOT / "events_selected.json"))
evs = sel["events"] if isinstance(sel, dict) and "events" in sel else sel
by_id = {e["id"]: e for e in evs}
d = pd.read_parquet(ROOT / "scores.parquet")
meta = json.load(open(ROOT / "meta.json"))
out = {"full_run_complete": meta.get("full_run_complete"),
       "last_updated": meta.get("last_updated"),
       "rows": len(d), "events_scored": int(d.event_id.nunique()),
       "n_selected": len(evs)}

# ---- helpers ----
def _bb(e):
    b = e["bbox"]
    if isinstance(b, dict):
        return b["lat_min"], b["lat_max"], b["lon_min"], b["lon_max"]
    return b

def lon_center(e):  # wrap-aware bbox center longitude
    _, _, lon0, lon1 = _bb(e)
    arc = (lon1 - lon0) % 360.0
    c = (lon0 + arc / 2.0 + 180.0) % 360.0 - 180.0
    return c, arc

def is_west(e):
    c, _ = lon_center(e)
    return c < 0

# ---- 1. heat RMSE west vs east (ifs_hres baseline, leads 24..120) ----
heat_ids = [e["id"] for e in evs if e["event_type"] == "heat_wave"]
h = d[(d.event_id.isin(heat_ids)) & (d.metric == "RootMeanSquaredError") &
      (d.model == "ifs_hres") & (d.lead_time_hours.between(24, 120))]
per = h.groupby("event_id").value.mean()
west = [i for i in heat_ids if is_west(by_id[i])]
east = [i for i in heat_ids if i not in west]
def _r(v, nd=3):
    """Round for JSON: non-finite (empty-lane NaN) -> null, never bare NaN."""
    v = float(v)
    return round(v, nd) if math.isfinite(v) else None


out["heat_rmse_ifs_24_120"] = {
    "west_n": len(west), "west_mean": _r(per.reindex(west).mean()),
    "west_max": _r(per.reindex(west).max()),
    "east_n": len(east), "east_mean": _r(per.reindex(east).mean()),
    "east_max": _r(per.reindex(east).max()),
    "per_event": {i: (_r(per[i]) if i in per.index else None)
                  for i in heat_ids},
    "west_ids": west, "pre_fix_ref": {"west_mean": 3.63, "east_mean": 2.14,
                                      "56caf7420b9a": 5.62, "95aeafb7fd7f": 5.00}}

# Greenland freeze (was 13.2 K)
g = d[(d.event_id == "88bb73d7ae25") & (d.metric == "RootMeanSquaredError")]
out["greenland_freeze_rmse"] = {
    "by_model": {k: _r(v, 2) for k, v in g.groupby("model").value.mean().items()},
    "max": _r(g.value.max(), 2) if len(g) else None}

# ---- 1b. formerly all-zero CSI ARs + dateline set ----
AR_TGT = ["a3bb50ccde3d", "1ff0485667cc", "0e530ab9eebf", "e1bd4ec451c5",
          "255f68102c43", "8fe8ae212d73"]
ar_ids = [e["id"] for e in evs if e["event_type"] == "atmospheric_river"]
csi = d[(d.metric == "CriticalSuccessIndex")]
es = d[(d.metric == "EarlySignal")]
out["ar_csi"] = {}
for i in AR_TGT:
    s = csi[csi.event_id == i]
    e = es[es.event_id == i]
    if not len(s):
        out["ar_csi"][i] = "NO CSI ROWS"
        continue
    m = s.groupby("lead_time_hours").value.mean()
    out["ar_csi"][i] = {
        "n": len(s), "max": _r(s.value.max(), 4),
        "mean": _r(s.value.mean(), 4),
        "csi@24h": _r(m.get(24.0, np.nan), 4),
        "csi@120h": _r(m.get(120.0, np.nan), 4),
        "ess_mean": _r(e.value.mean(), 4) if len(e) else None,
        "ess_max": _r(e.value.max(), 4) if len(e) else None,
        "spatial_displacement_rows": int(len(d[(d.event_id == i) &
                                               (d.metric == "spatial_displacement")]))}
allzero = [i for i in ar_ids
           if len(csi[csi.event_id == i]) and
           float(csi[csi.event_id == i].value.max()) == 0.0]
out["ar_csi"]["all_zero_csi_events_current"] = allzero
# regression (G-34/H4 class): any scored AR whose CSI is zero for every
# model at leads <=48h is the edge-column-alignment fingerprint, not physics
sl = csi[csi.lead_time_hours <= 48]
ok_short = set(sl[sl.value > 0].event_id)
scored_ar = {i for i in ar_ids if len(csi[csi.event_id == i])}
out["ar_csi"]["short_lead_zero_csi_events"] = sorted(scored_ar - ok_short)

# ---- 2. 95aeafb7fd7f max RMSE anatomy ----
e95 = d[(d.event_id == "95aeafb7fd7f") & (d.metric == "RootMeanSquaredError")]
if len(e95):
    top = e95.loc[e95.value.idxmax()]
    curve95 = e95[e95.model == top.model].groupby("lead_time_hours").value.mean()
    peers = d[(d.event_id.isin([i for i in heat_ids if i != "95aeafb7fd7f"])) &
              (d.metric == "RootMeanSquaredError") & (d.model == top.model)]
    pcurve = peers.groupby("lead_time_hours").value.agg(["mean", "max"])
    L = int(top.lead_time_hours)
    out["95aeafb7fd7f"] = {
        "max_value": _r(top.value), "lead_h": L,
        "model": top.model, "init_time": str(top.init_time),
        "event_lead_curve": {int(k): _r(v, 2) for k, v in
                             curve95.items() if k % 48 == 0},
        "peers_same_lead_mean": _r(pcurve["mean"].reindex([float(L)]).iloc[0]),
        "peers_same_lead_max": _r(pcurve["max"].reindex([float(L)]).iloc[0]),
        "is_new_global_max": _r(e95.value.max())}

# ---- 3. TC screen + refill curation integrity ----
cur = json.load(open(ROOT / "curation_exclusions.json")) if (ROOT / "curation_exclusions.json").exists() else None
out["curation"] = {
    "counts_by_type": {t: sum(1 for e in evs if e["event_type"] == t)
                       for t in ("heat_wave", "freeze", "atmospheric_river")},
    "0c7a7f10f667_selected": "0c7a7f10f667" in by_id,
    "0c7a7f10f667_score_rows": int((d.event_id == "0c7a7f10f667").sum()),
    "bebcc8d36395_selected": "bebcc8d36395" in by_id,
    "bebcc8d36395_scored_models": sorted(d[d.event_id == "bebcc8d36395"].model.unique().tolist()) if "bebcc8d36395" in set(d.event_id) else [],
    "exclusions_file": cur}
ref = by_id.get("bebcc8d36395")
if ref:
    det = ref.get("details", {})
    out["curation"]["refill_details"] = {
        "bbox": ref["bbox"], "start": ref.get("start"), "end": ref.get("end"),
        "tc_screen": det.get("tc_screen"),
        "ocean_start_landfall_keys": {k: det[k] for k in det
                                      if any(w in k.lower() for w in
                                             ("land", "ocean", "origin", "track"))}}

# overlap check (arc-aware lon; reuse convention lon_min>lon_max = wrap)
def arcs(e):
    _, _, lon0, lon1 = _bb(e)
    if lon0 <= lon1:
        return [(lon0, lon1)]
    return [(lon0, 180.0), (-180.0, lon1)]

def overlap(a, b):
    la0, la1, _, _ = _bb(a)
    lb0, lb1, _, _ = _bb(b)
    if la1 <= lb0 or lb1 <= la0:
        return False
    if a["end"] <= b["start"] or b["end"] <= a["start"]:
        return False
    return any(max(x0, y0) < min(x1, y1) for x0, x1 in arcs(a) for y0, y1 in arcs(b))

pairs = [(evs[i]["id"], evs[j]["id"]) for i in range(len(evs))
         for j in range(i + 1, len(evs)) if overlap(evs[i], evs[j])]
out["curation"]["overlapping_pairs"] = pairs
out["curation"]["events_missing_qualified_at"] = [e["id"] for e in evs if not e.get("qualified_at")]

print(json.dumps(out, indent=1, default=str))
dest = ROOT / "review"
dest.mkdir(exist_ok=True)
(dest / "sr11_g34_verify.json").write_text(json.dumps(out, indent=1, default=str))
