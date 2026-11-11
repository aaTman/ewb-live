# Science-review sr01: independent validation of the OWB pooled-quantile
# climatology product (data/clim/t2m_owb_1990_2019.zarr) + spot check of EWB's
# shipped climatology. RUN ON THE ewb BOX via ops/remote.sh run.
#
# 1. Structure: dims/coords/units/dtype/lat orientation, .done marker.
# 2. Reproduction: recompute pooled +/-10d quantiles at sampled points from
#    the RAW annual zarrs (independent pandas dayofyear mapping, NOT the
#    builder's datetime64 arithmetic) -> must match the product.
# 3. Physical sanity: SH/NH seasonal opposition, Kelvin magnitudes, diurnal
#    hour anchors, leap-day (doy 366) continuity.
# 4. EWB shipped climatology spot check at the same points (q0.15/0.50/0.85).
# Output: data/results/review/sr01_clim_validate.json (+csv sidecars)
import json
import pathlib

import numpy as np
import pandas as pd
import xarray as xr

ROOT = pathlib.Path("/opt/ewb-owb/data/clim2")
CLIM = pathlib.Path("/opt/ewb-live/data/clim/t2m_owb_1990_2019.zarr")
OUT = pathlib.Path("/opt/ewb-live/data/results/review")
OUT.mkdir(parents=True, exist_ok=True)
YEARS = list(range(1990, 2020))
Q = [0.05, 0.15, 0.85, 0.95]
HALF = 10

res: dict = {"generated": pd.Timestamp.utcnow().isoformat()}

ds = xr.open_zarr(CLIM)
da = ds["2m_temperature"]
res["structure"] = {
    "dims": {str(k): int(v) for k, v in da.sizes.items()},
    "quantiles": [float(q) for q in ds["quantile"].values],
    "lat_first_last": [float(da.latitude.values[0]), float(da.latitude.values[-1])],
    "lon_first_last": [float(da.longitude.values[0]), float(da.longitude.values[-1])],
    "dtype": str(da.dtype),
    "done_marker": (CLIM / ".done").exists(),
}
coarse = da.isel(dayofyear=slice(None, None, 37), hour=0,
                 latitude=slice(None, None, 41), longitude=slice(None, None, 41))
res["structure"]["nan_count_coarse"] = int(np.isnan(coarse.values).sum())
raw0 = xr.open_zarr(ROOT / "t2m_6h_1990.zarr")["2m_temperature"]
res["raw_store"] = {"dims": dict(raw0.sizes), "chunks": raw0.encoding.get("chunks"),
                    "lat_first_last": [float(raw0.latitude.values[0]), float(raw0.latitude.values[-1])],
                    "time0_last": str(raw0.time.values[0]) + " " + str(raw0.time.values[-1])}

# ---------- sample points ----------
pts = [
    ("heat_afar", 12.0, 32.0), ("heat_sahel_w", 14.0, 0.0),
    ("heat_amazon_n", 1.0, -60.0), ("heat_us_splains", 35.0, -95.0),
    ("heat_sahara", 22.0, 12.0), ("freeze_greenland", 76.0, -45.0),
    ("freeze_greenland_n", 81.0, -40.0), ("sh_midlat", -45.0, 170.0),
    ("nh_siberia", 65.0, 100.0), ("oce_pacific", 0.0, -150.0),
    ("oce_southern", -60.0, 30.0), ("nz", -42.0, 173.0),
    ("patagonia", -45.0, -70.0), ("antarctic_edge", -69.0, 0.0),
    ("arctic_ocean", 85.0, 0.0), ("india", 25.0, 78.0),
    ("amazon_s", -10.0, -55.0), ("europe", 48.0, 10.0),
    ("australia", -25.0, 135.0),
]
rng = np.random.default_rng(7)
for k in range(40):
    pts.append((f"rand{k}", float(rng.uniform(-66, 88)), float(rng.uniform(0, 360))))

lats = da.latitude.values
lons = da.longitude.values
ilats = [int(np.argmin(np.abs(lats - la))) for _, la, _ in pts]
ilons = [int(np.argmin(np.abs(lons - (lo % 360.0)))) for _, _, lo in pts]
DOY_TARGETS = [15, 60, 100, 150, 172, 200, 240, 262, 275, 290, 305, 330, 366]
HOURS = [0, 6, 12, 18]

