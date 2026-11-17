"""Standalone completeness/sanity check of a precip_clim store (no ewb_live import).

SUPERSEDED by `python -m ewb_live.climatology.precip_clim status|finalize` for
normal use; keep it as the probe that works when the module is not deployed on
the box (it reads the store with raw zarr only).

Read-only. Runs on the ewb LXC with the box venv:
    ssh root@ewb ". /opt/ewb-live/.venv/bin/activate && python -" < tmp/verify_store.py
"""
import json
import pathlib
import sys

import numpy as np
import zarr

STORE = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else
                     "/opt/ewb-live/data/clim/precip_era5_pooled_1990_2019.zarr")
root = zarr.open_group(str(STORE), mode="r")
print("store:", STORE, "done marker:", (STORE / ".done").exists())
print("root attrs:", json.dumps({k: v for k, v in root.attrs.items()
                                 if k in ("years", "quantiles", "accums",
                                          "pool_half_window_days",
                                          "wet_day_min_mm", "min_wet_samples",
                                          "tile", "grid", "dtype")}, indent=1))

for name in ("total_precipitation", "wet_sample_count"):
    a = root[name]
    print(f"\n== {name}: shape={a.shape} chunks={a.chunks} dtype={a.dtype} "
          f"fill={a.fill_value!r}")
    # expected chunk keys
    nidx = [int(np.ceil(s / c)) for s, c in zip(a.shape, a.chunks)]
    total, missing = 0, []
    def walk(dim, prefix):
        global total
        if dim == len(nidx):
            total += 1
            if not (STORE / name / "c" / prefix).exists():
                missing.append(prefix)
            return
        for i in range(nidx[dim]):
            walk(dim + 1, f"{prefix}{i}" + ("/" if dim + 1 < len(nidx) else ""))
    walk(0, "")
    print(f"   chunks expected={total} missing={len(missing)}"
          + (f" e.g. {missing[:5]}" if missing else ""))

lat = np.asarray(root["latitude"][:], float)
lon = np.asarray(root["longitude"][:], float) % 360.0     # store is 0..359.75
REG = {"amazon": (-12, 3, -76, -49), "sahara_sahel": (11, 33, -12, 35),
       "monsoon_india": (6, 30, 65, 96)}
VAR, CNT = "total_precipitation", "wet_sample_count"
bad = 0
for d in (15, 200):
    for w in (1, 3):
        ai = 0 if w == 1 else 1        # accumulation coord is [1, 3]
        th95 = root[VAR][0, ai, d - 1]           # (lat, lon)
        th99 = root[VAR][1, ai, d - 1]
        cnt = root[CNT][ai, d - 1]
        for name, (la0, la1, lo0, lo1) in REG.items():
            iy = np.flatnonzero((lat <= la1) & (lat >= la0))
            a, b = lo0 % 360.0, lo1 % 360.0        # store lon is 0..359.75
            ix = np.flatnonzero((lon >= a) & (lon <= b) if a <= b
                                else (lon >= a) | (lon <= b))
            assert ix.size and iy.size, (name, ix.size, iy.size)
            v99 = np.asarray(th99)[np.ix_(iy, ix)]
            v95 = np.asarray(th95)[np.ix_(iy, ix)]
            c = np.asarray(cnt)[np.ix_(iy, ix)]
            diff = np.where(np.isnan(v95) | np.isnan(v99), -np.inf, v95 - v99)
            ok_mono = bool(diff.max() <= 1e-4)
            nan_frac = float(np.isnan(v99).mean())
            med = float(np.nanmedian(v99)) if nan_frac < 1 else float("nan")
            mx = float(v99.max()) if nan_frac < 1 else float("nan")
            ok_rel = bool(np.all(np.isnan(v99)[c < 30]))
            bad += (not ok_mono) + (not ok_rel)
            print(f"doy{d:3d} {w}d {name:14s} p99 mean={np.nanmean(v99):6.1f} "
                  f"med={med:6.1f} max={mx:7.1f} mm "
                  f"nan={nan_frac:.3f} wetn_med={np.median(c):4.0f} "
                  f"p99>=p95:{ok_mono} relmask:{ok_rel}")
print("\ninvariants violated:", bad)
