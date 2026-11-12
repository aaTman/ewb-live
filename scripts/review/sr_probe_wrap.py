import json, sys
sys.path.insert(0, ".")
import numpy as np
from ewb_live.viz.exceedance import frame_layers, LAT_N, LON_N
lat = -90 + 0.25*np.arange(LAT_N); lon = -180 + 0.25*np.arange(LON_N)
idx = json.load(open("data/results/raw_exceedance/index.json"))
ev = json.load(open("data/results/events_selected.json")); ev = ev["events"] if isinstance(ev, dict) else ev
ars = [e for e in ev if e["event_type"] == "atmospheric_river"]
e = [a for a in ars if a["id"] == "6b4dec9dc8db"][0]   # live, non-wrapped
b = e["bbox"]; la1, la2, lo1, lo2 = min(b[0], b[1]), max(b[0], b[1]), b[2], b[3]
w = lo2 - lo1   # width deg
t = frame_layers(open(f"data/results/raw_exceedance/{idx['frames'][8]}.bin", "rb").read(), (LAT_N, LON_N))[2]
W, E = 170.0, 170.0 - w   # same-width wrapped twin arc (E < W = AM crossing)
rlat = (lat >= la1) * (lat <= la2)
band = (lon >= min(W, E)) * (lon <= max(W, E))          # old min/max band (the COMPLEMENT)
arc = ((lon - W) % 360.0) <= ((E - W) % 360.0)          # new arc form (true arc)
old = int(t[np.ix_(rlat.nonzero()[0], band.nonzero()[0])].sum())
new = int(t[np.ix_(rlat.nonzero()[0], arc.nonzero()[0])].sum())
print(f"frame {idx['frames'][8]}: synthetic wrapped twin of {e['id']} bbox: lat[{la1},{la2}] arc 170->{E} (width {w}deg)")
print(f"OLD (min/max) scans the {360-w}deg complement band: {old} px")
print(f"NEW (arc) scans the true {w}deg arc: {new} px")
