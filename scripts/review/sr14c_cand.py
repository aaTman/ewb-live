"""sr14c: candidate-level AR funnel (65 -> 12). Run on box."""
import datetime as dt
import json
from collections import Counter
from pathlib import Path

import numpy as np

R = Path("data/results")
es = json.load(open(R / "events_selected.json"))
sel = {e["id"] for e in (es["events"] if isinstance(es, dict) else es)}
cand = [e for e in json.load(open(R / "events.json")) if e["event_type"] == "atmospheric_river"]
cur = json.load(open(R / "curation.json")) if (R / "curation.json").exists() else []
dec = {}
if isinstance(cur, dict):
    for k, v in cur.items():
        dec[k] = v
elif isinstance(cur, list):
    for d in cur:
        dec[d.get("id") or d.get("event_id")] = d
print("curation sample:", json.dumps(cur[0] if isinstance(cur, list)
      else list(cur.items())[0])[:400] if cur else "0 curated")
rows = []
for c in sorted(cand, key=lambda e: -(e.get("peak_value") or 0)):
    d = c["details"] or {}
    dur = (dt.datetime.strptime(c["end"][:10], "%Y-%m-%d")
           - dt.datetime.strptime(c["start"][:10], "%Y-%m-%d")).days
    eb = c["bbox"]
    span_l = eb[1] - eb[0]
    span_o = (eb[3] - eb[2]) % 360
    pv = c.get("peak_value") or 0.0
    if c["id"] in sel:
        rej = "SELECTED"
    elif d.get("landfall_days", 0) < 3:
        rej = f"F landfall_days={d.get('landfall_days')}<3"
    elif pv < 600:
        rej = f"F peakIVT={pv:.0f}<600"
    elif pv > 1500:
        rej = f"F peakIVT={pv:.0f}>1500"
    elif abs(d.get("centroid_lat") or 0) < 20:
        rej = f"F |clat|={d.get('centroid_lat'):.1f}<20"
    elif dur < 2 or dur > 14:
        rej = f"F dur={dur}d"
    elif span_l > 60 or span_o > 60:
        rej = f"F span={span_l:.0f}/{span_o:.0f}"
    else:
        dd = dec.get(c["id"])
        rej = "M/P?" if not dd else "cur:" + json.dumps(dd)[:140]
    rows.append((c["id"], c["start"][:10], c["end"][:10], round(pv), d.get("landfall_days"),
                 d.get("peak_land_pixels"), (d.get("track_id") or "")[-6:], rej))
hdr = f"{'id':13}{'start':11}{'end':11}{'ivt':>5}{'lf':>3}{'land':>6}{'trk':>7}  reject"
print(hdr)
for r in rows:
    print(f"{r[0][:12]:13}{r[1]:11}{r[2]:11}{r[3]:>5}{str(r[4]):>3}{str(r[5]):>6}{r[6]:>7}  {r[7]}")
print(Counter(r[7].split(" ")[0] for r in rows))
