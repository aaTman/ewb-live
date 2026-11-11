# Science-review sr10: attribution of externally-reported heat extremes that
# EWB Live did NOT detect. For each case region/window, walk the detection
# pipeline stage by stage and report the actual numbers at each gate:
#   anchor exceedance (t2m > OWB p95 at 00/06/12/18Z), 4-anchor AND,
#   >=3-day persistence filter, largest blob vs 200k km2 seed floor,
#   data availability (ARCO cutoff), curation candidates.
# Also relaxations (>=3-of-4 anchors; q0.85; 100k km2 floor) as what-ifs.
# RUN ON THE ewb BOX. Lightweight: regional subsets only.
import json
import pathlib
import sys

import numpy as np
import pandas as pd
import xarray as xr
from scipy import ndimage

sys.path.insert(0, "/opt/ewb-live")
import regionmask  # noqa: E402

BASE = pathlib.Path("/opt/ewb-live")
OUT = BASE / "data/results/review"
MIRROR = BASE / "data/cache/truth/era5.zarr"
CLIM = BASE / "data/clim/t2m_owb_1990_2019.zarr"

t = xr.open_zarr(MIRROR, consolidated=False)["2m_temperature"]
c = xr.open_zarr(CLIM, consolidated=False)["2m_temperature"]
lat = t.latitude.values.copy()
lon = t.longitude.values.copy()
lat[~np.isfinite(lat)] = np.nanmedian(lat)  # mirror coord NaN (sr06)
lon[~np.isfinite(lon)] = np.nanmedian(lon)
land = regionmask.defined_regions.natural_earth_v5_0_0.land_110.mask(
    t.longitude, t.latitude).values == 0
DEG = np.deg2rad(1)
R = 6371000.0
CELL = R * DEG * 0.25  # m per 0.25-deg cell in lon at equator
AREA = CELL * np.cos(np.deg2rad(lat))[:, None] * CELL * np.ones_like(lon)[None, :] / 1e6  # km2

CASES = {
    "w_europe": dict(box=(36, 55, -11, 16), d0="2026-09-01", d1="2026-09-30"),
    "europe_aug": dict(box=(38, 55, -5, 26), d0="2026-08-08", d1="2026-09-08"),
    "brazil_cerrado": dict(box=(-27, -12, -58, -40), d0="2026-09-20", d1="2026-10-02"),
    "iberia_sfrance": dict(box=(37, 44, -10, 4), d0="2026-09-01", d1="2026-09-30"),
    "nw_australia": dict(box=(-25, -13, 117, 131), d0="2026-10-20", d1="2026-10-30"),
    "china_east": dict(box=(22, 40, 104, 122), d0="2026-09-01", d1="2026-10-15"),
    "se_africa": dict(box=(-34, -22, 16, 40), d0="2026-10-01", d1="2026-10-28"),
}


def idx_of(lo0, lo1):
    if lo0 >= 0:
        m = (lon >= lo0) & (lon <= lo1)
    else:
        m = (lon >= lo0 + 360) & (lon <= lo1 + 360)
    return np.where(m)[0]


def analyze(case, box, d0, d1, q, climv):
    la0, la1, lo0, lo1 = box
    ilat = np.where((lat >= la0) & (lat <= la1))[0]
    ilon = idx_of(lo0, lo1)
    tt = t.sel(time=slice(d0, d1 + "T23")).isel(latitude=ilat, longitude=ilon).astype("float32").values
    times = pd.DatetimeIndex(t.sel(time=slice(d0, d1 + "T23")).time.values)
    if len(times) == 0:
        return {"case": case, "error": "no data in window (mirror/ARCO cutoff)"}
    keep = np.isin(times.hour.to_numpy(), [0, 6, 12, 18])
    times = times[keep]
    tt = tt[keep]
    nsteps = len(times) // 4
    times = times[: nsteps * 4]
    tt = tt[: nsteps * 4]
    doy = times.dayofyear.to_numpy().reshape(nsteps, 4)
    hour = times.hour.to_numpy().reshape(nsteps, 4)
    hidx = {0: 0, 6: 1, 12: 2, 18: 3}
    bounds = climv[doy - 1, np.vectorize(hidx.get)(hour)][:, :, ilat][:, :, :, ilon]
    passN = (tt.reshape(nsteps, 4, *tt.shape[1:]) > bounds)  # (d,h,lat,lon)
    sub_land = land[:, ilat][:, :, ilon][0] if land.ndim == 3 else land[np.ix_(ilat, ilon)]
    sub_area = AREA[np.ix_(ilat, ilon)]
    n_land = int(sub_land.sum())
    and4 = passN.all(axis=1) & sub_land
    and3 = passN.sum(axis=1) >= 3
    and3 = and3 & sub_land
    frac_and4 = float(and4.sum()) / max(1, n_land * nsteps)
    frac_and3 = float(and3.sum()) / max(1, n_land * nsteps)
    frac_any = float((passN & sub_land[None]).any(axis=1).sum()) / max(1, n_land * nsteps * 4)

    def peak_blob(mask):  # mask (d,lat,lon) land-only -> max blob km2 over days
        mx = 0.0
        peak_day = 0
        for d in range(mask.shape[0]):
            lab, n = ndimage.label(mask[d], structure=np.ones((3, 3)))
            if n == 0:
                continue
            areas = ndimage.sum(np.broadcast_to(sub_area, mask[d].shape), lab, np.arange(1, n + 1))
            k = int(np.argmax(areas))
            if areas[k] > mx:
                mx, peak_day = float(areas[k]), d
        return mx, peak_day

    # persistence: >=3-day streaks of and4 per cell, then blob
    streak = and4.copy()
    for d in range(2, nsteps):
        streak[d] &= and4[d - 1] & and4[d - 2]
    b4, pd4 = peak_blob(and4)
    b4p, pdp = peak_blob(streak)
    b3, pd3 = peak_blob(and3)
    # longest run of and4 days at region scale (any cell passes each day)
    day_hit = and4.any(axis=(1, 2))
    longest = cur = 0
    for v in day_hit:
        cur = cur + 1 if v else 0
        longest = max(longest, cur)
    return {"case": case, "q": q, "window": [str(times[0]), str(times[-1])],
            "days": nsteps, "land_cells": n_land,
            "frac_cell_anchor_pass": round(frac_any, 4),
            "frac_and4_cell_days": round(frac_and4, 5),
            "frac_and3of4_cell_days": round(frac_and3, 5),
            "max_and4_blob_km2": round(b4),
            "max_and4_persist3_blob_km2": round(b4p),
            "max_and3of4_blob_km2": round(b3),
            "max_and4_day_any_cell_run_days": int(longest),
            "peak_and4_day": str(times[pdp * 4]) if b4p else None,
            "_mask4": and4, "_times": times, "_ilat": ilat, "_ilon": ilon,
            "_sub_land": sub_land}


