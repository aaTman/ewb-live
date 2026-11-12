"""sr14: AR landfall funnel (owner HIGH report: landfalling ARs not events).

Every AR object in the published tracking ledger (90-day window) + every
pre-curation candidate is run through the ACTUAL rule stack, in code order,
recording the FIRST rule that rejects it. Read-only on production outputs.

Detection-side track ids and raw-ledger track ids are SEPARATE namespaces;
candidates are joined to ledger tracks by date+geometry overlap.

Order (code refs):
  L  ledger drop (tropical |lat|<15 / tc 500km)      ar_filter.ar_filter_tracks
  D1 tracked days >= AR_MIN_DAYS(2)                  ar.py:311
  D2 peak-day land px >= AR_MIN_LAND_PIXELS(10)      ar.py ocean_land_rule
  D3 landfall days (land>=10) >= 2                   ar.py (docstring floor)
  D4 ocean-frac start day >= 0.5                     ar.py ocean_land_rule
  D5 not all-land (ocean frac >=0.05 some day)       ar.py ocean_land_rule
  D6 peak IVT <= 1500 (mirror-measured)              ar.py tc_plausibility_rule
  D7 duration split: days > 9                        ar.py AR_MAX_DURATION_DAYS
  S  fragment of a candidate (tracking split/merge)  select.merge_same_type
  F  curation floor                                  select._passes_floor
  M/P merge/conflict/quota + publish gate            select/validate
Run on the box: ./ops/remote.sh run python scripts/review/sr14_ar_funnel.py
"""
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
from ewb_live.viz.exceedance import labels_from_bytes, LAT_N, LON_N  # noqa: E402
from ewb_live.viz.build import land_mask, _ar_inputs_window, _ivt_and_mask  # noqa: E402

R = Path("data/results")
X = R / "raw_exceedance"
idx = json.load(open(X / "index.json"))
frames = idx["frames"]
led = json.load(open(X / "ar_tracks.json"))
lab2id = {int(k): v for k, v in led["tracks"].items()}
lats = -90 + 0.25 * np.arange(LAT_N)
lons = -180 + (np.arange(LON_N) + 0.5) * 0.25
print("building land mask...", flush=True)
land = land_mask(lats, lons)
land1 = land.ravel()

S: dict = {}
K = np.uint64(2 ** 20)  # cell index < 720*1440 = 1,036,800 < 2**20
daykeys: dict = {}
daykeys_land: dict = {}
for f in frames:
    lab = labels_from_bytes(open(X / f"{f}.trk", "rb").read(), (LAT_N, LON_N)).ravel()
    nz = np.nonzero(lab)[0]
    if not len(nz):
        continue
    lb = lab[nz]
    tot = np.bincount(lb)
    ldl = np.bincount(lab[land1], minlength=len(tot))
    ys, xs = np.divmod(nz, LON_N)
    day = f[:8]
    # daily OR-union cell keys, matching ar.py's daily[j] = g.any(axis=0)
    daykeys.setdefault(day, []).append(lb.astype(np.uint64) * K + nz.astype(np.uint64))
    nl = nz[land1[nz]]
    if len(nl):
        daykeys_land.setdefault(day, []).append(
            lab[nl].astype(np.uint64) * K + nl.astype(np.uint64))
    for i in np.unique(lb):
        tid = lab2id.get(int(i))
        if tid is None:
            continue
        s = S.setdefault(tid, {"px": 0, "land": 0, "daypx": {}, "dayland": {},
                               "b": [90.0, -90.0, 0.0, 0.0]})
        m = lb == i
        s["px"] += int(tot[i])
        s["land"] += int(ldl[i])
        yy, xx = ys[m], xs[m]
        b = s["b"]
        b[0] = min(b[0], float(lats[yy.min()]))
        b[1] = max(b[1], float(lats[yy.max()]))
        # continuous lon vs first occupied cell (A4 pattern): a dateline-
        # crossing track must not record a fake 360 deg span
        if "lon0" not in s:
            s["lon0"] = float(lons[xx.min()])
            s["w"] = 0.0
            s["e"] = 0.0
        offs = (lons[xx] - s["lon0"] + 180.0) % 360.0 - 180.0
        s["w"] = min(s["w"], float(offs.min()))
        s["e"] = max(s["e"], float(offs.max()))
