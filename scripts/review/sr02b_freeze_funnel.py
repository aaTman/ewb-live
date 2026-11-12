# Science-review sr02 v2: independent audit of the FREEZE detection funnel,
# with PRODUCTION semantics: exceedance mask on 6-hourly t2m vs hour-matched
# OWB threshold, then upstream _consecutive_filter_np + detect_events on the
# 6-hourly axis (temperature.py passes the 6-hourly mask straight through).
# RUN ON THE ewb BOX via ops/remote.sh.
# Output: data/results/review/sr02b_freeze_funnel.json
import json
import pathlib
import sys

import numpy as np
import pandas as pd
import regionmask
import xarray as xr
from scipy import ndimage

BASE = pathlib.Path("/opt/ewb-live")
sys.path.insert(0, "/opt/ewb-live")
from ewb_live.events import _upstream  # noqa: E402

te = _upstream.temperature_events()

mirror = xr.open_zarr(BASE / "data/cache/truth/era5.zarr")
t2m = mirror["2m_temperature"]
lats = t2m.latitude.values.astype(float)
lons = t2m.longitude.values.astype(float)
time = pd.DatetimeIndex(t2m.time.values)
nt = len(time)
hour_lookup = {0: 0, 6: 1, 12: 2, 18: 3}
doy = time.dayofyear.to_numpy()
hr = np.array([hour_lookup[h] for h in time.hour.to_numpy()])

clim = xr.open_zarr(BASE / "data/clim/t2m_owb_1990_2019.zarr")["2m_temperature"]
clim_p05 = clim.sel(quantile=0.05).values
clim_p15 = clim.sel(quantile=0.15).values

land = (regionmask.defined_regions.natural_earth_v5_0_0.land_110
        .mask(t2m.longitude, t2m.latitude).values == 0)
area_grid = te.compute_grid_cell_area(lats, lons)
print("mirror:", t2m.sizes, time[0], time[-1], "land cells:", int(land.sum()))

def build6h(clim_block: np.ndarray) -> np.ndarray:
    parts = []
    for i0 in range(0, nt, 40):
        sl = slice(i0, min(nt, i0 + 40))
        tt = t2m.isel(time=sl).values.astype(np.float32)
        parts.append(tt < clim_block[doy[sl] - 1, hr[sl]])
        del tt
    m = np.concatenate(parts, axis=0)
    return m & land[None]

def funnel(clim_block, tag):
    out = {"tag": tag}
    m6 = build6h(clim_block)
    out["steps"] = int(nt)
    out["pass_steps_land"] = int(m6.sum())
    out["pass_rate_land"] = float(m6.sum() / (nt * land.sum()))
    # per-band pass rates
    bands = {"greenland_60_83": (60, 83), "NH_30_60": (30, 60),
             "tropics": (-20, 20), "SH_-40_-20": (-40, -20), "SH_-60_-40": (-60, -40)}
    out["pass_rate_by_band"] = {}
    for name, (a, b) in bands.items():
        sel = (lats >= a) & (lats <= b)
        sub_land = land[:, sel]
        cells = int(sub_land.sum())
        out["pass_rate_by_band"][name] = (
            float(m6[:, sel][np.broadcast_to(sub_land[None], m6[:, sel].shape)].sum()
                  / (nt * cells)) if cells else None)
    filt = te._consecutive_filter_np(m6, te.MIN_CONSECUTIVE_DAYS, 1)
    out["after_consecutive_steps"] = int(filt.sum())
    # largest 4-connected blob per step (proper areas)
    struct4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    blobs = []
    for d in range(nt):
        lab, n = ndimage.label(filt[d], structure=struct4)
        if not n:
            continue
        areas = np.atleast_1d(ndimage.sum(
            np.broadcast_to(area_grid, filt[d].shape), lab, np.arange(1, n + 1)))
        areas = np.nan_to_num(areas, nan=0.0)
        k = int(np.argmax(areas))
        if areas[k] <= 0:
            continue
        pos = np.argwhere(lab == k + 1)
        blobs.append({"step": str(time[d]), "area_km2": float(areas[k]),
                      "lat": [float(lats[pos[:, 0].min()]), float(lats[pos[:, 0].max()])],
                      "lon": [float(lons[pos[:, 1].min()]), float(lons[pos[:, 1].max()])]})
    blobs.sort(key=lambda b: -b["area_km2"])
    out["top_step_blobs"] = blobs[:12]
    out["steps_with_blob_ge_200k"] = sum(1 for b in blobs if b["area_km2"] >= 2e5)
    # upstream chain on the 6-hourly axis EXACTLY as temperature.py does
    dates = time.values
    events = te.detect_events(filt, dates, lats, lons, "freeze",
                              area_grid=area_grid, min_seed_area_km2=te.MIN_AREA_KM2)
    out["detect_events_raw"] = len(events)
    # daily-mean t2m (degC) for include_temps (production passes daily means;
    # note: production passes DAILY t2m with a DAILY mask? no: same 6h axis)
    parts = []
    for i0 in range(0, nt, 40):
        sl = slice(i0, min(nt, i0 + 40))
        parts.append(t2m.isel(time=sl).values.astype(np.float32) - 273.15)
    t2m_np = np.concatenate(parts, axis=0)
    if events:
        import joblib
        events = joblib.Parallel(n_jobs=4, prefer="threads")(
            joblib.delayed(te.expand_event_bounds)(e, filt, dates, lats, lons, land)
            for e in events)
        events = te.include_temps_with_events(events, t2m_np, filt, dates, lats, lons)
        df = te.events_to_dataframe(events, min_area_km2=te.MIN_AREA_KM2)
        out["kept_after_min_area"] = int(len(df))
        out["records"] = [dict(start=str(getattr(r, "start_date", "?")),
                               end=str(getattr(r, "end_date", "?")),
                               bbox=[getattr(r, "latitude_min", None), getattr(r, "latitude_max", None),
                                     getattr(r, "longitude_min", None), getattr(r, "longitude_max", None)],
                               area_km2=getattr(r, "max_area", None))
                          for r in df.itertuples()]
    return out

result = {"generated": pd.Timestamp.utcnow().isoformat(),
          "window": [str(time[0]), str(time[-1])],
          "params": {"MIN_CONSECUTIVE_DAYS": te.MIN_CONSECUTIVE_DAYS,
                     "MIN_AREA_KM2": te.MIN_AREA_KM2,
                     "grace": 1, "axis": "6-hourly (production)"}}
result["p05"] = funnel(clim_p05, "owb_p05")
result["p15"] = funnel(clim_p15, "owb_p15")
with open(BASE / "data/results/review/sr02b_freeze_funnel.json", "w") as f:
    json.dump(result, f, indent=1)
print("WROTE sr02b_freeze_funnel.json")
