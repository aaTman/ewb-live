"""Probe the ARCO ERA5 total_precipitation source (run on ewb).

Measures: object sizes, download throughput, the 6-hourly-accumulation
identity (4 x 6-hourly tp == daily total == sum of 24 hourly tp), and the
fraction of the globe that is wet (for the wet-day pool).
"""
from __future__ import annotations

import io
import time

import gcsfs
import numpy as np
import xarray as xr

import extremeweatherbench.inputs as I

URI = I.ARCO_ERA5_FULL_URI
VAR = "total_precipitation"

fs = gcsfs.GCSFileSystem(token="anon")
prefix = URI.replace("gs://", "") + f"/{VAR}"

ds = xr.open_zarr(URI, storage_options={"token": "anon"}, chunks=None)
da = ds[VAR]
tv = ds.time.values
idx = [int((np.datetime64(f"1990-01-31T{hh:02d}:00") - tv[0]) / np.timedelta64(1, "h"))
       for hh in (6, 12, 18)] + [int((np.datetime64("1990-02-01T00:00") - tv[0])
                                   / np.timedelta64(1, "h"))]

# --- 1. object sizes ------------------------------------------------------
sizes = []
for i in idx[:4]:
    p = f"{prefix}/{i}.0.0"
    sizes.append((p.rsplit("/", 1)[1], fs.info(p)["size"]))
for name, n in sizes:
    print(f"object {name}: {n/1e6:.2f} MB compressed "
          f"({n / (721 * 1440 * 4) * 100:.0f}% of raw float32)")
avg = np.mean([n for _, n in sizes])
print(f"mean {avg/1e6:.2f} MB/step -> 6-hourly climatology transfer "
      f"~{avg * 4 * 365 * 30 / 1e9:.0f} GB, hourly "
      f"~{avg * 24 * 365 * 30 / 1e9:.0f} GB")

# --- 2. throughput --------------------------------------------------------
t0 = time.time()
fields = [np.asarray(da.isel(time=i).values, dtype="float32") for i in idx]
dt_ = time.time() - t0
print(f"read {len(idx)} global fields in {dt_:.1f}s -> "
      f"{len(idx) * 721 * 1440 * 4 / dt_ / 1e6:.0f} MB/s uncompressed, "
      f"{len(idx) * avg / dt_ / 1e6:.0f} MB/s on the wire")

# --- 3. time coord / index mapping ---------------------------------------
print("time[0]", tv[0], "time[idx]", [str(tv[i]) for i in idx])

# --- 4. accumulation identity --------------------------------------------
# daily total for 1990-01-31 = tp(06)+tp(12)+tp(18)+tp(next 00)
d0 = np.datetime64("1990-01-31")
hourly = da.sel(time=slice(d0, d0 + np.timedelta64(1, "D")))
h = np.asarray(hourly.values, dtype="float32")
print("hourly steps returned:", h.shape, [str(t) for t in hourly.time.values[:3]],
      "...", str(hourly.time.values[-1]))
sum24_hourly = h[1:].sum(axis=0)          # 00Z of D is the 18->24 increment
six = np.asarray(da.sel(time=[np.datetime64(f"1990-01-31T{hh:02d}:00")
                              for hh in (6, 12, 18)]
                             + [np.datetime64("1990-02-01T00:00")],
                             ).values, dtype="float32").sum(axis=0)
print("max |daily(4x6h) - daily(24x1h)| =",
      float(np.nanmax(np.abs(six - sum24_hourly))), "m")
print("as mm: mean daily", float(np.nanmean(six * 1000)),
      "max", float(np.nanmax(six * 1000)))

# --- 5. wet fraction / spatial stats on one day --------------------------
day = six * 1000.0  # mm
print("wet (>=0.1mm) fraction of globe:", float((day >= 0.1).mean()),
      "wet (>=1mm):", float((day >= 1.0).mean()))

# --- 6. does the store hold what a full-year read costs? -----------------
one_year = da.sel(time=slice("1990-01-01", "1990-12-31T23:00"))
t0 = time.time()
_ = np.asarray(one_year.isel(time=slice(0, 24 * 5)).values, dtype="float32")
print(f"5 days contiguous read: {time.time()-t0:.1f}s")

# --- 7. chunk layout of the non-chunk-1 store (if it exists) -------------
for alt in ["gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg.zarr",
            "gs://gcp-public-data-arco-era5/co/1940-2100-1h-1deg.zarr",
            "gs://gcp-public-data-arco-era5/ar/1979-2022-1h-0p25deg.zarr"]:
    try:
        d2 = xr.open_zarr(alt, storage_options={"token": "anon"}, chunks=None)
        v2 = d2[VAR] if VAR in d2 else list(d2.data_vars)[0]
        print("ALT", alt, "sizes", dict(d2.sizes), "chunks",
              v2.encoding.get("chunks") or v2.encoding.get("preferred_chunks"),
              "time", str(d2.time.values[0]), str(d2.time.values[-1]))
        d2.close()
    except Exception as exc:  # noqa: BLE001
        print("ALT", alt, "->", type(exc).__name__, str(exc)[:100])