# daily union px per (track, day) = ar.py's daily_px / land floors
for day in sorted(set(daykeys) | set(daykeys_land)):
    u = np.unique(np.concatenate(daykeys[day]))
    cnt = np.bincount((u >> 20).astype(np.int64))
    lu = np.unique(np.concatenate(daykeys_land[day])) if day in daykeys_land else None
    lcnt = np.bincount((lu >> 20).astype(np.int64)) if lu is not None else np.zeros_like(cnt)
    for i in np.nonzero(cnt)[0]:
        tid = lab2id.get(int(i))
        if tid is None:
            continue
        s = S.get(tid)
        if s is None:
            continue
        s["daypx"][day] = int(cnt[i])
        s["dayland"][day] = int(lcnt[i]) if i < len(lcnt) else 0
print(f"ledger kept tracks w/ px: {len(S)}", flush=True)

_es = json.load(open(R / "events_selected.json"))
sel_ids = {e["id"] for e in (_es["events"] if isinstance(_es, dict) else _es)}
cand = {}
for e in json.load(open(R / "events.json")):
    if e["event_type"] == "atmospheric_river":
        days = set()
        d0 = dt.datetime.strptime(e["start"][:10], "%Y-%m-%d")
        d1 = dt.datetime.strptime(e["end"][:10], "%Y-%m-%d")
        while d0 <= d1:
            days.add(d0.strftime("%Y%m%d"))
            d0 += dt.timedelta(days=1)
        cand[e["id"]] = (e, days)


def overlaps(eb, tb):
    lat = min(eb[1], tb[1]) > max(eb[0], tb[0])
    # arc-aware lon overlap (events bbox [latmin,latmax,lonmin,lonmax])
    span_e = (eb[3] - eb[2]) % 360.0
    span_t = (tb[3] - tb[2]) % 360.0
    d = (tb[2] - eb[2]) % 360.0
    lon = d < span_e or (360.0 - d) % 360.0 < span_t
    return lat and lon


def match_track(track_days, tb):
    best, best_ov = None, 0
    for cid, (c, cdays) in cand.items():
        ov = len(track_days & cdays)
        if ov > best_ov and overlaps(c["bbox"], tb):
            best, best_ov = cid, ov
    return best

# mirror IVT for land-intersecting non-candidate survivors (bounded, D-gated)
IVT_CAP = 60
n_ivt = 0


def arc_bb(s):
    """EWB eastward-arc lon pair (lon_min > lon_max encodes dateline wrap)."""
    lonw = (s["lon0"] + s["w"] + 180.0) % 360.0 - 180.0
    lone = (s["lon0"] + s["e"] + 180.0) % 360.0 - 180.0
    return s["b"][0], s["b"][1], lonw, lone


def peak_ivt(tid, s):
    days = sorted(s["daypx"])
    lmin, lmax, lonw, lone = arc_bb(s)
    bb = (max(-89.9, lmin - 2), min(89.9, lmax + 2), lonw, lone)  # _crop_lon pads lon ±2
    try:
        t0 = dt.datetime.strptime(days[0], "%Y%m%d")
        t1 = dt.datetime.strptime(days[-1], "%Y%m%d") + dt.timedelta(days=1)
        ds = _ar_inputs_window(t0, t1, bb)
        best = None
        for k in range(ds.dims["valid_time"]):
            iv, _ = _ivt_and_mask(ds.isel(valid_time=k))
            m = float(np.nanmax(iv))
            best = m if best is None else max(best, m)
        return best
    except Exception as ex:
        print("ivt fail", tid, repr(ex)[:80], flush=True)
        return None