# ---------- read point time series from every raw year (one year in RAM) ----------
series = [[] for _ in pts]  # per point: list of (doy, hour, values) per year
metas = []
for y in YEARS:
    yr = xr.open_zarr(ROOT / f"t2m_6h_{y}.zarr")["2m_temperature"]
    v = yr.values  # (time, lat, lon), time-first assumed
    t = pd.DatetimeIndex(yr.time.values)
    keep = np.isin(t.hour.to_numpy(), HOURS)
    doy = t.dayofyear.to_numpy()[keep]
    hour = t.hour.to_numpy()[keep]
    p = v[keep][:, ilats, ilons]  # (n_steps, n_pts) paired fancy-index
    for i in range(len(pts)):
        series[i].append((doy, hour, p[:, i]))
    metas.append((y, v.shape, v.nbytes))
    del v, yr, p
res["raw_years"] = [{"year": m[0], "shape": list(m[1])} for m in metas[:2]]

# ---------- product values at sample points (slice-load once) ----------
prod_sel = da.sel(dayofyear=DOY_TARGETS, hour=HOURS)
prod_vals = {}
qv = [float(q) for q in ds["quantile"].values]
for qi, q in enumerate(Q):
    if q not in qv:
        continue
    blk = prod_sel.isel(quantile=qv.index(q)).values  # (doy, hour, lat, lon)
    prod_vals[q] = blk[:, :, ilats, ilons]  # (ndoy, nhour, npts)

rows = []
for i, (name, la, lo) in enumerate(pts):
    for di_, D in enumerate(DOY_TARGETS):
        for hi_, H in enumerate(HOURS):
            pool_by_q = None
            pool = []
            for (doy, hour, v) in series[i]:
                dist = np.abs(doy - D)
                dist = np.minimum(dist, 366 - dist)
                pool.append(v[(dist <= HALF) & (hour == H)])
            pool = np.concatenate(pool)
            if len(pool) < 30:
                rows.append({"pt": name, "doy": D, "hour": H, "q": None,
                             "mine": None, "prod": None, "n_pool": len(pool)})
                continue
            mine = np.quantile(pool, Q)
            for q, mv in zip(Q, mine):
                pv = float(prod_vals[q][di_, hi_, i]) if q in prod_vals else None
                rows.append({"pt": name, "doy": D, "hour": H, "q": q,
                             "mine": float(mv), "prod": pv,
                             "diff": (float(mv - pv) if pv is not None else None),
                             "n_pool": len(pool)})
df = pd.DataFrame(rows).dropna(subset=["diff"])
repro = {}
for q, gg in df.groupby("q"):
    d_ = gg["diff"].to_numpy()
    repro[str(q)] = {"n": int(len(d_)), "mean_K": float(d_.mean()),
                     "std_K": float(d_.std()), "max_abs_K": float(np.abs(d_).max()),
                     "p999_abs_K": float(np.quantile(np.abs(d_), 0.999))}
res["reproduction"] = repro
res["reproduction_worst"] = df.reindex(df["diff"].abs().sort_values(ascending=False).index)[:8].to_dict("records")
df.to_csv(OUT / "sr01_reproduction_rows.csv", index=False)

# ---------- physical sanity ----------
lats_f, lons_f = lats.astype(float), lons.astype(float)
phys = {
    "p95_jan_60N_10E_K": float(da.sel(quantile=0.95, dayofyear=15, hour=12,
        latitude=float(lats[int(np.argmin(abs(lats_f-60)))]), longitude=float(lons[int(np.argmin(abs(lons_f-10)))])).values),
    "p95_jan_60S_170E_K": float(da.sel(quantile=0.95, dayofyear=15, hour=12,
        latitude=float(lats[int(np.argmin(abs(lats_f+60)))]), longitude=float(lons[int(np.argmin(abs(lons_f-170)))])).values),
    "p05_lateOct_greenland80N_K": float(da.sel(quantile=0.05, dayofyear=305, hour=0,
        latitude=float(lats[int(np.argmin(abs(lats_f-80)))]), longitude=float(lons[int(np.argmin(abs(lons_f-315)))])).values),
    "p95_aug_ethiopia12N_K": float(da.sel(quantile=0.95, dayofyear=240, hour=12,
        latitude=float(lats[int(np.argmin(abs(lats_f-12)))]), longitude=float(lons[int(np.argmin(abs(lons_f-32)))])).values),
}
res["physical"] = phys

