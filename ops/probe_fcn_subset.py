"""Derive AR mask from the EXACT cached case subset the crashed bench used."""
import json
import sys

import numpy as np
import xarray as xr

sys.path.insert(0, "/opt/ewb-live")

evs = json.load(open("/opt/ewb-live/data/results/events_selected.json"))
items = evs["events"] if isinstance(evs, dict) else evs
ev = next(e for e in items if e["id"] == "a3bb50ccde3d")
lat0, lat1, lon0, lon1 = ev["bbox"]
from ewb_live.scoring.evaluate import BBOX_PAD_DEG  # noqa: E402

pad = BBOX_PAD_DEG
lat0, lat1 = max(-89.0, lat0 - pad), min(89.0, lat1 + pad)
lon0 = ((lon0 - pad + 180.0) % 360.0) - 180.0
lon1 = ((lon1 - pad + 180.0) % 360.0) - 180.0
print("event bbox padded:", lat0, lat1, lon0, lon1)

from extremeweatherbench.derived import AtmosphericRiverVariables  # noqa: E402

root = "/opt/ewb-live/data/cache/subsets/fourcastnet_v2_gfs"
import glob  # noqa: E402

cands = glob.glob(root + "/*/*")
hit = None
for c in sorted(cands):
    try:
        ds = xr.open_zarr(c, consolidated=False)
        la, lo = ds.latitude.values, ds.longitude.values
        if (abs(la.min() - lat0) < 1.0 and abs(la.max() - lat1) < 1.0):
            hit = c
            break
    except Exception as exc:  # noqa: BLE001
        print("skip", c, type(exc).__name__, str(exc)[:80])
print("case subset:", hit)
if hit:
    ds = xr.open_zarr(hit, consolidated=False)
    print("vars:", sorted(ds.data_vars), "dims:", dict(ds.sizes))
    for v in sorted(ds.data_vars):
        print(" ", v, ds[v].dims, "nan%", round(float(ds[v].isnull().mean()) * 100, 2))
    ar = AtmosphericRiverVariables(
        output_variables=["atmospheric_river_mask", "integrated_vapor_transport"])
    out = ar.derive_variable(ds)
    m = out["atmospheric_river_mask"]
    iv = out["integrated_vapor_transport"]
    print("MASK dims:", m.dims, "nan%:", round(float(m.isnull().mean()) * 100, 2))
    print("IVT dims:", iv.dims)
