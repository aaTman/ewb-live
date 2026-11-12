# Science-review sr04: structural integrity audit of events_selected.json.
# RUN ON THE ewb BOX via ops/remote.sh run (pure JSON checks, fast).
#
#  1. zero time+space overlap pairs (antimeridian-aware)  [product rule]
#  2. per-type cap <= 15                                  [product rule]
#  3. event id determinism (recompute make_event_id)
#  4. title/date/peak/bbox validation (reuses ewb_live.events.validate)
#  5. curation floor compliance from Event.details (durations, spans, AR
#     ocean_frac_start >= 0.5, landfall days, centroid |lat| >= 20, IVT >= 600)
#  6. merge provenance: merged_from members must exist in the RAW catalogue
#     (events.json) as same type; merged window <= caps (AR 9, heat/freeze 16)
#  7. truth policy vs ARCO cutoff: which selected events END after the cutoff
#     (must be scored on ifs_hres_analysis), which start before the mirror
#     start / window start (14-day buffer rule)
#  8. window-edge and geometry artifacts: bbox touching poles, antimeridian
#     wrap flag consistency, events fully outside [window] (start..end)
# Output: data/results/review/sr04_eventset_structural.json
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, "/opt/ewb-live")
from ewb_live.events.types import Event, make_event_id  # noqa: E402
from ewb_live.events.validate import validate_event  # noqa: E402

RES = pathlib.Path("/opt/ewb-live/data/results")
sel = json.load(open(RES / "events_selected.json"))
cat = json.load(open(RES / "events.json"))
meta = json.load(open(RES / "meta.json"))
out: dict = {"n_selected": len(sel), "n_catalogue": len(cat)}

def to_event(d):
    return Event(id=d["id"], event_type=d["event_type"], title=d["title"],
                 start=dt.datetime.fromisoformat(d["start"]),
                 end=dt.datetime.fromisoformat(d["end"]),
                 bbox=tuple(d["bbox"]), peak_value=d["peak_value"],
                 details=d.get("details") or {})

events = [to_event(d) for d in sel]
byid = {e.id: e for e in events}
cat_ids = {e["id"] for e in cat}

# 1. overlap (arc-aware lon)
def arcs(lon0, lon1):
    return [(lon0, lon1)] if lon0 <= lon1 else [(lon0, 180.0), (-180.0, lon1)]
def bbox_inter(a, b):
    lat = min(a[1], b[1]) - max(a[0], b[0])
    if lat <= 0:
        return False
    lon = 0.0
    for l0, h0 in arcs(a[2], a[3]):
        for l1, h1 in arcs(b[2], b[3]):
            lon += max(0.0, min(h0, h1) - max(l0, l1))
    return lon > 0.0
overlaps = []
for i, a in enumerate(events):
    for b in events[i + 1:]:
        t = min(a.end, b.end) - max(a.start, b.start)
        if t > dt.timedelta(0) and bbox_inter(a.bbox, b.bbox):
            overlaps.append([a.id, b.id, str(t)])
out["overlap_pairs"] = overlaps
out["overlap_ok"] = not overlaps

# 2. cap
cap = {}
for e in events:
    cap[e.event_type] = cap.get(e.event_type, 0) + 1
out["per_type"] = cap
out["cap_ok"] = all(v <= 15 for v in cap.values())

# 3. id determinism
bad_ids = [e.id for e in events if make_event_id(e.event_type, e.start, e.bbox) != e.id]
out["bad_ids"] = bad_ids

# 4. validator
fails = {e.id: validate_event(e) for e in events if validate_event(e)}
out["validator_fails"] = fails

# 5. floors from details
floor_bad = []
for e in events:
    d = e.details or {}
    dur = (e.end - e.start).days
    lat_span = e.bbox[1] - e.bbox[0]
    lon_span = (e.bbox[3] - e.bbox[2]) % 360.0
    if e.event_type == "atmospheric_river":
        if not (d.get("landfall_days", 0) >= 3 and (e.peak_value or 0) >= 600
                and abs(d.get("centroid_lat", 999)) >= 20 and 2 <= dur <= 14
                and lat_span <= 60 and lon_span <= 60):
            floor_bad.append([e.id, "AR floor", dur, d.get("landfall_days"),
                              e.peak_value, d.get("centroid_lat")])
        if d.get("ocean_frac_start") is None or d["ocean_frac_start"] < 0.5:
            floor_bad.append([e.id, "ocean_frac_start", d.get("ocean_frac_start")])
        if d.get("landfall_time") is None:
            floor_bad.append([e.id, "no landfall_time"])
    else:
        if not (d.get("max_consecutive_days", 0) >= 3 and 3 <= dur <= 21
                and lat_span <= 60 and lon_span <= 60):
            floor_bad.append([e.id, "temp floor", dur, d.get("max_consecutive_days")])
    # quantile provenance
    if e.event_type == "freeze" and d.get("quantile") != 0.05:
        floor_bad.append([e.id, "freeze quantile != 0.05 (unapproved clip)",
                          d.get("quantile")])
    if e.event_type == "heat_wave" and d.get("quantile") != 0.95:
        floor_bad.append([e.id, "heat quantile != 0.95", d.get("quantile")])
out["floor_violations"] = floor_bad

# 6. merge provenance
merge_issues = []
CAPS = {"atmospheric_river": 9, "heat_wave": 16, "freeze": 16}
for e in events:
    d = e.details or {}
    members = d.get("merged_from")
    if not members or d.get("merged_count", 1) <= 1:
        continue
    dur = (e.end - e.start).days
    if dur > CAPS[e.event_type]:
        merge_issues.append([e.id, "merged duration > cap", dur, CAPS[e.event_type]])
    for m in members:
        if m not in cat_ids:
            merge_issues.append([e.id, "member not in raw catalogue", m])
    out.setdefault("merges", []).append({
        "id": e.id, "type": e.event_type, "count": d["merged_count"],
        "members": members, "window": [e.start.isoformat(), e.end.isoformat()],
        "bbox": list(e.bbox), "core_fallback": d.get("merge_core"),
        "duration_days": dur})
out["merge_issues"] = merge_issues

# 7. truth policy vs cutoff
cutoff = meta["truth_source"]["arco_cutoff"]
cut = dt.datetime.fromisoformat(cutoff)
w_start = dt.date.fromisoformat(meta["window"]["start"])
w_end = dt.date.fromisoformat(meta["window"]["end"])
past_cutoff = [e.id for e in events
               if (e.end - dt.timedelta(hours=1)) > cut]
before_window = [e.id for e in events if e.start.date() < w_start]
after_window = [e.id for e in events if e.start.date() > w_end]
out["truth"] = {"cutoff": cutoff, "events_past_cutoff": past_cutoff,
                "events_starting_before_window_start(buffer ok)": before_window,
                "events_starting_after_window_end": after_window}

# 8. geometry artifacts
geom = []
for e in events:
    lat0, lat1, lon0, lon1 = e.bbox
    if lat1 >= 89.75 or lat0 <= -89.75:
        geom.append([e.id, "touches pole", e.bbox])
    wrapped = lon0 > lon1
    flag = (e.details or {}).get("antimeridian_wrap")
    if flag is not None and bool(flag) != wrapped:
        geom.append([e.id, "wrap flag mismatch", e.bbox, flag])
    if wrapped:
        geom.append([e.id, "wrapped arc (verify intended)", e.bbox])
out["geometry_flags"] = geom

with open(RES / "review" / "sr04_eventset_structural.json", "w") as f:
    json.dump(out, f, indent=1)
print(json.dumps(out, indent=1))
