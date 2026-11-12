# Science-review sr02: independent audit of the FREEZE detection funnel.
# RUN ON THE ewb BOX via ops/remote.sh.
#
# Rebuilds the upstream exceedance chain from the TRUTH MIRROR (independent of
# the pipeline's ARCO read path) using OWB p05 (owner threshold) and OWB p15
# (what ewb-3 switched to), and reports every funnel stage with numbers:
#   land cell-steps -> 4-anchor AND daily passes -> consecutive filter (3d,
#   grace 1) -> largest blob -> upstream seed rule -> raw events.
# Also: per-step empirical pass rate vs theoretical, independence ratio,
# regional breakdown (SH winter bands, Greenland), and the top-10 largest
# post-filter blobs (the "near-miss" cold systems).
# Output: data/results/review/sr02_freeze_funnel.json (+npz of masks)
import datetime as dt
import importlib.util
import json
import pathlib

import numpy as np
import pandas as pd
import regionmask
import xarray as xr
from scipy import ndimage

BASE = pathlib.Path("/opt/ewb-live")
MIRROR = BASE / "data/cache/truth/era5.zarr"
CLIM = BASE / "data/clim/t2m_owb_1990_2019.zarr"
OUT = BASE / "data/results/review"
OUT.mkdir(parents=True, exist_ok=True)
UPSTREAM = BASE / "third_party/ExtremeWeatherBench/data_prep/temperature_events.py"

# load upstream detection module via the PRODUCTION loader (identical code
# path incl. the sys.modules registration numba needs)
import sys
sys.path.insert(0, "/opt/ewb-live")
from ewb_live.events import _upstream
te = _upstream.temperature_events()

mirror = xr.open_zarr(MIRROR)
t2m = mirror["2m_temperature"]
lats = t2m.latitude.values.astype(float)
lons = t2m.longitude.values.astype(float)
time = pd.DatetimeIndex(t2m.time.values)
print("mirror:", t2m.sizes, time[0], time[-1])

# time axis must be dense 6-hourly starting at 00Z for the reshape trick
d0 = time[0]
expected = pd.date_range(d0, periods=len(time), freq="6h")
assert (time == expected).all(), "mirror time axis not dense 6-hourly"
nd = len(time) // 4
nt = nd * 4

# climatology p05/p15 (label-based selection at exact anchors)
clim = xr.open_zarr(CLIM)["2m_temperature"]
clim_p05 = clim.sel(quantile=0.05).values  # (366, 4, lat, lon)
clim_p15 = clim.sel(quantile=0.15).values
hour_lookup = {0: 0, 6: 1, 12: 2, 18: 3}
doy = time.dayofyear.to_numpy()
hr = np.array([hour_lookup[h] for h in time.hour.to_numpy()])

# land mask exactly as production builds it (regionmask 0.13 quirk: must pass
# the coord DataArrays, not freshly-built ones)
land = (regionmask.defined_regions.natural_earth_v5_0_0.land_110
        .mask(t2m.longitude, t2m.latitude).values == 0)
area_grid = te.compute_grid_cell_area(lats, lons)
print("land cells:", land.sum(), "of", land.size)

def build_chain(clim_block: np.ndarray) -> np.ndarray:
    """daily AND-chain exceedance mask (t2m < p at all 4 anchors) over land."""
    daily_parts = []
    for i0 in range(0, nd, 10):
        sl = slice(i0 * 4, min(nd, i0 + 10) * 4)
        tt = t2m.isel(time=sl).values.astype(np.float32)
        dd = doy[sl]; hh = hr[sl]
        bounds = clim_block[dd - 1, hh]  # (40, lat, lon)
        m6 = tt < bounds
        daily = m6.reshape(-1, 4, *tt.shape[1:]).all(axis=1)
        daily_parts.append(daily)
        del tt, bounds, m6
    m = np.concatenate(daily_parts, axis=0)
    return m & land[None]

