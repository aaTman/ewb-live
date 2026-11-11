# Science-review sr05: targeted AND-chain blob check for externally-reported
# heat extremes that are ABSENT from events_selected.json.
# RUN ON THE ewb BOX via ops/remote.sh run.
import json
import pathlib

import numpy as np
from scipy import ndimage

BASE = pathlib.Path("/opt/ewb-live")
d = np.load(BASE / "data/results/review/sr03_masks.npz")
mask, anom = d["mask95"], d["anom"]
import xarray as xr
mirror = xr.open_zarr(BASE / "data/cache/truth/era5.zarr")
lats = mirror["2m_temperature"].latitude.values.astype(float)
lons = mirror["2m_temperature"].longitude.values.astype(float)
import pandas as pd
time = pd.DatetimeIndex(mirror["2m_temperature"].time.values)
nd = len(time) // 4
R = 6371.0
lat0 = abs(lats[1] - lats[0]); lon0 = abs(lons[1] - lons[0])
area_grid = np.outer(R * np.deg2rad(lat0) * np.cos(np.deg2rad(lats))
                     * R * np.deg2rad(lon0), np.ones(len(lons)))
struct4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])

def check(name, d0, d1, la0, la1, lo0, lo1):
    t0 = np.datetime64(d0); t1 = np.datetime64(d1)
    i0 = int((t0 - time[0].to_datetime64()) / np.timedelta64(1, "D"))
    n = int((t1 - t0) / np.timedelta64(1, "D")) + 1
    m_lat = (lats >= la0) & (lats <= la1)
    if lo0 >= 0:
        m_lon = (lons >= lo0) & (lons <= lo1)
    else:
        m_lon = (lons >= lo0 + 360) & (lons <= lo1 + 360)
    sub = mask[i0:i0 + n][:, m_lat][:, :, m_lon]
    sub_a = anom[i0:i0 + n][:, m_lat][:, :, m_lon]
    ag = area_grid[np.ix_(np.where(m_lat)[0], np.where(m_lon)[0])]
    mx = 0.0; mx_day = None; mx_bbox = None
    for k in range(sub.shape[0]):
        lab, nn = ndimage.label(sub[k], structure=struct4)
        if nn:
            areas = np.atleast_1d(ndimage.sum(
                np.broadcast_to(ag, sub[k].shape), lab, np.arange(1, nn + 1)))
            j = int(np.argmax(areas))
            if areas[j] > mx:
                pos = np.argwhere(lab == j + 1)
                mx = float(areas[j])
                mx_day = str(time[(i0 + k) * 4].date())
                mx_bbox = [float(lats[m_lat][pos[:, 0].min()]),
                           float(lats[m_lat][pos[:, 0].max()]),
                           float(lons[m_lon][pos[:, 1].min()]),
                           float(lons[m_lon][pos[:, 1].max()])]
    return {"case": name, "dates": [d0, d1],
            "cell_days_p95_exceed": int(sub.sum()),
            "max_blob_km2": round(mx, 0), "max_blob_day": mx_day,
            "max_blob_bbox": mx_bbox, "MIN_AREA_KM2": 200_000,
            "meets_area_criterion": mx >= 200_000,
            "anom_K_mean_over_exceeding": float(np.nanmean(sub_a[sub])) if sub.any() else None}

cases = [
    ("S China autumn-tiger (reported Sep25-Oct3, rec 39.5C)", "2026-09-24", "2026-10-04", 22, 34, 99, 121),
    ("S California Oct heat (reported Oct1-8, SD 105F)", "2026-09-30", "2026-10-10", 32, 36, -121, -114),
    ("S Plains record Sep (KS/OK/TX)", "2026-09-08", "2026-09-30", 33, 38, -102, -95),
    ("Midwest Oct13/28 records", "2026-10-10", "2026-11-01", 38, 45, -98, -84),
    ("Iberia/W Europe Sep (C3S rec-warm)", "2026-08-25", "2026-09-20", 36, 46, -10, 5),
]
out = [check(*c) for c in cases]
print(json.dumps(out, indent=1))
with open(BASE / "data/results/review/sr05_missed_heat_blobs.json", "w") as f:
    json.dump(out, f, indent=1)
