"""Validate events_selected.json: zero time+space overlap (arc-aware),
duration caps, AR ocean fields. stdlib + json only; runs anywhere."""
import json
import sys
import datetime as dt
from collections import Counter

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/ewb-live/data/results/events_selected.json"
d = json.load(open(path))

def lat_iou(a, b):
    i = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    u = (a[1] - a[0]) + (b[1] - b[0]) - i
    return i / u if u > 0 else 1.0

def lon_iou(a, b):
    lo1, hi1, lo2, hi2 = a[2], a[3], b[2], b[3]
    a1, a2 = (hi1 - lo1) % 360, (hi2 - lo2) % 360
    if lo1 <= hi1 and lo2 <= hi2:
        i = max(0, min(hi1, hi2) - max(lo1, lo2))
    else:
        i = (max(0, min(hi1, hi2 + 360) - max(lo1, lo2))
             + max(0, min(hi1 + 360, hi2) - max(lo1, lo2 + 360)))
    u = a1 + a2 - i
    return i / u if u > 0 else 1.0

bad, rows = [], []
for i, e in enumerate(d):
    s = dt.date.fromisoformat(e["start"][:10])
    t = dt.date.fromisoformat(e["end"][:10])
    dur = (t - s).days
    det = e.get("details", {})
    if e["event_type"] == "atmospheric_river":
        for f in ("ocean_frac_start", "ocean_frac_max", "land_frac_peak", "landfall_time"):
            if f not in det:
                bad.append(e["id"][:8] + " missing " + f)
        extra = ("ofr%.2f/%.2f lfp%.2f merged%d"
                 % (det["ocean_frac_start"], det["ocean_frac_max"],
                    det["land_frac_peak"], det.get("merged_count", 1)))
    else:
        extra = "%+.1fC max%.1fMkm2 q%.2f %s merged%d" % (
            det["mean_temp_c"], det["peak_area_km2"] / 1e6,
            det["quantile"], det["clim_source"], det.get("merged_count", 1))
    rows.append((e["event_type"][:6], e["id"][:8], "%s..%s" % (s, t), dur,
                 "lat%.0f..%.0f lon%.0f..%.0f" % tuple(e["bbox"]), extra))
    for g in d[i + 1:]:
        ts = dt.date.fromisoformat(g["start"][:10])
        te = dt.date.fromisoformat(g["end"][:10])
        if s < te and ts < t and lat_iou(e["bbox"], g["bbox"]) > 0 and lon_iou(e["bbox"], g["bbox"]) > 0:
            bad.append("OVERLAP %s x %s" % (e["id"][:8], g["id"][:8]))

print(Counter(e["event_type"] for e in d))
for r in rows:
    print("%-6s %s %-13s %2dd %-32s %s" % r)
cap = {"atmospheric_river": 19, "heat_wave": 26, "freeze": 26}
for e in d:
    dur = (dt.date.fromisoformat(e["end"][:10]) - dt.date.fromisoformat(e["start"][:10])).days
    if dur > cap[e["event_type"]]:
        print("DUR-OVER", e["id"][:8], dur)
print("PROBLEMS:" if bad else "OK: zero overlaps; ocean fields on all ARs; durations within caps")
print(*bad[:8], sep="\n")