rows = []
for tid, s in sorted(S.items()):
    days = sorted(s["daypx"])
    landfall_days = sum(1 for v in s["dayland"].values() if v >= 10)
    peak_land = max(s["dayland"].values()) if s["dayland"] else 0
    tb = list(arc_bb(s))
    cid = match_track(set(days), tb) if s["land"] > 0 else None
    c, _cd = cand.get(cid, (None, None))
    rec = {"track": tid, "event": cid, "days": [days[0], days[-1]], "n_days": len(days),
           "px": s["px"], "land_px": s["land"], "peak_land": peak_land,
           "landfall_days_track": landfall_days,
           "bbox": [round(x, 2) for x in tb], "peak_ivt": None}
    if c is not None:
        rec["peak_ivt"] = c.get("peak_value")
        rec["ivt_source"] = "candidate"
    # ---- first rejecting rule (code order) ----
    if cid is not None and cid not in sel_ids:
        rej = None  # falls to candidate-side classification below
    elif cid is None:
        if s["land"] == 0:
            rej = "OCEANIC (no land intersection - out of scope)"
        elif len(days) < 2:
            rej = f"D1 min days (tracked {len(days)}d < 2)"
        elif peak_land < 10:
            rej = f"D2 peak land px {peak_land} < 10"
        elif landfall_days < 2:
            rej = f"D3 landfall days {landfall_days} < 2"
        else:
            of_start = 1 - s["dayland"].get(days[0], 0) / s["daypx"][days[0]]
            if of_start < 0.5:
                rej = f"D4 ocean-start {of_start:.2f} < 0.5"
            elif all(1 - s["dayland"].get(d, 0) / px < 0.05 for d, px in s["daypx"].items()):
                rej = "D5 all-land"
            elif n_ivt < IVT_CAP:
                n_ivt += 1
                rec["peak_ivt"] = peak_ivt(tid, s)
                rec["ivt_source"] = "mirror"
                if rec["peak_ivt"] is not None and rec["peak_ivt"] > 1500:
                    rej = f"D6 IVT {rec['peak_ivt']:.0f} > 1500"
                elif len(days) > 9:
                    rej = f"D7 duration {len(days)}d > 9 (split upstream)"
                else:
                    rej = "MYSTERY: passes D-rules, no candidate match"
            elif len(days) > 9:
                rej = f"D7 duration {len(days)}d > 9 (split upstream), IVT unmeasured"
            else:
                rej = "D-rules pass, IVT unmeasured (cap)"
    else:
        rej = "SELECTED"
    if cid is not None and cid not in sel_ids:
        det = c["details"] or {}
        dur = (dt.datetime.strptime(c["end"][:10], "%Y-%m-%d")
               - dt.datetime.strptime(c["start"][:10], "%Y-%m-%d")).days
        eb = c["bbox"]
        span_l = eb[1] - eb[0]
        span_o = (eb[3] - eb[2]) % 360
        pv = c.get("peak_value") or 0.0
        if det.get("landfall_days", 0) < 3:
            rej = f"F landfall_days {det.get('landfall_days')} < 3 (curation floor)"
        elif pv < 600:
            rej = f"F peak IVT {pv:.0f} < 600 (curation floor)"
        elif pv > 1500:
            rej = f"F peak IVT {pv:.0f} > 1500 (B8 ceiling)"
        elif abs(det.get("centroid_lat", 0) or 0) < 20:
            rej = f"F centroid |lat| {det.get('centroid_lat'):.1f} < 20"
        elif dur < 2 or dur > 14:
            rej = f"F duration {dur}d outside [2,14]"
        elif span_l > 60 or span_o > 60:
            rej = f"F bbox span lat {span_l:.0f} lon {span_o:.0f} > 60"
        else:
            rej = "M/P: passed floor -> merge/conflict/quota/publish gate"
    rec["reject"] = rej
    rows.append(rec)

# candidates with NO ledger track (window or geometry mismatch)
matched_c = {r["event"] for r in rows if r.get("event")}
orphan_c = [cid for cid in cand if cid not in matched_c]

drop_rows = [{"track": d["id"], "reason": d.get("reason"),
              "storm": d.get("tc_name") or d.get("storm"),
              "dist_km": d.get("distance_km"), "steps": len(d.get("steps", []))}
             for d in led["dropped"]]

curation = json.load(open(R / "curation.json")) if (R / "curation.json").exists() else []
json.dump({"funnel": rows, "dropped": drop_rows, "orphan_candidates": orphan_c,
           "curation_decisions": curation,
           "counts": {"ledger_kept": len(led["tracks"]), "ledger_dropped": len(led["dropped"]),
                      "kept_with_land": sum(1 for s in S.values() if s["land"] > 0),
                      "candidates": len(cand), "selected": len(sel_ids),
                      "ivt_unmeasured_cap": n_ivt >= IVT_CAP}},
          open("/tmp/sr14_funnel.json", "w"), indent=1, default=str)
print("wrote /tmp/sr14_funnel.json", flush=True)
tally = defaultdict(int)
for r in rows:
    tally[r["reject"].split(":")[0].split(" ")[0]] += 1
print(json.dumps(dict(tally), indent=1))
print("orphan candidates:", orphan_c)
