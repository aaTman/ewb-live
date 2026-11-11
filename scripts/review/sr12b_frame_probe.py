import sys, zlib, json
sys.path.insert(0, ".")
import numpy as np
from ewb_live.events.ar_filter import filter_ar_objects
from ewb_live.events.tc_screen import load_ibtracs_tracks

idx = json.load(open("data/results/raw_exceedance/index.json"))
ny, nx = idx["grid"]["ny"], idx["grid"]["nx"]; rs = nx // 8
lats = -90 + 0.25 * np.arange(ny)
raw = zlib.decompress(open("data/results/raw_exceedance/2026100300.bin", "rb").read())
ar = np.frombuffer(raw[2 * ny * rs:], np.uint8).reshape(ny, rs)[:, :nx].astype(bool)
tr = load_ibtracs_tracks({2026})
out, dropped = filter_ar_objects(
    ar[None], lats, np.array(["2026-10-03T00"], "datetime64[h]"), tr)
print("tc drops on published frame:",
      [(d["tc_name"], d["distance_km"]) for d in dropped if d["reason"] == "tc"])
print("published px:", int(ar.sum()), "-> current filter keeps:", int(out[0].sum()),
      "(removes", int(ar.sum()) - int(out[0].sum()), ")")