def funnel(clim_block: np.ndarray, tag: str, daily_mean_c: np.ndarray) -> dict:
    out: dict = {"tag": tag}
    daily = build_chain(clim_block)
    # exact per-step pass rate (t2m < p at each anchor, over land), sampled coarsely
    step_cnt = 0.0; n_steps = 0
    for i0 in range(0, nd, 20):
        sl = slice(i0 * 4, min(nt, (i0 + 20) * 4))
        tt = t2m.isel(time=sl).values.astype(np.float32)
        bounds = clim_block[doy[sl] - 1, hr[sl]]
        step_cnt += float(((tt < bounds) & land[None]).sum())
        n_steps += int((np.broadcast_to(land[None], tt.shape)).sum())
        del tt, bounds
    out["step_pass_rate_land"] = step_cnt / n_steps
    out["daily_pass_cell_days"] = int(daily.sum())
    # per-day pass counts
    out["daily_pass_cell_days_per_day"] = [int(x) for x in daily.sum(axis=(1, 2))]
    # independence expectation for the 4-AND at the empirical per-step rate
    p = out["step_pass_rate_land"]
    land_cells = int(land.sum())
    exp_cd = nd * land_cells * (p ** 4)
    out["expected_cell_days_if_independent"] = int(exp_cd)
    out["persistence_ratio"] = (daily.sum() / exp_cd) if exp_cd > 0 else None
    # consecutive filter (upstream, verbatim)
    filt = te._consecutive_filter_np(daily, te.MIN_CONSECUTIVE_DAYS, 1)
    out["after_consecutive_cells"] = int(filt.sum())
    # largest post-filter blob per day (4-connected)
    lbl_struct4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], int)
    blobs = []
    for d in range(nd):
        lab, n = ndimage.label(filt[d], structure=lbl_struct4)
        if not n:
            continue
        areas = np.atleast_1d(ndimage.sum(
            np.broadcast_to(area_grid, filt[d].shape), lab, np.arange(1, n + 1)))
        k = int(np.argmax(areas))
        pos = np.argwhere(lab == k + 1)
        blobs.append({"day": str(time[d * 4].date()), "area_km2": float(areas.max()),
                      "lat": [float(lats[pos[:, 0].min()]), float(lats[pos[:, 0].max()])],
                      "lon": [float(lons[pos[:, 1].min()]), float(lons[pos[:, 1].max()])]})
    blobs.sort(key=lambda b: -b["area_km2"])
    out["post_filter_blobs_top10"] = blobs[:10]
    # regional breakdown of daily passes
    bands = {"NH_greenland_60_83": (60, 83), "NH_30_60": (30, 60),
             "tropics_-20_20": (-20, 20), "SH_-40_-20": (-40, -20),
             "SH_-60_-40": (-60, -40)}
    reg = {}
    for name, (a, b) in bands.items():
        sel = (lats >= a) & (lats <= b)
        reg[name] = int(daily[:, sel].sum())
    out["daily_pass_by_band"] = reg
    # upstream full chain: detect_events + expand + temps + keep-filters
    dates = time[: nt:4].values
    events = te.detect_events(filt, dates, lats, lons, "freeze",
                              area_grid=area_grid, min_seed_area_km2=te.MIN_AREA_KM2)
    out["detect_events_raw"] = len(events)
    if events:
        import joblib
        events = joblib.Parallel(n_jobs=4, prefer="threads")(
            joblib.delayed(te.expand_event_bounds)(e, filt, dates, lats, lons, land)
            for e in events)
        events = te.include_temps_with_events(events, daily_mean_c, filt, dates,
                                              lats, lons)
        df = te.events_to_dataframe(events, min_area_km2=te.MIN_AREA_KM2)
        out["kept_after_filters"] = int(len(df))
        out["raw_event_records"] = [
            {"start": str(getattr(r, "start_date", "?")),
             "lat": [getattr(r, "latitude_min", None), getattr(r, "latitude_max", None)],
             "lon": [getattr(r, "longitude_min", None), getattr(r, "longitude_max", None)]}
            for r in df.itertuples()]
    return out

# daily mean t2m (degC) over the window, needed by upstream include_temps
parts = []
for i0 in range(0, nd, 10):
    sl = slice(i0 * 4, min(nd, i0 + 10) * 4)
    tt = t2m.isel(time=sl).values.astype(np.float32)
    parts.append(tt.reshape(tt.shape[0] // 4, 4, *tt.shape[1:]).mean(axis=1) - 273.15)
    del tt
daily_mean_c = np.concatenate(parts, axis=0)

result = {"generated": pd.Timestamp.utcnow().isoformat(),
          "window": [str(time[0]), str(time[-1])]}
result["p05"] = funnel(clim_p05, "owb_p05_owner_threshold", daily_mean_c)
result["p15"] = funnel(clim_p15, "owb_p15_current_clip", daily_mean_c)

# cross-check: mirror vs live ARCO at one land point, 2026-10-20 (rules out mirror artifact)
try:
    from extremeweatherbench import inputs as ewb_inputs
    ds = xr.open_zarr(ewb_inputs.ARCO_ERA5_FULL_URI, storage_options={"token": "anon"})
    il = int(np.argmin(np.abs(t2m.latitude.values - 76.0)))
    io = int(np.argmin(np.abs(t2m.longitude.values - 315.0)))  # -45W
    a = ds["2m_temperature"].isel(latitude=il, longitude=io).sel(
        time=slice("2026-10-20", "2026-10-20T18")).values
    b = t2m.sel(time=slice("2026-10-20", "2026-10-20T18")).isel(
        latitude=il, longitude=io).values
    result["mirror_vs_arco_spot_20261020_greenland"] = {
        "arco": [float(x) for x in a], "mirror": [float(x) for x in b],
        "max_abs_diff": float(np.max(np.abs(a - b)))}
except Exception as exc:  # noqa: BLE001
    result["mirror_vs_arco_spot_20261020_greenland"] = {"error": repr(exc)}

with open(OUT / "sr02_freeze_funnel.json", "w") as f:
    json.dump(result, f, indent=1)
print("WROTE", OUT / "sr02_freeze_funnel.json")
print(json.dumps(result, indent=1))
