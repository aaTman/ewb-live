# Science-review sr03: quantification of the heat-wave geographic skew.
# RUN ON THE ewb BOX via ops/remote.sh.
#
# Single pass over the truth mirror computing, on the full 0.25 deg grid:
#   mask95_daily : AND-chain (4 anchor hours) t2m > OWB p95, over land
#   anom_daily   : 2026 daily-mean t2m minus the 1990-2019 daily-median
#                  climatology (EWB shipped q0.50, daily mean of its anchors)
# Then per-region tables (exceedance cell-days, anomaly K, max blob) and
# per-selected-event context anomalies. Output:
#   data/results/review/sr03_heat_skew.json
import json
import pathlib

import numpy as np
import pandas as pd
import xarray as xr
from scipy import ndimage

BASE = pathlib.Path("/opt/ewb-live")
OUT = BASE / "data/results/review"
OUT.mkdir(parents=True, exist_ok=True)

mirror = xr.open_zarr(BASE / "data/cache/truth/era5.zarr")
t2m = mirror["2m_temperature"]
lats = t2m.latitude.values.astype(float)
lons = t2m.longitude.values.astype(float)
time = pd.DatetimeIndex(t2m.time.values)
nd = len(time) // 4
hour_lookup = {0: 0, 6: 1, 12: 2, 18: 3}
doy = time.dayofyear.to_numpy()
hr = np.array([hour_lookup[h] for h in time.hour.to_numpy()])

clim95 = xr.open_zarr(BASE / "data/clim/t2m_owb_1990_2019.zarr")[
    "2m_temperature"].sel(quantile=0.95).values  # (366,4,lat,lon)

import regionmask
land = (regionmask.defined_regions.natural_earth_v5_0_0.land_110
        .mask(t2m.longitude, t2m.latitude).values == 0)
R = 6371.0
dlat = abs(lats[1] - lats[0]); dlon = abs(lons[1] - lons[0])
area_grid = np.outer((R * np.deg2rad(dlat)) * np.cos(np.deg2rad(lats))
                     * (R * np.deg2rad(dlon)), np.ones(len(lons)))

# daily-mean 1990-2019 median climatology (EWB shipped q0.50) on mirror grid
from extremeweatherbench import defaults as ewb_defaults
med = ewb_defaults.get_climatology(0.5)
doy_set = sorted(set(int(d) for d in doy))
med = med.sel(dayofyear=doy_set, hour=[0, 6, 12, 18]).load()
med = med.sortby("latitude").sortby("longitude").sortby("hour")
med_vals = med.values  # (doy_set, 4, lat, lon)
med_vals = med_vals.astype(np.float32)
doy_pos = {d: i for i, d in enumerate(doy_set)}
# daily mean over the day's 4 anchors, per window day
med_daily = np.stack([med_vals[doy_pos[int(d)]][hr[i]] for i, d in enumerate(doy)
                      ]).reshape(nd, 4, *med_vals.shape[2:]).mean(axis=1)
del med, med_vals

mask95 = np.zeros((nd, len(lats), len(lons)), bool)
anom = np.zeros((nd, len(lats), len(lons)), np.float32)
for i0 in range(0, nd, 10):
    sl = slice(i0 * 4, min(nd, i0 + 10) * 4)
    tt = t2m.isel(time=sl).values.astype(np.float32)
    bounds = clim95[doy[sl] - 1, hr[sl]]
    m6 = tt > bounds
    mask95[i0:i0 + tt.shape[0] // 4] = (
        m6.reshape(tt.shape[0] // 4, 4, *tt.shape[1:]).all(axis=1) & land[None])
    dmean = tt.reshape(tt.shape[0] // 4, 4, *tt.shape[1:]).mean(axis=1)
    anom[i0:i0 + tt.shape[0] // 4] = dmean - med_daily[i0:i0 + tt.shape[0] // 4]
    del tt, bounds, m6, dmean
mask95 &= land[None]
anom[~np.broadcast_to(land[None], anom.shape)] = np.nan
np.savez_compressed(OUT / "sr03_masks.npz", mask95=mask95, anom=anom)

REGIONS = {
    "Africa_N": (0, 37, -18, 52), "Africa_S": (-36, 0, 10, 52),
    "S_America": (-56, 13, -82, -34), "N_America": (13, 72, -130, -52),
    "Europe": (36, 70, -10, 40), "Asia": (0, 70, 40, 150),
    "Oceania": (-48, 0, 110, 180),
}
struct4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])

def lon_sel(lo0, lo1):
    if lo0 >= 0:
        return (lons >= lo0) & (lons <= lo1)
    return (lons >= lo0 + 360) & (lons <= lo1 + 360)

rows = {}
for name, (la0, la1, lo0, lo1) in REGIONS.items():
    m_lat = (lats >= la0) & (lats <= la1)
    m_lon = lon_sel(lo0, lo1)
    sub = mask95[:, m_lat][:, :, m_lon]
    sub_a = anom[:, m_lat][:, :, m_lon]
    mx = 0.0
    for d in range(nd):
        lab, n = ndimage.label(sub[d], structure=struct4)
        if n:
            areas = ndimage.sum(np.broadcast_to(area_grid[np.ix_(np.where(m_lat)[0], np.where(m_lon)[0])], sub[d].shape), lab, np.arange(1, n + 1))
            mx = max(mx, float(np.atleast_1d(areas).max()))
    rows[name] = {
        "land_cells": int(land[np.ix_(m_lat, m_lon)].sum()),
        "and_chain_cell_days": int(sub.sum()),
        "anom_vs_1990_2019_median_K_land_mean": float(np.nanmean(sub_a[land[np.ix_(m_lat, m_lon)]])),
        "anom_K_over_exceeding_cells": float(np.nanmean(sub_a[sub])),
        "max_daily_blob_km2": mx,
    }

result = {"generated": pd.Timestamp.utcnow().isoformat(),
          "window": [str(time[0]), str(time[-1])], "regions": rows}

# per selected heat event: anomalies over its active cells
sel = json.load(open(BASE / "data/results/events_selected.json"))
ev_rows = []
for e in sel:
    if e["event_type"] != "heat_wave":
        continue
    la0, la1, lo0, lo1 = e["bbox"]
    m_lat = (lats >= la0) & (lats <= la1)
    m_lon = lon_sel(lo0, lo1)
    i0 = int((np.datetime64(e["start"]) - time[0].to_datetime64())
             / np.timedelta64(1, "D"))
    n_days = max(1, (np.datetime64(e["end"]) - np.datetime64(e["start"]))
                 / np.timedelta64(1, "D"))
    sub = mask95[i0:i0 + n_days, m_lat][:, :, m_lon]
    sub_a = anom[i0:i0 + n_days, m_lat][:, :, m_lon]
    ev_rows.append({
        "id": e["id"], "start": e["start"][:10], "end": e["end"][:10],
        "active_cell_days": int(sub.sum()),
        "peak_daily_mean_C": e["peak_value"],
        "anom_vs_1990_2019_median_K_active_cells":
            float(np.nanmean(sub_a[sub])) if sub.any() else None,
        "anom_vs_1990_2019_median_K_bbox_all_land":
            float(np.nanmean(sub_a[land[np.ix_(m_lat, m_lon)]])) if sub.size else None})
result["per_event_context"] = ev_rows

with open(OUT / "sr03_heat_skew.json", "w") as f:
    json.dump(result, f, indent=1)
print("WROTE", OUT / "sr03_heat_skew.json")
print(json.dumps(result, indent=1))