import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

def plot_case(case, r, m, times, ilat, ilon, sub_land):
    d_peak = int(np.argmax([m[d].sum() for d in range(m.shape[0])]))
    la, lo = lat[ilat], lon[ilon]
    LON, LAT = np.meshgrid(lo, la)
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.5))
    ax[0].contourf(LON, LAT, sub_land.astype(float), levels=[0.5, 1.5], colors="0.85")
    ax[0].pcolormesh(LON, LAT, m[d_peak], color="red", alpha=0.7, shading="auto")
    ax[0].set_title(f"{case}: 4-anchor AND p95 cells, {times[d_peak]:%m-%d} "
                    f"(persist-3 blob {r.get('max_and4_persist3_blob_km2', 0):,} km2)")
    dayhit = m.any(axis=(1, 2))
    ax[1].plot(pd.to_datetime(times[::4]), dayhit.astype(int), ".-")
    ax[1].axhline(1, color="r", lw=0.5)
    ax[1].set_title("AND-4 pass anywhere in region, per day")
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / f"sr-miss-{case}.png", dpi=110)
    plt.close(fig)


summary = {}
for q in (0.95, 0.85):
    climv = c.sel(quantile=q).values
    for case, cfg in CASES.items():
        r = analyze(case, cfg["box"], cfg["d0"], cfg["d1"], q, climv)
        m = r.pop("_mask4", None)
        if q == 0.95 and m is not None:
            plot_case(case, r, m, r.pop("_times"), r["_ilat"], r["_ilon"], r["_sub_land"])
        elif q == 0.95:
            r.pop("_times", None)
        summary[(case, q)] = {k: v for k, v in r.items() if not k.startswith("_")}
        if q == 0.95:
            cands = []
            for e in json.load(open(BASE / "data/results/events.json")):
                if e["event_type"] != "heat_wave":
                    continue
                b = e["bbox"]
                clat = (b[0] + b[1]) / 2
                clon = (b[2] + b[3]) / 2
                clon -= 360 if clon > 180 else 0
                la0, la1, lo0, lo1 = cfg["box"]
                if la0 <= clat <= la1 and lo0 <= clon <= lo1:
                    cands.append(e["id"])
            summary[(case, q)]["catalogue_candidates_in_box"] = cands
    del climv

out = list(summary.values())
# --- NW Australia post-cutoff window (Nov 1-6) via live ARCO probe -------
try:
    from extremeweatherbench import inputs as ewb_inputs
    t_arco = xr.open_zarr(ewb_inputs.ARCO_ERA5_FULL_URI,
                          storage_options={"token": "anon"},
                          consolidated=False)["2m_temperature"]
    if "valid_time" in t_arco.dims:
        t_arco = t_arco.rename({"valid_time": "time"})
    t_arco = (t_arco.sortby(t_arco.latitude, ascending=False)
              .sortby("longitude"))
    t_saved, lat_saved, lon_saved = t, lat, lon
    if (np.allclose(t_arco.latitude.values, lat_saved)
            and np.allclose(t_arco.longitude.values, lon_saved, equal_nan=True)):
        t = t_arco
    else:
        raise RuntimeError("ARCO grid order differs from mirror; positional subset unsafe")
    times_probe = pd.DatetimeIndex(t_arco.sel(time=slice("2026-11-01", "2026-11-06T23")).time.values)
    r_nov = analyze("nw_australia_nov", CASES["nw_australia"]["box"],
                    "2026-11-01", "2026-11-06", 0.95, c.sel(quantile=0.95).values)
    m = r_nov.pop("_mask4", None)
    if m is not None:
        plot_case("nw_australia_nov", r_nov, m, r_nov.pop("_times"),
                  r_nov["_ilat"], r_nov["_ilon"], r_nov["_sub_land"])
    r_nov["arco_availability"] = (f"live ARCO steps {times_probe[0]} .. {times_probe[-1]}"
                                  if len(times_probe) else "NO STEPS after cutoff")
    t, lat, lon = t_saved, lat_saved, lon_saved
    out.append({k: v for k, v in r_nov.items() if not k.startswith("_")})
except Exception as exc:  # noqa: BLE001
    out.append({"case": "nw_australia_nov", "error": repr(exc)})

with open(OUT / "sr10_miss_attribution.json", "w") as f:
    json.dump(out, f, indent=1, default=str)
print(json.dumps(out, indent=1, default=str))
print("WROTE", OUT / "sr10_miss_attribution.json")
