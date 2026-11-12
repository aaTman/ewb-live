# Science-review sr08: rerun the PRODUCTION freeze scan (exact code path)
# for p15 (live clip) and p05 (owner clip), plus lat-range sensitivity.
# RUN ON THE ewb BOX via ops/remote.sh bg sr08.
import datetime as dt
import json
import sys

sys.path.insert(0, "/opt/ewb-live")
from ewb_live.events import temperature as T  # noqa: E402

results = []
for tag, q, lat_min in [("p15_default_lat", 0.15, -60.0),
                        ("p05_default_lat", 0.05, -60.0),
                        ("p05_full_lat", 0.05, -90.0)]:
    T.EVENT_CLIP_MODES["owb"]["freeze"]["q_upper"] = q
    evs = T.scan_temperature_events(dt.date(2026, 8, 8), dt.date(2026, 10, 30),
                                    "freeze", lat_min=lat_min)
    results.append({
        "case": tag, "q_upper": q, "lat_min": lat_min,
        "n_events": len(evs),
        "events": [{"id": e.id, "start": e.start.isoformat(),
                    "end": e.end.isoformat(), "bbox": list(e.bbox),
                    "peak": e.peak_value,
                    "peak_area_km2": (e.details or {}).get("peak_area_km2"),
                    "consec": (e.details or {}).get("max_consecutive_days")}
                   for e in evs]})
    print(tag, "->", len(evs), "events")
with open("/opt/ewb-live/data/results/review/sr08_prod_freeze_scans.json", "w") as f:
    json.dump(results, f, indent=1)
print("WROTE sr08")
