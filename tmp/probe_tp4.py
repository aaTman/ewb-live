"""Decide the daily-precip recipe in the ARCO stores (run on ewb).

Per-block identity between hourly `total_precipitation` increments and the
6h store's `total_precipitation_6hr` / `_24hr`, on one UTC day, plus a small
box dump for eyeballing.
"""
from __future__ import annotations

import numpy as np
import xarray as xr

H = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
S = "gs://gcp-public-data-arco-era5/ar/1959-2022-6h-1440x721.zarr"
so = {"token": "anon"}

hour = xr.open_zarr(H, storage_options=so, chunks=None)["total_precipitation"]
ds6 = xr.open_zarr(S, storage_options=so, chunks=None)

day = np.datetime64("1990-01-31")
h = (hour.sel(time=slice(day, day + np.timedelta64(1, "D"))).load().values
     * 1000.0)  # index 0 = 00Z Jan31 ... 24 = 00Z Feb1
blocks = {"00-06": (1, 6), "06-12": (7, 12), "12-18": (13, 18),
          "18-24": (19, 24)}
for name, (a, b) in blocks.items():
    t = f"1990-01-31T{b:02d}:00" if b != 24 else "1990-02-01T00:00"
    v6 = ds6["total_precipitation_6hr"].sel(time=np.datetime64(t)).load().values * 1000.0
    ref = h[a:b + 1].sum(axis=0)
    print(f"BLOCK {name}: 6hr@{t} vs hourly[{a}:{b}] "
          f"maxdiff={np.nanmax(np.abs(v6 - ref)):.4f} mm "
          f"mean6={v6.mean():.3f} meanH={ref.mean():.3f} "
          f"n(>0.01mm diff)={int((np.abs(v6-ref) > 0.01).sum())}")

A = h[1:].sum(axis=0)                       # daily from 24 hourly increments
B = sum(ds6["total_precipitation_6hr"].sel(time=np.datetime64(t)).load().values
        for t in ["1990-01-31T06:00", "1990-01-31T12:00", "1990-01-31T18:00",
                  "1990-02-01T00:00"]) * 1000.0
C = ds6["total_precipitation_24hr"].sel(time=np.datetime64("1990-02-01T00:00")).load().values * 1000.0
for nm, X in [("A hourly24", A), ("B 4x6hr", B), ("C 24hr var", C)]:
    print(f"{nm}: mean={X.mean():.3f} mm p50={np.nanmedian(X):.3f} "
          f"p99={np.nanquantile(X,0.99):.2f} max={np.nanmax(X):.1f} "
          f"frac>=1mm={(X>=1).mean():.4f} frac>0={(X>0).mean():.4f} "
          f"nan={int(np.isnan(X).sum())}")
print("DIFF A-B: max", float(np.nanmax(np.abs(A - B))),
      "n>0.01mm", int((np.abs(A - B) > 0.01).sum()))
print("DIFF A-C: max", float(np.nanmax(np.abs(A - C))),
      "n>0.01mm", int((np.abs(A - C) > 0.01).sum()))
print("DIFF B-C: max", float(np.nanmax(np.abs(B - C))),
      "n>0.01mm", int((np.abs(B - C) > 0.01).sum()))

# where do they differ? pick the cell with the largest |A-B|
j = np.unravel_index(np.nanargmax(np.abs(A - B)), A.shape)
box = (slice(max(j[0] - 2, 0), j[0] + 3), slice(max(j[1] - 2, 0), j[1] + 3))
print("CELL", j, "lat", float(hour.latitude[j[0]]), "lon", float(hour.longitude[j[1]]))
print("A box\n", np.round(A[box], 2))
print("B box\n", np.round(B[box], 2))
print("C box\n", np.round(C[box], 2))
print("hourly tp column at that cell (mm, 00Z Jan31..00Z Feb1):")
print(np.round(h[:, j], 3))
print("6hr values at that cell (06,12,18 Jan31; 00 Feb1):",
      [round(float(ds6['total_precipitation_6hr'].sel(
          time=np.datetime64(t), latitude=hour.latitude[j[0]],
          longitude=hour.longitude[j[1]]).values * 1000), 3)
       for t in ["1990-01-31T06:00", "1990-01-31T12:00", "1990-01-31T18:00",
                 "1990-02-01T00:00"]])
print("24hr at that cell at 00,06,12,18 Feb1:",
      [round(float(ds6['total_precipitation_24hr'].sel(
          time=np.datetime64(f"1990-02-01T{hh:02d}:00"), latitude=hour.latitude[j[0]],
          longitude=hour.longitude[j[1]]).values * 1000), 3) for hh in (0, 6, 12, 18)])