# leap-day continuity at 48N,10E hour 12: doy 356..366,1..12
seq = []
for d in list(range(356, 367)) + list(range(1, 13)):
    v_ = float(da.sel(quantile=0.95, dayofyear=d, hour=12,
        latitude=float(lats[int(np.argmin(abs(lats_f-48)))]), longitude=float(lons[int(np.argmin(abs(lons_f-10)))])).values)
    seq.append([d, round(v_, 2)])
res["leap_doy_seq_p95_K"] = seq

# diurnal anchors, doy 240 (late Aug): p95 by hour, Afar + Amazon + Sahara
di = {}
for label, (la, lo) in {"afar": (12.0, 32.0), "amazon_n": (1.0, -60.0), "sahara": (22.0, 12.0)}.items():
    il = int(np.argmin(abs(lats_f - la))); io = int(np.argmin(abs(lons_f - lo % 360)))
    di[label] = {H: float(da.sel(quantile=0.95, dayofyear=240, hour=H,
        latitude=float(lats[il]), longitude=float(lons[io])).values) for H in HOURS}
res["diurnal_p95_by_hour_K"] = di

# ---------- EWB shipped climatology spot check ----------
try:
    from extremeweatherbench import defaults as ewb_defaults
    e = {q: ewb_defaults.get_climatology(q).sortby("latitude")
         .sel(latitude=slice(-90, 90)) for q in (0.15, 0.50, 0.85)}
    spot = []
    rng2 = np.random.default_rng(3)
    for k in range(120):
        i = int(rng2.integers(0, len(pts)))
        il, io = ilats[i], ilons[i]
        D = int(rng2.choice(DOY_TARGETS)); H = int(rng2.choice(HOURS))
        la, lo = float(lats[il]), float(lons[io])
        row = {"doy": D, "hour": H, "lat": la, "lon": lo}
        row["owb15"] = float(prod_vals[0.15][DOY_TARGETS.index(D), HOURS.index(H), i])
        row["owb85"] = float(prod_vals[0.85][DOY_TARGETS.index(D), HOURS.index(H), i])
        for q, key in ((0.15, "ewb15"), (0.50, "ewb50"), (0.85, "ewb85")):
            row[key] = float(e[q].sel(dayofyear=D, hour=H, latitude=la, longitude=lo,
                                      method="nearest").values)
        spot.append(row)
    sp = pd.DataFrame(spot)
    d15 = sp.owb15 - sp.ewb15
    d85 = sp.owb85 - sp.ewb85
    res["ewb_shipped_spot"] = {
        "n": len(sp),
        "q0.15_owb_minus_ewb": {"mean_K": round(float(d15.mean()), 2),
                                "p95_abs_K": round(float(np.quantile(d15.abs(), 0.95)), 2)},
        "q0.85_owb_minus_ewb": {"mean_K": round(float(d85.mean()), 2),
                                "p95_abs_K": round(float(np.quantile(d85.abs(), 0.95)), 2)},
        "sandwich_frac_owb15_lt_ewb50_lt_owb85": round(float(((sp.owb15 < sp.ewb50) & (sp.ewb50 < sp.owb85)).mean()), 3),
    }
    sp.to_csv(OUT / "sr01_ewb_spot.csv", index=False)
except Exception as exc:  # noqa: BLE001
    res["ewb_shipped_spot"] = {"error": repr(exc)}

with open(OUT / "sr01_clim_validate.json", "w") as f:
    json.dump(res, f, indent=1)
print("WROTE", OUT / "sr01_clim_validate.json")
print(json.dumps({k: res.get(k) for k in ("structure", "raw_store", "reproduction",
      "reproduction_worst", "physical", "diurnal_p95_by_hour", "ewb_shipped_spot")}, indent=1))
