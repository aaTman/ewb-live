"""Verify the ARCO 6h store's pre-computed accumulation variables (run on ewb).

Checks that `total_precipitation_24hr` at 00Z == the UTC-day total built from
the hourly `total_precipitation` increments in the EWB store, that _6hr is the
6-hour accumulation, and measures object size / throughput for the daily read.
"""
from __future__ import annotations

import time

import numpy as np
import xarray as xr

H = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
S = "gs://gcp-public-data-arco-era5/ar/1959-2022-6h-1440x721.zarr"
so = {"token": "anon"}

ds6 = xr.open_zarr(S, storage_options=so, chunks=None)
print("STORE vars with precip:", [v for v in ds6.data_vars if "precip" in v])
print("time", str(ds6.time.values[0]), str(ds6.time.values[-1]),
      "n", ds6.sizes["time"], "lat", ds6.latitude.values[:3],
      "lon", ds6.longitude.values[:3])

d24 = ds6["total_precipitation_24hr"]
d6 = ds6["total_precipitation_6hr"]
print("chunks 24hr:", d24.encoding.get("preferred_chunks"),
      "dtype", d24.dtype, "attrs", d24.attrs)
print("6hr sample times:", [str(t) for t in d6.sel(
    time=slice("1990-01-31", "1990-02-01")).time.values])
print("24hr sample times:", [str(t) for t in d24.sel(
    time=slice("1990-01-31", "1990-02-01")).time.values])

hour = xr.open_zarr(H, storage_options=so, chunks=None)["total_precipitation"]

# --- semantics: 24hr at 00Z(D+1) == sum of hourly increments over day D ---
hh = hour.sel(time=slice("1990-01-31", "1990-02-01")).load().values * 1000.0
daily_hourly = hh[1:].sum(axis=0)  # 01Z..24Z of Jan 31
for t in ["1990-01-31T00:00", "1990-02-01T00:00", "1990-02-01T06:00"]:
    v = d24.sel(time=np.datetime64(t)).load().values * 1000.0
    print(f"24hr @{t}: max|v-daily(Jan31)|={np.nanmax(np.abs(v-daily_hourly)):.3f} mm "
          f"mean={v.mean()*1:.3f} max={v.max():.1f}")
# 6hr accumulation
s6 = d6.sel(time=np.datetime64("1990-02-01T06:00")).load().values * 1000.0
ref = hh[[19, 20, 21, 22, 23, 24]].sum(axis=0) + (hh[18] * 0)
print("6hr@Feb1 06Z vs hourly 01..06Z of Feb1:",
      "max diff", float(np.nanmax(np.abs(s6 - (hour.sel(time=slice('1990-02-01T01:00', '1990-02-01T06:00')).load().values * 1000).sum(0)))), "mm")
s12 = d6.sel(time=np.datetime64("1990-02-01T12:00")).load().values * 1000.0
print("6hr@Feb1 12Z vs hourly 07..12Z:", "max diff", float(np.nanmax(np.abs(
    s12 - (hour.sel(time=slice('1990-02-01T07:00', '1990-02-01T12:00')).load().values * 1000).sum(0)))), "mm")
print("sum(4 x 6hr) Jan31 vs daily:", float(np.nanmax(np.abs(
    sum(d6.sel(time=np.datetime64(f"1990-02-01T{h:02d}:00")).load().values if h == 0
        else d6.sel(time=np.datetime64(f"1990-01-31T{h:02d}:00")).load().values
        for h in (0, 6, 12, 18)) * 1000.0 - daily_hourly))), "mm")

# --- throughput: 48 daily 24hr fields, serial then threaded ---------------
times = [np.datetime64("1995-06-01T00:00") + np.timedelta64(i, "D") for i in range(48)]
t0 = time.time()
_ = [np.asarray(d24.sel(time=t).values, dtype="float32") for t in times[:8]]
ser = time.time() - t0
print(f"serial 8 fields: {ser:.1f}s -> {8*721*1440*4/ser/1e6:.0f} MB/s uncompressed")

from concurrent.futures import ThreadPoolExecutor
_tls = __import__("threading").local()


def _read(t):
    if not hasattr(_tls, "d"):
        _tls.d = xr.open_zarr(S, storage_options=so, chunks=None)[
            "total_precipitation_24hr"]
    return np.asarray(_tls.d.sel(time=t).values, dtype="float32")


for nthreads in (8, 16, 24):
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=nthreads) as ex:
        out = list(ex.map(_read, times))
    dt_ = time.time() - t0
    print(f"{nthreads} threads, 48 fields: {dt_:.1f}s -> "
          f"{48*721*1440*4/dt_/1e6:.0f} MB/s uncompressed "
          f"({48*2.0/dt_:.0f} MB/s wire est); "
          f"30-year daily climatology ETA ~{10958/48*dt_/60:.0f} min")
print("sanity: mean of 48 daily fields (mm/day)", float(np.mean(out)))
